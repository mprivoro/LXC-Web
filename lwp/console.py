# LXC attach console over WebSocket.
# Isolated so the feature can be removed without touching the rest of the panel:
#   delete this file, templates/console.html, static/js/lwp-console.js,
#   static/js/xterm.js, static/js/xterm-addon-fit.js, static/css/xterm.css,
#   and the small hooks in lwp.py / index.html / lwp.css.

import atexit
import os
import queue
import re
import select
import signal
import threading
import time

from flask import session
from ptyprocess import PtyProcess
from simple_websocket import ConnectionClosed

_NAME = re.compile(r'^[A-Za-z0-9_-]+$')
_RESIZE_PREFIX = '\x1fR'

# Live host-side lxc-attach processes. One per container: a new console
# replaces the previous attach so tabs cannot pile up sessions.
_sessions = {}
_sessions_lock = threading.Lock()
_signals_installed = False


def register_console(sock, lxc_mod):
    '''Attach /console/<name> to the Flask-Sock instance.'''

    if sock is None:
        return
    _install_cleanup_hooks()

    @sock.route('/console/<name>')
    def lxc_console(ws, name):
        '''WebSocket handler: PTY to lxc-attach for this container.'''

        _run_console(ws, name, lxc_mod)


def _install_cleanup_hooks():
    '''Kill leftover attach processes on SIGTERM/INT/HUP and atexit.'''

    global _signals_installed
    if _signals_installed:
        return
    _signals_installed = True
    atexit.register(_kill_all)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            prev = signal.getsignal(sig)
            signal.signal(sig, lambda s, f, prev=prev: _on_exit_signal(s, f, prev))
        except (ValueError, OSError):
            pass


def _on_exit_signal(signum, frame, prev):
    '''Kill attach sessions, then chain to the previous handler.'''

    _kill_all()
    if callable(prev) and prev not in (signal.SIG_DFL, signal.SIG_IGN):
        prev(signum, frame)
        return
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def _kill_all():
    '''Stop every tracked lxc-attach (panel shutdown).'''

    with _sessions_lock:
        procs = list(_sessions.values())
        _sessions.clear()
    for proc in procs:
        _kill_proc(proc)


def _kill_proc(proc):
    '''Tear down host lxc-attach. Guest shell should get SIGHUP on the PTY.'''

    if proc is None:
        return
    pid = getattr(proc, 'pid', None)
    try:
        if proc.isalive():
            proc.kill(signal.SIGHUP)
    except Exception:
        pass
    try:
        if proc.isalive():
            proc.kill(signal.SIGKILL)
    except Exception:
        pass
    if pid:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        for _ in range(20):
            try:
                wpid, _status = os.waitpid(pid, os.WNOHANG)
            except OSError:
                break
            if wpid != 0:
                break
            time.sleep(0.02)
    try:
        if not getattr(proc, 'closed', True):
            proc.close(force=True)
    except Exception:
        pass


def _remember(name, proc):
    '''Track this attach; kill any previous one for the same CT.'''

    with _sessions_lock:
        old = _sessions.get(name)
        _sessions[name] = proc
    if old is not None and old is not proc:
        _kill_proc(old)


def _forget(name, proc):
    '''Drop this proc from the map if it is still the current one.'''

    with _sessions_lock:
        if _sessions.get(name) is proc:
            _sessions.pop(name, None)


def _run_console(ws, name, lxc_mod):
    '''su-only: spawn lxc-attach, copy PTY bytes to the socket and back.'''

    if 'logged_in' not in session or session.get('su') != 'Yes':
        _close_with(ws, 'Not allowed.')
        return
    if not _NAME.match(name or ''):
        _close_with(ws, 'Invalid container name.')
        return
    try:
        if not lxc_mod.exists(name):
            _close_with(ws, 'Container does not exist.')
            return
        state = lxc_mod.info(name).get('state', '')
    except Exception as e:
        _close_with(ws, 'Cannot inspect container: %s' % e)
        return
    if state != 'RUNNING':
        _close_with(ws, 'Container is not running (%s).' % (state or 'unknown'))
        return

    try:
        proc = PtyProcess.spawn(
            ['lxc-attach', '-n', name],
            dimensions=(24, 80),
        )
    except Exception as e:
        try:
            from lwp.ctlog import log_cmd
            log_cmd(['lxc-attach', '-n', name], 1, str(e))
        except Exception:
            pass
        _close_with(ws, 'lxc-attach failed: %s' % e)
        return

    try:
        from lwp.ctlog import log_cmd
        log_cmd(['lxc-attach', '-n', name], 0,
                'started pid=%s' % getattr(proc, 'pid', '?'))
    except Exception:
        pass

    _remember(name, proc)
    outgoing = queue.Queue()
    stop = threading.Event()

    def pump_pty():
        '''Background thread: read PTY output into the outgoing queue.'''

        fd = proc.fd
        while not stop.is_set() and proc.isalive():
            try:
                ready, _, _ = select.select([fd], [], [], 0.4)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            outgoing.put(data.decode('utf-8', 'replace'))
        outgoing.put(None)

    reader = threading.Thread(target=pump_pty, daemon=True)
    reader.start()

    try:
        while not stop.is_set():
            try:
                while True:
                    chunk = outgoing.get_nowait()
                    if chunk is None:
                        return
                    try:
                        ws.send(chunk)
                    except ConnectionClosed:
                        return
            except queue.Empty:
                pass

            try:
                msg = ws.receive(timeout=0.15)
            except ConnectionClosed:
                return
            if msg is None:
                continue
            if isinstance(msg, bytes):
                try:
                    os.write(proc.fd, msg)
                except OSError:
                    return
                continue
            if msg.startswith(_RESIZE_PREFIX):
                _apply_resize(proc, msg[len(_RESIZE_PREFIX):])
                continue
            try:
                os.write(proc.fd, msg.encode('utf-8'))
            except OSError:
                return
    except ConnectionClosed:
        return
    finally:
        stop.set()
        _forget(name, proc)
        pid = getattr(proc, 'pid', '?')
        _kill_proc(proc)
        try:
            from lwp.ctlog import log_cmd
            log_cmd(['lxc-attach', '-n', name], 0, 'closed pid=%s' % pid)
        except Exception:
            pass


def _apply_resize(proc, spec):
    '''Apply COLSxROWS from the browser (prefix already stripped).'''

    try:
        cols_s, rows_s = spec.split('x', 1)
        cols = int(cols_s)
        rows = int(rows_s)
    except (TypeError, ValueError):
        return
    if cols < 2 or rows < 1 or cols > 500 or rows > 200:
        return
    try:
        proc.setwinsize(rows, cols)
    except Exception:
        pass


def _close_with(ws, text):
    '''Send a last line and close the WebSocket.'''

    try:
        ws.send(text + '\r\n')
    except Exception:
        pass
    try:
        ws.close()
    except Exception:
        pass
