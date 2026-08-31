# LXC Python Library
# for compatibility with LXC 0.8 and 0.9
# on Ubuntu 12.04/12.10/13.04

# Author: Michael Privorotsky
# https://github.com/mprivoro/LXC-Web

# The MIT License (MIT)
# Copyright (c) 2013 Élie DELOUMEAU
# Copyright (c) 2026 Michael Privorotsky

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

# Classic LXC CLI wrapper (lxc-create, lxc-start, lxc-snapshot, …).
# Container store is lxc.lxcpath from the LXC conf file (path from lwp.conf
# [lxc] conf). If that is missing or invalid, [lxc] store. Not LXD/Incus.

import subprocess
import os
import re
import tempfile
import time
import shutil


def _log_cmd(cmd, rc, output=''):
    '''Forward the command to the panel log; never raise.'''

    try:
        from lwp.ctlog import log_cmd
        log_cmd(cmd, rc, output)
    except Exception:
        pass


def _run(cmd, env=None):
    '''Run a shell command, log it, raise CalledProcessError on failure.'''

    try:
        out = subprocess.check_output(
            '{}'.format(cmd), shell=True,
            universal_newlines=True, stderr=subprocess.STDOUT, env=env)
        _log_cmd(cmd, 0, out)
        return out
    except subprocess.CalledProcessError as e:
        _log_cmd(cmd, e.returncode, e.output)
        raise
    except Exception as e:
        _log_cmd(cmd, -1, str(e))
        raise


class ContainerAlreadyExists(Exception):
    '''Create/clone name is already taken.'''
    pass


class ContainerDoesntExists(Exception):
    '''No such container directory.'''
    pass


class ContainerAlreadyRunning(Exception):
    '''Start/restore refused because the CT is not stopped.'''
    pass


class ContainerNotRunning(Exception):
    '''Stop/freeze refused because the CT is already stopped.'''
    pass


class SnapshotDoesntExists(Exception):
    '''Named snapshot is not on this container.'''
    pass


class InvalidSnapshot(Exception):
    '''Snapshot or restore name failed the safety check.'''
    pass


class SnapshotNotPossible(Exception):
    '''Storage/state combination cannot snapshot.'''
    pass


class SnapshotNeedsConfirm(Exception):
    '''Live snapshot needs the user to confirm allow_running.'''
    pass


def exists(container):
    '''True if this name is in ls() (directory + config/rootfs).'''


    return (container in ls())

def create(container, template='ubuntu', storage=None, xargs=None, env=None):
    '''lxc-create -n name -t template, optional -B storage and extra args.'''


    if exists(container):
        raise ContainerAlreadyExists(
            'Container {} already created!'.format(container))

    command = 'lxc-create -n {}'.format(container)
    command += ' -t {}'.format(template)

    if storage:
        command += ' -B {}'.format(storage)

    if xargs:
        command += ' -- {}'.format(xargs)

    return _run(command, env=env)


def clone(orig=None, new=None, snapshot=False):
    '''lxc-clone orig -> new. snapshot=True adds -s.'''


    if orig and new:
        if exists(new):
            raise ContainerAlreadyExists(
                'Container {} already exist!'.format(new))

        command = 'lxc-clone -o {} -n {}'.format(orig, new)
        if snapshot:
            command += ' -s'

        return _run(command)


