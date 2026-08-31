# Append-only logs: container commands ([logging] file) and MCP requests
# ([logging] mcp).

import contextvars
import os
import threading
from datetime import datetime

_lock = threading.Lock()
_path = None
_mcp_path = None
_MAX_OUT = 1000 * 1000
_actor = contextvars.ContextVar('lwp_log_actor', default=None)


def _bind_path(path):
    '''Normalize a log path. Empty disables. Creates the directory if needed.'''

    path = (path or '').strip()
    if not path:
        return None
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            return None
    return path


def init(path):
    '''Set container command log file. Empty path disables it.'''

    global _path
    _path = _bind_path(path)


def init_mcp(path):
    '''Set MCP request log file. Empty path disables it.'''

    global _mcp_path
    _mcp_path = _bind_path(path)


def enabled():
    '''True if a container log path was set and the directory could be used.'''

    return bool(_path)


def log_path():
    '''Absolute or relative path of the container log, or empty if disabled.'''

    return _path or ''


def mcp_log_path():
    '''Absolute or relative path of the MCP log, or empty if disabled.'''

    return _mcp_path or ''


def set_actor(user, via=''):
    '''Attribute following log_cmd / log_mcp lines to this user (MCP or panel).'''

    return _actor.set({'user': user or '-', 'via': via or ''})


def reset_actor(token):
    '''Undo set_actor (pass the token it returned).'''

    _actor.reset(token)


def actor_info():
    '''(user, via) for the current command: MCP identity, else Flask session.'''

    val = _actor.get()
    if val:
        return val.get('user') or '-', val.get('via') or ''
    try:
        from flask import has_request_context, session
        if has_request_context():
            return session.get('username') or '-', 'panel'
    except Exception:
        pass
    return '-', ''


def read_log(max_bytes=256 * 1024):
    '''Return the container log text (tail if large) for the panel view.'''

    return _read_file(
        _path, max_bytes,
        empty='Logging is disabled ([logging] file is empty).',
        missing='Log file does not exist yet. It is created on the first container action.')


def read_mcp_log(max_bytes=256 * 1024):
    '''Return the MCP request log text (tail if large) for the panel view.'''

    return _read_file(
        _mcp_path, max_bytes,
        empty='MCP logging is disabled ([logging] mcp is empty).',
        missing='MCP log file does not exist yet. It is created on the first MCP request.')


def _read_file(path, max_bytes, empty, missing):
    info = {
        'path': path or '',
        'text': '',
        'error': '',
        'truncated': False,
        'size': 0,
    }
    if not path:
        info['error'] = empty
        return info
    try:
        size = os.path.getsize(path)
    except FileNotFoundError:
        info['error'] = missing
        return info
    except OSError as e:
        info['error'] = str(e)
        return info
    info['size'] = size
    try:
        with _lock:
            with open(path) as fh:
                if size > max_bytes:
                    fh.seek(size - max_bytes)
                    fh.readline()
                    info['text'] = fh.read()
                    info['truncated'] = True
                else:
                    info['text'] = fh.read()
    except OSError as e:
        info['error'] = str(e)
    return info


def cmd_str(cmd):
    '''Flatten a command list/tuple to one line for the log.'''

    if isinstance(cmd, (list, tuple)):
        return ' '.join(str(part) for part in cmd)
    return str(cmd)


def is_readonly(cmd):
    '''Skip polling (overview refresh, listings). Mutations are logged.'''

    text = cmd_str(cmd)
    parts = text.split()
    if not parts:
        return True
    binary = os.path.basename(parts[0])
    if binary in ('lxc-info', 'lxc-checkconfig', 'lxc-ls', 'lxc-list'):
        return True
    if binary == 'lxc-snapshot' and '-L' in parts:
        return True
    if binary == 'du':
        return True
    if parts[0].startswith('/sbin/shutdown') or binary == 'shutdown':
        return True
    return False


def log_cmd(cmd, rc, output=''):
    '''Append one command block unless it is a read-only poll.'''

    if not _path or is_readonly(cmd):
        return
    if output is None:
        output = ''
    elif isinstance(output, bytes):
        output = output.decode('utf-8', 'replace')
    else:
        output = str(output)
    truncated = ''
    if len(output) > _MAX_OUT:
        output = output[:_MAX_OUT]
        truncated = '\n  ... (truncated)\n'
    if output.strip():
        body = output.rstrip() + truncated
    else:
        body = '(empty)'
    user, via = actor_info()
    block = (
        '----- %s -----\n'
        'user: %s\n'
        'via: %s\n'
        'cmd: %s\n'
        'rc: %s\n'
        'out:\n%s\n\n'
    ) % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user, via or '-',
         cmd_str(cmd), rc, body)
    _append(_path, block)


def log_mcp(fields):
    '''Append one MCP request block. fields is an ordered list of (key, value).'''

    if not _mcp_path:
        return
    user, via = actor_info()
    lines = [
        '----- %s -----' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'user: %s' % user,
        'via: %s' % (via or 'MCP'),
    ]
    for key, value in fields:
        if value is None:
            continue
        text = str(value)
        if '\n' in text:
            lines.append('%s:\n%s' % (key, text.rstrip()))
        else:
            lines.append('%s: %s' % (key, text))
    _append(_mcp_path, '\n'.join(lines) + '\n\n')


def _append(path, block):
    try:
        with _lock:
            with open(path, 'a') as fh:
                fh.write(block)
                fh.flush()
    except OSError:
        pass
