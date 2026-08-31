# MCP server for LXC-Web: list/inspect CTs, read/write config, start/stop/…
# Classic LXC only (lxc-*), not LXD/Incus.
#
# Started in a background thread by python3 lwp.py (Streamable HTTP).
# Standalone: python3 -m lwp.mcp_server [--stdio] [--host …] [--port …]
# Needs the `mcp` package (Python 3.10+). The panel runs without it.

from __future__ import annotations

import argparse
import contextvars
import functools
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from urllib.parse import urlparse

import lxclite as lxc
import lwp
import lwp.ctlog as ctlog
from lwp.util import (
    RE_CPUS, RE_CT_NAME, RE_FLAGS, RE_HOSTNAME, RE_HWADDR, RE_IFACE,
    RE_ROOTFS, RE_SHARES, matches)

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

log = logging.getLogger('lwp.mcp')

_RO = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                      open_world_hint=False)
_RW = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                      open_world_hint=False)
_DEST = ToolAnnotations(read_only_hint=False, destructive_hint=True,
                        open_world_hint=False)

_LXC_KEY = re.compile(r'^lxc\.[a-zA-Z0-9._-]+$')
_READY = False
_OVERVIEW_PARTITION = '/'
_DATABASE = ''
_MCP_CONFIG_KEY = ''
_identity = contextvars.ContextVar('lwp_mcp_identity', default=None)
DEFAULT_MCP_URL = 'http://127.0.0.1:5001/mcp'

# Friendly field -> first LXC key that push_config_value understands.
_FIELDS = {
    'type': 'lxc.network.type',
    'link': 'lxc.network.link',
    'flags': 'lxc.network.flags',
    'hwaddr': 'lxc.network.hwaddr',
    'rootfs': 'lxc.rootfs',
    'hostname': 'lxc.utsname',
    'utsname': 'lxc.utsname',
    'arch': 'lxc.arch',
    'ipv4': 'lxc.network.ipv4',
    'ipv6': 'lxc.network.ipv6',
    'memlimit': 'lxc.cgroup.memory.limit_in_bytes',
    'swlimit': 'lxc.cgroup.memory.memsw.limit_in_bytes',
    'cpus': 'lxc.cgroup.cpuset.cpus',
    'shares': 'lxc.cgroup.cpu.shares',
}


def _panel_version():
    try:
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'version')
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ''


mcp = MCPServer(
    'LXC-Web',
    instructions=(
        'Classic LXC (lxc-* tools) on this host, not LXD or Incus. '
        'Use list_containers then container_info. '
        'CPU % is of one core (100% = one full core); the figure in '
        'parentheses is the share of all host CPUs. '
        'Destroy requires confirm_name equal to the container name. '
        'Live snapshot of a running CT needs allow_running=true.'
    ),
    version=_panel_version(),
    log_level='WARNING',
)


def parse_mcp_url(url):
    '''Split [mcp] url into host, port, path. Missing bits use the default.'''

    parsed = urlparse((url or '').strip() or DEFAULT_MCP_URL)
    host = parsed.hostname or '127.0.0.1'
    port = parsed.port or 5001
    path = parsed.path or '/mcp'
    if not path.startswith('/'):
        path = '/' + path
    scheme = parsed.scheme or 'http'
    return {
        'url': '%s://%s:%s%s' % (scheme, host, port, path),
        'host': host,
        'port': int(port),
        'path': path,
    }


def mcp_listen_spec(config):
    '''Listen spec from lwp.conf [mcp] url (legacy address/port if url is absent).'''

    url = ''
    try:
        url = config.get('mcp', 'url').strip()
    except (Exception):
        url = ''
    if not url:
        host, port = '127.0.0.1', 5001
        try:
            host = config.get('mcp', 'address').strip() or host
        except Exception:
            pass
        try:
            port = int(config.get('mcp', 'port'))
        except Exception:
            pass
        url = 'http://%s:%s/mcp' % (host, port)
    return parse_mcp_url(url)


