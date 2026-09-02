# MCP runtime for LXC-Web: auth, HTTP/stdio, host_info.
# LXC tools: lwp.mcp_lxc. VM tools: lwp.mcp_vm.
# Started in a background thread by python3 lwp.py (Streamable HTTP).
# Standalone: python3 -m lwp.mcp_server [--stdio] [--url …]
# Needs the `mcp` package (Python 3.10+). The panel runs without it.

from __future__ import annotations

import argparse
import contextvars
import functools
import json
import logging
import os
import secrets
import sqlite3
import sys
import threading
import time
from urllib.parse import urlparse

import lxclite as lxc
import lwp
import lwp.ctlog as ctlog

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

log = logging.getLogger('lwp.mcp')

_RO = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                      open_world_hint=False)
_RW = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                      open_world_hint=False)
_DEST = ToolAnnotations(read_only_hint=False, destructive_hint=True,
                        open_world_hint=False)

_READY = False
_OVERVIEW_PARTITION = '/'
_DATABASE = ''
_MCP_CONFIG_KEY = ''
_identity = contextvars.ContextVar('lwp_mcp_identity', default=None)
DEFAULT_MCP_URL = 'http://127.0.0.1:5001/mcp'


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
        'This host has classic LXC containers (lxc-*) and KVM/QEMU VMs '
        '(virsh / libvirt). Not LXD or Incus. '
        'Containers: list_containers, container_info. '
        'VMs: list_vms, vm_info. '
        'CPU % is of one core (100% = one full core); the figure in '
        'parentheses is the share of all host CPUs. '
        'Destroy/undefine requires confirm_name equal to the name. '
        'Live snapshot of a running CT/VM needs allow_running=true.'
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
        uri = cfg.get('vm', 'uri').strip()
    except (configparser.NoSectionError, configparser.NoOptionError):
        uri = 'qemu:///system'
    try:
        import libvirtlite as virt
        virt.init_uri(uri or 'qemu:///system')
        try:
            disk = cfg.get('vm', 'disk').strip()
        except (configparser.NoSectionError, configparser.NoOptionError):
            disk = ''
        virt.init_disk_dir(disk)
    except Exception:
        log.exception('Could not init libvirt URI')
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


def _fail(exc):
    '''Best-effort error dict from LXC or virsh CLI exceptions.'''

    out = getattr(exc, 'output', None)
    msg = str(exc)
    if out:
        module = getattr(type(exc), '__module__', '') or ''
        try:
            if module.startswith('libvirtlite'):
                import libvirtlite as virt
                msg = virt.virsh_error_message(out) or msg
            else:
                msg = lxc.lxc_error_message(out) or msg
        except Exception:
            pass
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
        'error': 'Read-only MCP token. Use an admin user token to change containers or VMs.',
    }


def _write_guard(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        blocked = _need_write()
        if blocked:
            return blocked
        return fn(*args, **kwargs)
    return wrapped


@mcp.tool(annotations=_RO)
def host_info() -> dict:
    '''Host CPU, load, RAM, disk of the overview partition, uptime, LXC paths.'''

    ensure_runtime()
    try:
        cpu = lwp.host_cpu_usage()
        mem = lwp.host_memory_usage()
        disk = lwp.host_disk_usage(partition=_OVERVIEW_PARTITION)
        up = lwp.host_uptime()
        uri = ''
        try:
            import libvirtlite as virt
            uri = virt.connect_uri()
        except Exception:
            uri = ''
        return _ok(
            dist=lwp.check_ubuntu(),
            version=lwp.check_version(),
            lxc_conf=lxc.lxc_conf_path(),
            lxcpath=lxc.lxcpath(),
            images=lwp.images_download_dir(),
            libvirt_uri=uri,
            cpu=cpu,
            memory=mem,
            disk=disk,
            disk_partition=_OVERVIEW_PARTITION,
            uptime=up,
        )
    except Exception as e:
        return _fail(e)


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


def _install_mcp_logging(server=None):
    '''Log every tools/call and resource read (once per server).'''

    target = server or mcp
    if getattr(target, '_lwp_logged', False):
        return
    orig_call = target.call_tool
    orig_read = target.read_resource

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

    target.call_tool = logged_call_tool
    target.read_resource = logged_read_resource
    target._lwp_logged = True


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


def serve_http(url, threaded=False, server=None):
    '''Block serving Streamable HTTP with token auth.'''

    spec = parse_mcp_url(url)
    import anyio
    import uvicorn

    target = server or mcp
    app = _ApiKeyASGI(
        target.streamable_http_app(
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


def start_background(url, server=None, prepare=None, thread_name='lwp-mcp'):
    '''Daemon thread: Streamable HTTP MCP next to the Flask app.'''

    def _run():
        try:
            (prepare or ensure_runtime)()
            serve_http(url, threaded=True, server=server)
        except Exception:
            log.exception('MCP server failed')

    thread = threading.Thread(target=_run, daemon=True, name=thread_name)
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


try:
    import lwp.mcp_lxc  # LXC tools on the same MCP server
except Exception:
    log.exception('LXC MCP tools not loaded')
try:
    import lwp.mcp_vm  # VM tools on the same MCP server
except Exception:
    log.exception('VM MCP tools not loaded')


if __name__ == '__main__':
    main()