def info(container):
    '''
    Check info from lxc-info.
    If LXC cannot load the container (killed/invalid config), return
    state BROKEN and an error string instead of raising.
    '''

    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exist!'.format(container))

    try:
        proc = subprocess.Popen(
            ['lxc-info', '-n', container, '-l', 'ERROR'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True)
        try:
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return {'state': 'BROKEN', 'pid': '0',
                    'error': 'lxc-info timed out', 'links': []}
    except OSError as e:
        return {'state': 'BROKEN', 'pid': '0',
                'error': 'lxc-info failed: {}'.format(e), 'links': []}

    state = ''
    pid = '0'
    links = []
    for line in (out or '').splitlines():
        if ':' not in line:
            continue
        key, val = line.split(':', 1)
        key = key.strip().lower()
        val = val.strip()
        if key == 'state' and val:
            state = val.split()[0].upper()
        elif key == 'pid' and val:
            pid = val.split()[0]
        elif key == 'link' and val:
            iface = val.split()[0]
            if iface and iface != 'lo':
                links.append(iface)

    if proc.returncode != 0 or not state:
        error = _config_load_error(err, out)
        cfg = os.path.join(_container_path(container), 'config')
        if not os.path.isfile(cfg):
            error = 'Config file is missing.'
        return {'state': 'BROKEN', 'pid': '0', 'error': error, 'links': []}

    if state == 'STOPPED':
        pid = '0'

    return {'state': state, 'pid': pid, 'error': '', 'links': links}

def _unique_keep(seq):
    '''Dedupe a list, keep first-seen order.'''

    seen = set()
    out = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _parse_ip_token(token):
    '''Classify a token as ipv4 or ipv6. Skip loopback and unspecified.'''

    token = (token or '').strip()
    if not token:
        return None, None
    addr = token.split()[0]
    host = addr.split('/')[0]
    if '%' in host:
        host = host.split('%', 1)[0]
    if host in ('127.0.0.1', '::1', '0.0.0.0', '::'):
        return None, None
    if ':' in host:
        return 'ipv6', addr
    if '.' in host:
        return 'ipv4', addr
    return None, None


def split_ip_text(text):
    '''Split whitespace/comma-separated addresses into IPv4 and IPv6 lists.'''

    ipv4 = []
    ipv6 = []
    for token in (text or '').replace(',', ' ').split():
        family, addr = _parse_ip_token(token)
        if family == 'ipv4':
            ipv4.append(addr)
        elif family == 'ipv6':
            ipv6.append(addr)
    ipv6.sort(key=lambda a: a.lower().startswith('fe80:'))
    return _unique_keep(ipv4), _unique_keep(ipv6)


def merge_ip_texts(*texts):
    '''Union of IPv4/IPv6 tokens from several config/live strings.'''

    ipv4, ipv6 = [], []
    for text in texts:
        v4, v6 = split_ip_text(text)
        ipv4.extend(v4)
        ipv6.extend(v6)
    ipv6.sort(key=lambda a: a.lower().startswith('fe80:'))
    return _unique_keep(ipv4), _unique_keep(ipv6)


def ip_addresses(container, assume_running=False):
    '''Live addresses from lxc-info -iH, split by family.'''

    ipv4, ipv6 = [], []
    try:
        if assume_running or (info(container)['state'] == 'RUNNING'):
            ipv4, ipv6 = split_ip_text(_run('lxc-info -n %s -iH' % container))
    except Exception:
        pass
    return {'ipv4': ipv4, 'ipv6': ipv6}


def ip_address(container, assume_running=False):
    '''Live addresses as one space-separated string (legacy).'''

    addrs = ip_addresses(container, assume_running)
    return ' '.join(addrs['ipv4'] + addrs['ipv6'])


def _valid_snapshot_name(snap):
    '''
    Snapshot names from lxc-snapshot look like snap0.
    Reject ALL so a request cannot wipe every snapshot.
    '''

    if not snap:
        return False
    if snap.upper() == 'ALL':
        return False
    return bool(re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*$', snap))


_lxcpath_cached = None
_LXC_CONF = '/etc/lxc/lxc.conf'
_LXCPATH_FALLBACK = '/var/lib/lxc'


def init_lxc_conf(conf_path, store=None):
    '''Set the LXC conf file and fallback store (from lwp.conf [lxc]).'''

    global _LXC_CONF, _LXCPATH_FALLBACK, _lxcpath_cached
    conf_path = (conf_path or '').strip()
    if conf_path.startswith('/'):
        _LXC_CONF = os.path.normpath(conf_path)
    store = (store or '').strip().rstrip('/')
    if store.startswith('/'):
        _LXCPATH_FALLBACK = os.path.normpath(store)
    _lxcpath_cached = None


def lxc_conf_path():
    '''Path of the LXC conf file that may contain lxc.lxcpath.'''

    return _LXC_CONF


def _lxcpath_from_file(filename):
    '''Read lxc.lxcpath from an lxc.conf file.'''

    try:
        with open(filename) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, val = line.split('=', 1)
                if key.strip() == 'lxc.lxcpath':
                    return val.strip().strip('"').rstrip('/')
    except (OSError, IOError):
        return ''
    return ''


def _valid_lxcpath(path):
    '''Absolute existing directory, or ''.'''

    path = (path or '').strip().rstrip('/')
    if not path.startswith('/') or not os.path.isdir(path):
        return ''
    return path


def lxcpath():
    '''
    Directory that holds one subdirectory per container.
    From lxc.lxcpath in the conf file ([lxc] conf) when that path is a
    real directory. Otherwise [lxc] store.
    '''

    global _lxcpath_cached
    if _lxcpath_cached is not None:
        return _lxcpath_cached
    path = _valid_lxcpath(_lxcpath_from_file(_LXC_CONF))
    if not path:
        path = _LXCPATH_FALLBACK
    _lxcpath_cached = path
    return _lxcpath_cached


def _container_path(container):
    '''Host directory for this CT: <lxc.lxcpath>/<name>.'''

    return os.path.join(lxcpath(), container)


def _next_snap_name(container):
    '''Next unused snapN name for a live copy.'''

    names = set(item['name'] for item in snapshots(container))
    n = 0
    while 'snap%d' % n in names:
        n += 1
    return 'snap%d' % n


def _lxc_output(cmd):
    '''
    Run an lxc CLI command. Force -l ERROR so messages are written even
    when stdout/stderr are not a tty (otherwise LXC prints nothing).
    '''

    cmd = list(cmd)
    if '-l' not in cmd and '--logpriority' not in cmd:
        cmd.extend(['-l', 'ERROR'])
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, universal_newlines=True)
        _log_cmd(cmd, 0, out)
        return out
    except subprocess.CalledProcessError as e:
        _log_cmd(cmd, e.returncode, e.output)
        raise
    except Exception as e:
        _log_cmd(cmd, -1, str(e))
        raise


def lxc_error_message(output):
    '''Best-effort human line from lxc CLI output.'''

    if not output:
        return 'lxc command failed'
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    for ln in reversed(lines):
        idx = ln.lower().rfind('error:')
        if idx != -1:
            return ln[idx + 6:].strip()
    for ln in reversed(lines):
        lower = ln.lower()
        if 'failed to' in lower or 'error creating' in lower:
            if ' - ' in ln:
                return ln.split(' - ', 1)[-1]
            return ln
    return lines[-1]


def _config_load_error(err, out=''):
    '''Human message when lxc-info cannot load a container config.'''

    combined = '\n'.join(x for x in (err, out) if x)
    if not combined.strip():
        return 'LXC cannot load this container config.'
    for ln in combined.splitlines():
        lower = ln.lower()
        if 'invalid' in lower or 'failed to parse' in lower:
            if ' - ' in ln:
                return ln.split(' - ', 1)[-1].strip()
            return ln.strip()
    return lxc_error_message(combined)


def rootfs_backend(container):
    '''
    Return (backend, raw_rootfs) from the container config.
    backend is dir, overlay, zfs, btrfs, lvm, loop, or unknown.
    '''

    raw = ''
    config_path = os.path.join(_container_path(container), 'config')
    try:
        with open(config_path) as fh:
            for line in fh:
                line = line.strip()
                if '=' not in line:
                    continue
                key = line.split('=', 1)[0].strip()
                if key in ('lxc.rootfs.path', 'lxc.rootfs'):
                    raw = line.split('=', 1)[1].strip()
    except (OSError, IOError):
        return 'unknown', raw

    if not raw:
        return 'unknown', raw
    if raw.startswith('/'):
        return 'dir', raw
    if ':' in raw:
        return raw.split(':', 1)[0].lower(), raw
    return 'dir', raw


def snapshot_plan(container):
    '''
    What taking a snapshot would do, and whether the user must confirm.

    Stopped: consistent lxc-snapshot, no extra confirm.
    Running/frozen + supported storage: possible live snapshot, confirm required.
    Otherwise: not possible, reason explains why.
    '''

    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exist!'.format(container))

    inf = info(container)
    state = inf['state']
    storage, _raw = rootfs_backend(container)
    plan = {
        'state': state,
        'storage': storage,
        'can': True,
        'need_confirm': False,
        'method': 'snapshot',
        'reason': '',
    }

    if state == 'BROKEN':
        plan['can'] = False
        plan['reason'] = inf.get('error') or (
            'LXC cannot load this container config.')
        return plan

    if state == 'STOPPED':
        plan['reason'] = (
            'The container is stopped. The snapshot is a consistent copy '
            'of the filesystem.'
        )
        return plan

    live = state.lower()
    if storage in ('dir', 'loop'):
        plan['need_confirm'] = True
        plan['method'] = 'copy-running'
        plan['reason'] = (
            'The container is %s (directory storage). A snapshot is possible '
            'only as a live copy: files can change during the copy, so it is '
            'not a clean shutdown. Stop the container first for a consistent '
            'snapshot.'
        ) % live
        return plan

    if storage in ('overlay', 'overlayfs', 'btrfs', 'zfs', 'lvm'):
        plan['need_confirm'] = True
        plan['method'] = 'snapshot'
        plan['reason'] = (
            'The container is %s (%s storage). A snapshot is possible, but it '
            'captures a live filesystem, not a clean shutdown. Stop the '
            'container first for a consistent snapshot.'
        ) % (live, storage)
        return plan

    plan['can'] = False
    plan['reason'] = (
        'Cannot snapshot a %s container with %s storage. Stop the container '
        'first, then try again.'
    ) % (live, storage)
    return plan


def _parse_snapshot_list(out):
    '''Parse `lxc-snapshot -L` text into name/path/created/comment dicts.'''

    snaps = []
    current = None
    snap_line = re.compile(r'^(\S+)\s+\(([^)]*)\)\s+(\S+)(?:\s+(\S+))?')
    for raw in out.splitlines():
        if raw.startswith((' ', '\t')):
            extra = raw.strip()
            if current is not None and extra:
                current['comment'] = (
                    '%s\n%s' % (current['comment'], extra)
                    if current['comment'] else extra
                )
            continue
        line = raw.strip()
        if not line or line.lower() == 'no snapshots':
            continue
        match = snap_line.match(line)
        if not match:
            if current is not None:
                current['comment'] = (
                    '%s\n%s' % (current['comment'], line)
                    if current['comment'] else line
                )
            continue
        created = match.group(3)
        if created in ('(null)', 'null'):
            created = ''
        elif match.group(4):
            created = '%s %s' % (created.replace(':', '-', 2), match.group(4))
        else:
            created = created.replace(':', '-', 2)
        current = {
            'name': match.group(1),
            'path': match.group(2),
            'created': created,
            'comment': '',
        }
        snaps.append(current)
    return snaps


def _read_text(path):
    '''Read a small file, strip, or '' if missing.'''

    try:
        with open(path) as fh:
            return fh.read().strip()
    except (OSError, IOError):
        return ''


def _snap_directory(container, snap, path=''):
    '''snaps/<snap> under the container (or under path from lxc-snapshot -L).'''

    parent = path or os.path.join(_container_path(container), 'snaps')
    return os.path.join(parent, snap)


def _safe_snap_directory(container, snap):
    '''
    Real path of snaps/<snap> if it stays under the container snaps dir.
    '''

    if not _valid_snapshot_name(snap):
        return ''
    base = os.path.realpath(os.path.join(_container_path(container), 'snaps'))
    target = os.path.realpath(os.path.join(base, snap))
    if target == base or not target.startswith(base + os.sep):
        return ''
    if not os.path.isdir(target):
        return ''
    return target


_du_cache = {}
_DU_TTL = 60


def _du_sm(path):
    '''du -sm of one path, or 0 if it fails. Cached for 60s.'''

    if not path or not os.path.exists(path):
        return 0
    now = time.time()
    real = os.path.realpath(path)
    cached = _du_cache.get(real)
    if cached and (now - cached[0]) < _DU_TTL:
        return cached[1]
    mb = 0
    try:
        out = subprocess.check_output(
            ['du', '-sm', path],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=30)
        mb = int(out.split()[0])
    except (subprocess.CalledProcessError, ValueError, OSError,
            subprocess.TimeoutExpired, IndexError):
        mb = 0
    _du_cache[real] = (now, mb)
    return mb


def snapshots(container):
    '''
    List LXC snapshots for a container.
    Returns a list of dicts: name, path, created, comment, directory, size_mb.
    Comments are read from each snapshot directory (lxc-snapshot -C
    concatenates a comment with no trailing newline onto the next name).
    '''

    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exist!'.format(container))

    try:
        out = subprocess.check_output(
            ['lxc-snapshot', '-L', '-n', container],
            stderr=subprocess.DEVNULL,
            universal_newlines=True)
    except (subprocess.CalledProcessError, OSError):
        return []

    snaps = _parse_snapshot_list(out)
    for item in snaps:
        directory = _snap_directory(container, item['name'], item.get('path'))
        if not os.path.isdir(directory):
            directory = _safe_snap_directory(container, item['name']) or directory
        item['directory'] = directory
        item['size_mb'] = _du_sm(directory) if os.path.isdir(directory) else 0
        if not item.get('comment'):
            item['comment'] = _read_text(os.path.join(directory, 'comment'))
        if not item.get('created') or item['created'] in ('(null)', 'null'):
            ts = _read_text(os.path.join(directory, 'ts'))
            if ts and ts[0:4].isdigit():
                item['created'] = ts.replace(':', '-', 2)
            else:
                item['created'] = ts
    return sorted(snaps, key=lambda item: item['name'])


def snapshot_info(container, snap):
    '''
    Details for one snapshot: name, path, directory, created, comment, size, rootfs.
    '''

    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exist!'.format(container))

    if not _valid_snapshot_name(snap):
        raise InvalidSnapshot(
            'Invalid snapshot name: {}'.format(snap))

    found = None
    for item in snapshots(container):
        if item['name'] == snap:
            found = dict(item)
            break

    if found is None:
        raise SnapshotDoesntExists(
            'Snapshot {} does not exist for {}!'.format(snap, container))

    directory = found.get('directory') or ''
    if not directory and found.get('path'):
        directory = os.path.join(found['path'], found['name'])
    found['directory'] = directory
    mb = int(found.get('size_mb') or 0)
    if mb >= 1024:
        found['size'] = '%.1f GB' % (mb / 1024.0)
    elif mb:
        found['size'] = '%s MB' % mb
    else:
        found['size'] = ''
    found['rootfs'] = ''

    if directory and os.path.isdir(directory):
        comment_file = os.path.join(directory, 'comment')
        if not found.get('comment') and os.path.isfile(comment_file):
            try:
                with open(comment_file) as fh:
                    found['comment'] = fh.read().strip()
            except (OSError, IOError):
                pass

        config_path = os.path.join(directory, 'config')
        if os.path.isfile(config_path):
            try:
                with open(config_path) as fh:
                    for line in fh:
                        line = line.strip()
                        if '=' not in line:
                            continue
                        key = line.split('=', 1)[0].strip()
                        if key in ('lxc.rootfs.path', 'lxc.rootfs'):
                            found['rootfs'] = line.split('=', 1)[1].strip()
            except (OSError, IOError):
                pass

    return found


def snapshot_destroy(container, snap):
    '''
    Destroy one snapshot of a container (lxc-snapshot -d).
    '''

    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exist!'.format(container))

    if not _valid_snapshot_name(snap):
        raise InvalidSnapshot(
            'Invalid snapshot name: {}'.format(snap))

    names = [item['name'] for item in snapshots(container)]
    if snap not in names:
        # Broken leftovers (no ts / (null) date) may be missing from a
        # confused -C listing; still allow delete if the directory exists.
        if not _safe_snap_directory(container, snap):
            raise SnapshotDoesntExists(
                'Snapshot {} does not exist for {}!'.format(snap, container))

    try:
        return _lxc_output(['lxc-snapshot', '-n', container, '-d', snap])
    except subprocess.CalledProcessError:
        snap_dir = _safe_snap_directory(container, snap)
        if not snap_dir:
            raise
        shutil.rmtree(snap_dir)
        _log_cmd('rmtree %s' % snap_dir, 0,
                 'snapshot leftover removed after lxc-snapshot -d failed')
        return ''


def snapshot_create(container, comment=None, allow_running=False):
    '''
    Create a new snapshot.
    Stopped: lxc-snapshot (consistent).
    Running/frozen: only if snapshot_plan says it is possible and
    allow_running is True (live copy / live snapshot).
    '''

    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exist!'.format(container))

    plan = snapshot_plan(container)
    if not plan['can']:
        raise SnapshotNotPossible(plan['reason'])
    if plan['need_confirm'] and not allow_running:
        raise SnapshotNeedsConfirm(plan['reason'])

    before = set(item['name'] for item in snapshots(container))
    comment = (comment or '').strip()
    tmp = None
    try:
        if plan['method'] == 'snapshot':
            cmd = ['lxc-snapshot', '-n', container]
            if comment:
                fd, tmp = tempfile.mkstemp(prefix='lwp-snap-c-')
                with os.fdopen(fd, 'w') as fh:
                    fh.write(comment.rstrip() + '\n')
                cmd.extend(['-c', tmp])
            _lxc_output(cmd)
        else:
            snap = _next_snap_name(container)
            snap_parent = os.path.join(_container_path(container), 'snaps')
            os.makedirs(snap_parent, exist_ok=True)
            _lxc_output([
                'lxc-copy', '-n', container, '-N', snap,
                '-p', snap_parent, '-a', '-K', '-M',
            ])
            snap_dir = os.path.join(snap_parent, snap)
            ts_path = os.path.join(snap_dir, 'ts')
            if not os.path.isfile(ts_path):
                with open(ts_path, 'w') as fh:
                    fh.write(time.strftime('%Y:%m:%d %H:%M:%S'))
            if comment:
                with open(os.path.join(snap_dir, 'comment'), 'w') as fh:
                    fh.write(comment.rstrip() + '\n')
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    created = [item['name'] for item in snapshots(container)
               if item['name'] not in before]
    return created[0] if created else ''


def snapshot_restore(container, snap, newname=None):
    '''
    Restore a snapshot. If newname is omitted or equals container, restore
    in place (container must be STOPPED). Otherwise create a new container.
    '''

    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exist!'.format(container))

    if not _valid_snapshot_name(snap):
        raise InvalidSnapshot(
            'Invalid snapshot name: {}'.format(snap))

    names = [item['name'] for item in snapshots(container)]
    if snap not in names:
        raise SnapshotDoesntExists(
            'Snapshot {} does not exist for {}!'.format(snap, container))

    inplace = not newname or newname == container
    if inplace:
        state = info(container)['state']
        if state == 'BROKEN':
            raise SnapshotNotPossible(
                'LXC cannot load {} — fix the config before restoring'.format(
                    container))
        if state != 'STOPPED':
            raise ContainerAlreadyRunning(
                'Stop {} before restoring a snapshot in place'.format(container))
        newname = container
    else:
        if newname == 'containers' or not re.match(r'^[a-zA-Z0-9_-]+$', newname):
            raise InvalidSnapshot(
                'Invalid name for restored container: {}'.format(newname))
        if exists(newname):
            raise ContainerAlreadyExists(
                'Container {} already created!'.format(newname))

    return _lxc_output(
        ['lxc-snapshot', '-n', container, '-r', snap, '-N', newname])


def ls():
    '''Names under lxc.lxcpath (one directory per container).'''

    base_path = lxcpath()

    try:
        names = os.listdir(base_path)
    except OSError:
        return []

    ct_list = []
    for name in names:
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            continue
        path = os.path.join(base_path, name)
        if not os.path.isdir(path):
            continue
        if (os.path.isfile(os.path.join(path, 'config')) or
                os.path.isdir(os.path.join(path, 'rootfs'))):
            ct_list.append(name)

    return sorted(ct_list)


def listx():
    '''
    List all containers with status (Running, Frozen or Stopped) in a dict
    Same as lxc-list or lxc-ls --fancy (0.9)
    '''

    stopped = []
    frozen = []
    running = []
    broken = []

    for container in ls():
        try:
            state = info(container)['state']
        except (ContainerDoesntExists, subprocess.CalledProcessError, OSError,
                IndexError, ValueError):
            state = 'BROKEN'
        if state == 'RUNNING':
            running.append(container)
        elif state == 'FROZEN':
            frozen.append(container)
        elif state == 'BROKEN':
            broken.append(container)
        else:
            stopped.append(container)

    return {'RUNNING': running,
            'FROZEN': frozen,
            'STOPPED': stopped,
            'BROKEN': broken}


def running():
    '''Names currently RUNNING.'''

    return listx()['RUNNING']


def frozen():
    '''Names currently FROZEN.'''

    return listx()['FROZEN']


def stopped():
    '''Names currently STOPPED (not broken).'''

    return listx()['STOPPED']


def start(container):
    '''lxc-start -dn (daemon, no console).'''


    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exists!'.format(container))

    if container in running():
        raise ContainerAlreadyRunning(
            'Container {} is already running!'.format(container))

    return _run('lxc-start -dn {}'.format(container))


def stop(container):
    '''lxc-stop.'''


    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exists!'.format(container))

    if container in stopped():
        raise ContainerNotRunning(
            'Container {} is not running!'.format(container))

    return _run('lxc-stop -n {}'.format(container))


def freeze(container):
    '''lxc-freeze (pause a running CT).'''


    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exists!'.format(container))

    if not container in running():
        raise ContainerNotRunning(
            'Container {} is not running!'.format(container))

    return _run('lxc-freeze -n {}'.format(container))


def unfreeze(container):
    '''lxc-unfreeze.'''


    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exists!'.format(container))

    if not container in frozen():
        raise ContainerNotRunning(
            'Container {} is not frozen!'.format(container))

    return _run('lxc-unfreeze -n {}'.format(container))


def destroy(container):
    '''lxc-destroy (no snapshots prompt — caller must confirm in the UI).'''


    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exists!'.format(container))

    return _run('lxc-destroy -n {}'.format(container))


def checkconfig():
    '''lxc-checkconfig lines with ANSI colors stripped.'''


    out = _run('lxc-checkconfig')

    if out:
        return out.replace('[1;32m', '').replace('[1;33m', '') \
            .replace('[0;39m', '').replace('[1;32m', '') \
            .replace('\x1b', '').replace(': ', ':').split('\n')

    return out


def cgroup(container, key, value):
    '''lxc-cgroup -n name key value (live cgroup write).'''

    if not exists(container):
        raise ContainerDoesntExists(
            'Container {} does not exist!'.format(container))

    return _run('lxc-cgroup -n {} {} {}'.format(container, key, value))