def mcp_key_from_config(config):
    '''Read-only default token from [mcp] key.'''

    try:
        return config.get('mcp', 'key', raw=True).strip()
    except Exception:
        return ''


def ensure_runtime():
    '''Load lwp.conf and init LXC paths. Safe to call more than once.'''

    global _READY, _OVERVIEW_PARTITION, _DATABASE, _MCP_CONFIG_KEY
    if _READY:
        return
    try:
        import configparser
    except ImportError:
        import ConfigParser as configparser
    cfg = configparser.ConfigParser()
    path = os.path.join(os.getcwd(), 'lwp.conf')
    if not os.path.isfile(path):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'lwp.conf')
    with open(path) as fh:
        cfg.read_file(fh)
    try:
        ctlog.init(cfg.get('logging', 'file', raw=True).strip())
    except (configparser.NoSectionError, configparser.NoOptionError):
        ctlog.init('lwp-containers.log')
    try:
        ctlog.init_mcp(cfg.get('logging', 'mcp', raw=True).strip())
    except (configparser.NoSectionError, configparser.NoOptionError):
        ctlog.init_mcp('lwp-mcp.log')
    try:
        conf = cfg.get('lxc', 'conf').strip()
    except (configparser.NoSectionError, configparser.NoOptionError):
        conf = '/etc/lxc/lxc.conf'
    try:
        store = cfg.get('lxc', 'store').strip()
    except (configparser.NoSectionError, configparser.NoOptionError):
        store = '/var/lib/lxc'
    lxc.init_lxc_conf(conf, store)
    try:
        images = cfg.get('lxc', 'images').strip()
    except (configparser.NoSectionError, configparser.NoOptionError):
        images = '/var/cache/lxc/download'
    lwp.init_images_dir(images)
    try:
        _OVERVIEW_PARTITION = cfg.get('overview', 'partition')
    except (configparser.NoSectionError, configparser.NoOptionError):
        _OVERVIEW_PARTITION = '/'
    try:
        _DATABASE = cfg.get('database', 'file').strip() or 'lwp.db'
    except (configparser.NoSectionError, configparser.NoOptionError):
        _DATABASE = 'lwp.db'
    if _DATABASE and not os.path.isabs(_DATABASE):
        _DATABASE = os.path.join(os.getcwd(), _DATABASE)
    _MCP_CONFIG_KEY = mcp_key_from_config(cfg)
    try:
        from lwp.auth import ensure_users_schema
        conn = sqlite3.connect(_DATABASE)
        try:
            ensure_users_schema(conn)
        finally:
            conn.close()
    except Exception:
        log.exception('Could not migrate users.mcp_token')
    _READY = True


def _name(name):
    if not matches(RE_CT_NAME, name) or name == 'containers':
        raise ValueError('Invalid container name.')
    return name


def _need(name):
    name = _name(name)
    if not lxc.exists(name):
        raise lxc.ContainerDoesntExists(
            'Container %s does not exist.' % name)
    return name


def _fail(exc):
    out = getattr(exc, 'output', None)
    msg = lxc.lxc_error_message(out) if out else str(exc)
    return {'ok': False, 'error': msg or exc.__class__.__name__}


def _ok(**data):
    data['ok'] = True
    return data


def resolve_mcp_token(token):
    '''
    Map a presented token to an identity.
    Panel user (su) may write; [mcp] key in lwp.conf is read-only.
    '''

    if not token:
        return None
    path = _DATABASE
    if path and os.path.isfile(path):
        from lwp.auth import lookup_mcp_user
        conn = sqlite3.connect(path)
        try:
            row = lookup_mcp_user(conn, token)
        finally:
            conn.close()
        if row:
            return {
                'username': row['username'],
                'write': row['su'] == 'Yes',
                'source': 'user',
            }
    key = _MCP_CONFIG_KEY
    if key:
        try:
            same = secrets.compare_digest(token, key)
        except (TypeError, ValueError):
            same = False
        if same:
            return {'username': '', 'write': False, 'source': 'config'}
    return None


