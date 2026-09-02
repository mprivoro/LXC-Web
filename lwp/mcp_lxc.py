# MCP tools for classic LXC (lxc-*). Registered on the shared NoDeck MCP server.

from __future__ import annotations

import json
import os
import re

import lxclite as lxc
import lwp
from lwp.util import (
    RE_CPUS, RE_CT_NAME, RE_FLAGS, RE_HOSTNAME, RE_HWADDR, RE_IFACE,
    RE_ROOTFS, RE_SHARES, matches)
from lwp.mcp_server import (
    _DEST, _RO, _RW, _fail, _ok, _write_guard, ensure_runtime, mcp)

_LXC_KEY = re.compile(r'^lxc\.[a-zA-Z0-9._-]+$')

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
