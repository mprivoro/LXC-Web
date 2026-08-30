# Append-only log of container manipulations (commands and results).
# Path comes from lwp.conf [logging] file.

import os
import threading
from datetime import datetime

_lock = threading.Lock()
_path = None
_MAX_OUT = 1000 * 1000


def init(path):
    '''Set log file. Empty path disables logging.'''

    global _path
    path = (path or '').strip()
    if not path:
        _path = None
        return
    _path = path
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            _path = None


def enabled():
    '''True if a log path was set and the directory could be used.'''

    return bool(_path)


def log_path():
    '''Absolute or relative path of the log file, or empty if disabled.'''

    return _path or ''


def read_log(max_bytes=256 * 1024):
    '''Return the log file text (tail if large) for the panel view.'''

    info = {
        'path': _path or '',
        'text': '',
        'error': '',
        'truncated': False,
        'size': 0,
    }
    if not _path:
        info['error'] = 'Logging is disabled ([logging] file is empty).'
        return info
    try:
        size = os.path.getsize(_path)
    except FileNotFoundError:
        info['error'] = 'Log file does not exist yet. It is created on the first container action.'
        return info
    except OSError as e:
        info['error'] = str(e)
        return info
    info['size'] = size
    try:
        with _lock:
            with open(_path) as fh:
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
    block = (
        '----- %s -----\n'
        'user: %s\n'
        'cmd: %s\n'
        'rc: %s\n'
        'out:\n%s\n\n'
    ) % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), _user(),
         cmd_str(cmd), rc, body)
    try:
        with _lock:
            with open(_path, 'a') as fh:
                fh.write(block)
                fh.flush()
    except OSError:
        pass


def _user():
    '''Session username if this ran inside a request, else '-'.'''

    try:
        from flask import has_request_context, session
        if has_request_context():
            return session.get('username') or '-'
    except Exception:
        pass
    return '-'