def _actor_from_ident(ident):
    '''(user, via) for logs. ident is None on stdio (still MCP).'''

    if not ident:
        return '-', 'MCP'
    if ident.get('source') == 'config':
        return '(config key)', 'MCP'
    return ident.get('username') or '-', 'MCP'


_REDACT_KEYS = ('token', 'password', 'secret', 'authorization', 'api_key',
                'apikey', 'mcp_token')
_MCP_JSON_LIMIT = 4000


def _sanitize_mcp(obj, depth=0):
    '''Drop secrets and clip long strings before writing the MCP log.'''

    if depth > 8:
        return '...'
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            low = str(key).lower()
            if any(part in low for part in _REDACT_KEYS):
                out[key] = '***'
            else:
                out[key] = _sanitize_mcp(value, depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [_sanitize_mcp(item, depth + 1) for item in obj[:50]]
    if isinstance(obj, str) and len(obj) > 500:
        return obj[:500] + '... (truncated)'
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _mcp_json(obj):
    try:
        text = json.dumps(_sanitize_mcp(obj), default=str, ensure_ascii=False)
    except Exception:
        text = str(obj)
    if len(text) > _MCP_JSON_LIMIT:
        return text[:_MCP_JSON_LIMIT] + '... (truncated)'
    return text


def _tool_payload(result):
    '''Pull structured tool output if the SDK wrapped it.'''

    if result is None:
        return None
    for attr in ('structured_content', 'structuredContent', 'data'):
        data = getattr(result, attr, None)
        if data is not None:
            return data
    content = getattr(result, 'content', None)
    if not content:
        return None
    texts = []
    for item in content:
        text = getattr(item, 'text', None)
        if text:
            texts.append(text)
    if not texts:
        return None
    blob = '\n'.join(texts)
    try:
        return json.loads(blob)
    except Exception:
        return blob


def _tool_ok(result):
    if result is None:
        return False
    if getattr(result, 'is_error', False):
        return False
    data = _tool_payload(result)
    if isinstance(data, dict) and data.get('ok') is False:
        return False
    return True


def log_mcp_request(kind, **fields):
    '''Write one MCP log block; uses the current actor if already set.'''

    rows = [('kind', kind)]
    for key in ('tool', 'uri', 'event', 'path', 'args', 'ok', 'error',
                'result', 'ms'):
        if key in fields:
            rows.append((key, fields[key]))
    ctlog.log_mcp(rows)


def _need_write():
    ident = _identity.get()
    if ident is None:
        return None
    if ident.get('write'):
        return None
    return {
        'ok': False,
        'error': 'Read-only MCP token. Use an admin user token to change containers.',
    }


def _write_guard(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        blocked = _need_write()
        if blocked:
            return blocked
        return fn(*args, **kwargs)
    return wrapped


def _settings_public(cfg):
    skip = {'config_error'}
    return {k: v for k, v in cfg.items() if k not in skip}


def _snapshot_public(item):
    return {
        'name': item.get('name', ''),
        'created': item.get('created', ''),
        'comment': item.get('comment', ''),
        'size_mb': item.get('size_mb') or 0,
        'path': item.get('path', ''),
    }


def _container_brief(name, state):
    row = {'name': name, 'state': state}
    try:
        cfg = lwp.get_container_settings(name)
    except Exception:
        cfg = lwp.empty_container_settings()
    row['hostname'] = cfg.get('utsname') or ''
    row['ipv4'] = cfg.get('ipv4_addrs') or []
    row['ipv6'] = cfg.get('ipv6_addrs') or []
    if state == 'RUNNING':
        try:
            addrs = lxc.ip_addresses(name, True)
            if addrs.get('ipv4') or addrs.get('ipv6'):
                row['ipv4'] = addrs.get('ipv4') or []
                row['ipv6'] = addrs.get('ipv6') or []
        except Exception:
            pass
    err = cfg.get('config_error') or ''
    if err:
        row['config_error'] = err
    return row


@mcp.tool(annotations=_RO)
def list_containers() -> dict:
    '''List all classic LXC containers grouped by state.'''

    ensure_runtime()
    try:
        grouped = lxc.listx()
    except Exception as e:
        return _fail(e)
    containers = []
    for state in ('RUNNING', 'FROZEN', 'STOPPED', 'BROKEN'):
        for name in grouped.get(state, []):
            try:
                containers.append(_container_brief(name, state))
            except Exception as e:
                containers.append({
                    'name': name, 'state': state, 'error': str(e)})
    return _ok(
        lxcpath=lxc.lxcpath(),
        counts={k.lower(): len(grouped.get(k, []))
                for k in ('RUNNING', 'FROZEN', 'STOPPED', 'BROKEN')},
        containers=containers)


@mcp.tool(annotations=_RO)
def container_info(name: str) -> dict:
    '''State, IPs, RAM, disk, CPU/net sample, settings, and snapshots.'''

    ensure_runtime()
    try:
        name = _need(name)
        inf = lxc.info(name)
        settings = lwp.get_container_settings(name)
        state = inf.get('state', '')
        live = lwp.empty_live_metrics()
        mem = disk = 0
        snaps = []
        addrs = {'ipv4': [], 'ipv6': []}
        if state != 'BROKEN':
            try:
                mem = lwp.memory_usage(
                    name, known_live=(state in ('RUNNING', 'FROZEN')))
            except Exception:
                mem = 0
            try:
                disk = lwp.container_disk_usage(name)
            except Exception:
                disk = 0
            try:
                snaps = [_snapshot_public(s) for s in lxc.snapshots(name)]
            except Exception:
                snaps = []
            if state in ('RUNNING', 'FROZEN'):
                try:
                    live = lwp.container_live_metrics(
                        name, inf.get('links') or [])
                except Exception:
                    live = lwp.empty_live_metrics()
                try:
                    addrs = lxc.ip_addresses(name, True)
                except Exception:
                    pass
        live_out = {
            'cpu': live.get('cpu_label') or None,
            'cpu_title': live.get('cpu_title') or '',
            'net_rx': live.get('net_rx_label') or None,
            'net_tx': live.get('net_tx_label') or None,
            'net_title': live.get('net_title') or '',
        }
        return _ok(
            name=name,
            state=state,
            pid=inf.get('pid') or '0',
            error=inf.get('error') or settings.get('config_error') or '',
            links=inf.get('links') or [],
            ipv4=addrs.get('ipv4') or settings.get('ipv4_addrs') or [],
            ipv6=addrs.get('ipv6') or settings.get('ipv6_addrs') or [],
            memory_mb=mem,
            disk_mb=disk,
            live=live_out,
            settings=_settings_public(settings),
            snapshots=snaps,
        )
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RO)
def host_info() -> dict:
    '''Host CPU, load, RAM, disk of the overview partition, uptime, LXC paths.'''

    ensure_runtime()
    try:
        cpu = lwp.host_cpu_usage()
        mem = lwp.host_memory_usage()
        disk = lwp.host_disk_usage(partition=_OVERVIEW_PARTITION)
        up = lwp.host_uptime()
        return _ok(
            dist=lwp.check_ubuntu(),
            version=lwp.check_version(),
            lxc_conf=lxc.lxc_conf_path(),
            lxcpath=lxc.lxcpath(),
            images=lwp.images_download_dir(),
            cpu=cpu,
            memory=mem,
            disk=disk,
            disk_partition=_OVERVIEW_PARTITION,
            uptime=up,
        )
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RO)
def read_config(name: str) -> dict:
    '''Raw LXC config file text for a container.'''

    ensure_runtime()
    try:
        name = _need(name)
        text, err = lwp.read_container_config(name)
        if text is None:
            return {'ok': False, 'error': err or 'unreadable', 'name': name}
        return _ok(name=name, path=lwp.container_config_path(name), text=text)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_DEST)
@_write_guard
def write_config(name: str, text: str) -> dict:
    '''Replace the container config (keeps config.bak). Does not restart the CT.'''

    ensure_runtime()
    try:
        name = _need(name)
        ok, err = lwp.write_container_config(name, text)
        if not ok:
            return {'ok': False, 'error': err, 'name': name}
        return _ok(name=name, path=lwp.container_config_path(name))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def restore_config_backup(name: str) -> dict:
    '''Restore config from config.bak.'''

    ensure_runtime()
    try:
        name = _need(name)
        ok, err = lwp.restore_container_config_backup(name)
        if not ok:
            return {'ok': False, 'error': err, 'name': name}
        return _ok(name=name)
    except Exception as e:
        return _fail(e)


def _set_autostart(name, enabled):
    lwp.push_config_value('lxc.start.auto', '1' if enabled else '0',
                          container=name)
    link = '/etc/lxc/auto/%s.conf' % name
    target = os.path.join(lxc.lxcpath(), name, 'config')
    if enabled:
        try:
            os.makedirs('/etc/lxc/auto', exist_ok=True)
            if not os.path.islink(link) and not os.path.exists(link):
                os.symlink(target, link)
        except OSError as e:
            return 'Wrote lxc.start.auto=1; auto symlink failed: %s' % e
    else:
        try:
            if os.path.islink(link) or os.path.isfile(link):
                os.remove(link)
        except OSError as e:
            return 'Wrote lxc.start.auto=0; removing auto symlink failed: %s' % e
    return ''


def _validate_field(field, value):
    if field in ('hostname', 'utsname'):
        if value and not matches(RE_HOSTNAME, value):
            return 'Invalid hostname.'
    elif field == 'flags':
        if value and not matches(RE_FLAGS, value):
            return 'flags must be up or down.'
    elif field == 'hwaddr':
        if value and not matches(RE_HWADDR, value):
            return 'Invalid MAC address.'
    elif field == 'link':
        if value and not matches(RE_IFACE, value):
            return 'Invalid interface / bridge name.'
    elif field == 'cpus':
        if value and not matches(RE_CPUS, value):
            return 'Invalid cpuset.'
    elif field == 'shares':
        if value and not matches(RE_SHARES, value):
            return 'CPU shares must be a number.'
    elif field == 'rootfs':
        if value and not matches(RE_ROOTFS, value):
            return 'Invalid rootfs path.'
    elif field in ('memlimit', 'swlimit'):
        if value and not re.match(r'^[0-9]+$', value):
            return 'Memory limit must be an integer (MB).'
    if value and ('\n' in value or '\r' in value or '\0' in value):
        return 'Value must be a single line.'
    return ''


@mcp.tool(annotations=_RW)
@_write_guard
def set_config(name: str, field: str, value: str) -> dict:
    '''
    Set one config field. field is a friendly name (hostname, ipv4, memlimit,
    cpus, shares, flags, link, hwaddr, autostart, …) or a raw lxc.* key.
    Empty value unsets the key. Does not restart the CT.
    '''

    ensure_runtime()
    try:
        name = _need(name)
        field = (field or '').strip()
        value = '' if value is None else str(value).strip()
        if field in ('autostart', 'auto', 'lxc.start.auto'):
            on = value.lower() in ('1', 'true', 'yes', 'on')
            note = _set_autostart(name, on)
            return _ok(name=name, field='autostart', value=on, warning=note or None)
        if field in _FIELDS:
            err = _validate_field(field, value)
            if err:
                return {'ok': False, 'error': err}
            key = _FIELDS[field]
        elif _LXC_KEY.match(field):
            key = field
        else:
            return {
                'ok': False,
                'error': 'Unknown field. Use hostname, ipv4, memlimit, '
                         'cpus, shares, flags, link, hwaddr, autostart, '
                         'or a raw lxc.* key.',
                'fields': sorted(set(_FIELDS) | {'autostart'}),
            }
        lwp.push_config_value(key, value, container=name)
        return _ok(name=name, field=field, key=key, value=value)
    except Exception as e:
        return _fail(e)


def _act(name, fn, extra=None):
    ensure_runtime()
    try:
        name = _need(name)
        fn(name)
        inf = lxc.info(name)
        out = _ok(name=name, state=inf.get('state'))
        if extra:
            out.update(extra)
        return out
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def start_container(name: str) -> dict:
    '''Start a stopped CT, or unfreeze a frozen one.'''

    ensure_runtime()
    try:
        name = _need(name)
        state = lxc.info(name).get('state')
        if state == 'FROZEN':
            lxc.unfreeze(name)
        elif state != 'RUNNING':
            lxc.start(name)
        return _ok(name=name, state=lxc.info(name).get('state'))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def stop_container(name: str) -> dict:
    '''Stop a running or frozen container (lxc-stop).'''

    return _act(name, lxc.stop)


@mcp.tool(annotations=_RW)
@_write_guard
def restart_container(name: str) -> dict:
    '''Stop then start. No-op start if it is already stopped (just starts).'''

    ensure_runtime()
    try:
        name = _need(name)
        state = lxc.info(name).get('state')
        if state in ('RUNNING', 'FROZEN'):
            lxc.stop(name)
        lxc.start(name)
        return _ok(name=name, state=lxc.info(name).get('state'))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def freeze_container(name: str) -> dict:
    '''Pause a running container (lxc-freeze).'''

    return _act(name, lxc.freeze)


@mcp.tool(annotations=_RW)
@_write_guard
def unfreeze_container(name: str) -> dict:
    '''Resume a frozen container (lxc-unfreeze).'''

    return _act(name, lxc.unfreeze)


@mcp.tool(annotations=_RO)
def list_snapshots(name: str) -> dict:
    '''Snapshots of a container, with size and comment.'''

    ensure_runtime()
    try:
        name = _need(name)
        plan = lxc.snapshot_plan(name)
        snaps = [_snapshot_public(s) for s in lxc.snapshots(name)]
        return _ok(name=name, snapshots=snaps, plan=plan)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def create_snapshot(name: str, comment: str = '',
                    allow_running: bool = False) -> dict:
    '''
    Create a snapshot. Stopped CTs: consistent. Running: only if
    allow_running is true (live copy).
    '''

    ensure_runtime()
    try:
        name = _need(name)
        snap = lxc.snapshot_create(
            name, comment or None, allow_running=allow_running)
        return _ok(name=name, snapshot=snap)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_DEST)
@_write_guard
def restore_snapshot(name: str, snapshot: str, new_name: str = '') -> dict:
    '''
    Restore a snapshot. Empty new_name restores in place (CT must be stopped).
    Otherwise creates a new container from the snapshot.
    '''

    ensure_runtime()
    try:
        name = _need(name)
        dest = new_name.strip() or None
        if dest:
            dest = _name(dest)
        lxc.snapshot_restore(name, snapshot, dest)
        return _ok(name=name, snapshot=snapshot, restored_as=dest or name)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_DEST)
@_write_guard
def destroy_snapshot(name: str, snapshot: str) -> dict:
    '''Delete one snapshot of a container.'''

    ensure_runtime()
    try:
        name = _need(name)
        lxc.snapshot_destroy(name, snapshot)
        return _ok(name=name, snapshot=snapshot)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def clone_container(name: str, new_name: str, snapshot: bool = False) -> dict:
    '''Clone a container to new_name. snapshot=true uses lxc-clone -s.'''

    ensure_runtime()
    try:
        name = _need(name)
        new_name = _name(new_name)
        lxc.clone(orig=name, new=new_name, snapshot=snapshot)
        return _ok(name=new_name, cloned_from=name, snapshot=snapshot)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def create_container(name: str, source: str) -> dict:
    '''
    Create a CT from a cached image or template.
    source is image:<dist>:<release>:<arch>:<variant> (from list_images)
    or template:<name>.
    '''

    ensure_runtime()
    try:
        name = _name(name)
        template, xargs = lwp.parse_create_source(source)
        if not template:
            return {
                'ok': False,
                'error': 'Unknown source. Use image:id from list_images '
                         'or template:<name>.',
            }
        lxc.create(name, template=template, xargs=xargs, env=lwp.create_env())
        return _ok(name=name, source=source, template=template)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_DEST)
@_write_guard
def destroy_container(name: str, confirm_name: str) -> dict:
    '''Destroy a container. confirm_name must be exactly the same as name.'''

    ensure_runtime()
    try:
        name = _need(name)
        if confirm_name != name:
            return {
                'ok': False,
                'error': 'confirm_name must match name exactly.',
            }
        lxc.destroy(name)
        lwp.forget_live_sample(name)
        return _ok(name=name, destroyed=True)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RO)
def list_images() -> dict:
    '''Cached lxc-download images and host templates for create_container.'''

    ensure_runtime()
    try:
        return _ok(
            images=lwp.get_cached_images(),
            images_dir=lwp.images_download_dir(),
            templates=lwp.get_templates_list(),
        )
    except Exception as e:
        return _fail(e)


@mcp.resource('lxc://containers', mime_type='application/json')
def resource_containers() -> str:
    '''JSON list of containers by state.'''

    return json.dumps(list_containers(), indent=2)


@mcp.resource('lxc://containers/{name}', mime_type='application/json')
def resource_container(name: str) -> str:
    '''JSON details for one container.'''

    return json.dumps(container_info(name), indent=2)


@mcp.resource('lxc://containers/{name}/config', mime_type='text/plain')
def resource_container_config(name: str) -> str:
    '''Raw config file.'''

    data = read_config(name)
    if not data.get('ok'):
        return data.get('error') or 'error'
    return data.get('text') or ''


def _header_token(headers):
    '''API key from Authorization, Bearer, or X-Api-Key.'''

    auth = bearer = api_key = ''
    for raw_name, raw_val in headers or []:
        name = raw_name.decode('latin1').lower()
        val = raw_val.decode('latin1').strip()
        if name == 'authorization':
            auth = val
        elif name == 'bearer':
            bearer = val
        elif name == 'x-api-key':
            api_key = val
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return (auth or bearer or api_key).strip()


def _install_mcp_logging():
    '''Log every tools/call and resource read (once).'''

    if getattr(mcp, '_lwp_logged', False):
        return
    orig_call = mcp.call_tool
    orig_read = mcp.read_resource

    async def logged_call_tool(name, arguments, context=None, **kwargs):
        ident = _identity.get()
        user, via = _actor_from_ident(ident)
        actor_tok = ctlog.set_actor(user, via)
        t0 = time.monotonic()
        error = None
        result = None
        try:
            result = await orig_call(name, arguments, context)
            return result
        except Exception as exc:
            error = exc
            raise
        finally:
            ms = int((time.monotonic() - t0) * 1000)
            payload = _tool_payload(result) if result is not None else None
            ok = False if error else _tool_ok(result)
            log_mcp_request(
                'tools/call',
                tool=name,
                args=_mcp_json(arguments or {}),
                ok=str(ok).lower(),
                error=str(error) if error else None,
                result=_mcp_json(payload) if payload is not None else None,
                ms=ms,
            )
            ctlog.reset_actor(actor_tok)

    async def logged_read_resource(uri, context=None, **kwargs):
        ident = _identity.get()
        user, via = _actor_from_ident(ident)
        actor_tok = ctlog.set_actor(user, via)
        t0 = time.monotonic()
        error = None
        try:
            return await orig_read(uri, context)
        except Exception as exc:
            error = exc
            raise
        finally:
            ms = int((time.monotonic() - t0) * 1000)
            log_mcp_request(
                'resources/read',
                uri=str(uri),
                ok=str(error is None).lower(),
                error=str(error) if error else None,
                ms=ms,
            )
            ctlog.reset_actor(actor_tok)

    mcp.call_tool = logged_call_tool
    mcp.read_resource = logged_read_resource
    mcp._lwp_logged = True


_install_mcp_logging()


class _ApiKeyASGI:
    '''Require a user MCP token or the read-only [mcp] key.'''

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            await self.app(scope, receive, send)
            return
        got = _header_token(scope.get('headers') or [])
        ident = resolve_mcp_token(got) if got else None
        if not ident:
            path = scope.get('path') or ''
            method = (scope.get('method') or '').upper()
            if method == 'POST' or scope['type'] == 'websocket':
                actor_tok = ctlog.set_actor('-', 'MCP')
                try:
                    log_mcp_request(
                        'http',
                        event='unauthorized',
                        path=path,
                        ok='false',
                        error='unauthorized',
                    )
                finally:
                    ctlog.reset_actor(actor_tok)
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 4401})
                return
            body = b'{"error":"unauthorized"}'
            await send({
                'type': 'http.response.start',
                'status': 401,
                'headers': [
                    (b'content-type', b'application/json'),
                    (b'www-authenticate', b'Bearer'),
                    (b'content-length', str(len(body)).encode('ascii')),
                ],
            })
            await send({'type': 'http.response.body', 'body': body})
            return
        user, via = _actor_from_ident(ident)
        id_tok = _identity.set(ident)
        actor_tok = ctlog.set_actor(user, via)
        try:
            await self.app(scope, receive, send)
        finally:
            ctlog.reset_actor(actor_tok)
            _identity.reset(id_tok)


def serve_http(url, threaded=False):
    '''Block serving Streamable HTTP with token auth.'''

    spec = parse_mcp_url(url)
    import anyio
    import uvicorn

    app = _ApiKeyASGI(
        mcp.streamable_http_app(
            streamable_http_path=spec['path'],
            stateless_http=True,
            host=spec['host'],
        ),
    )
    config = uvicorn.Config(
        app,
        host=spec['host'],
        port=spec['port'],
        log_level='warning',
    )
    server = uvicorn.Server(config)
    if threaded:
        server.install_signal_handlers = False
    log.warning('MCP listening at %s (user token or read-only config key)', spec['url'])
    anyio.run(server.serve)


def start_background(url):
    '''Daemon thread: Streamable HTTP MCP next to the Flask app.'''

    def _run():
        try:
            ensure_runtime()
            serve_http(url, threaded=True)
        except Exception:
            log.exception('MCP server failed')

    thread = threading.Thread(target=_run, daemon=True, name='lwp-mcp')
    thread.start()
    return thread


def main(argv=None):
    '''CLI: HTTP by default, or --stdio for a local spawned process.'''

    parser = argparse.ArgumentParser(description='LXC-Web MCP server')
    parser.add_argument('--stdio', action='store_true',
                        help='stdio transport (host spawns this process)')
    parser.add_argument('--url', default='',
                        help='listen URL (default: [mcp] url in lwp.conf)')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    ensure_runtime()
    if args.stdio:
        mcp.run(transport='stdio')
        return
    try:
        import configparser
    except ImportError:
        import ConfigParser as configparser
    cfg = configparser.ConfigParser()
    path = os.path.join(os.getcwd(), 'lwp.conf')
    if os.path.isfile(path):
        with open(path) as fh:
            cfg.read_file(fh)
    spec = parse_mcp_url(args.url) if args.url else mcp_listen_spec(cfg)
    print('MCP %s' % spec['url'], file=sys.stderr)
    serve_http(spec['url'])


if __name__ == '__main__':
    main()
