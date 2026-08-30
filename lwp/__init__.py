# LXC Python Library
# for compatibility with LXC 0.8 and 0.9
# on Ubuntu 12.04/12.10/13.04

# Author: Michael Privorotsky
# https://github.com/mprivoro/LXC-Web

# The MIT License (MIT)
# Copyright (c) 2013 Antoine TANZILLI, Élie DELOUMEAU
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

# Container config I/O, cgroup memory, templates/images, lxc-net settings.
# Host metrics are re-exported from lwp.host so lwp.host_cpu_usage() still works.
# This is not the Flask app (that is lwp.py).

import sys
sys.path.append('../')
from lxclite import exists, listx, ContainerDoesntExists, _container_path, \
    merge_ip_texts, rootfs_backend

import os
import re
import shutil
import stat
import subprocess
import tempfile
import time

from io import StringIO

try:
    import configparser
except ImportError:
    import ConfigParser as configparser

from lwp.host import (
    check_ubuntu,
    host_cpu_percent,
    host_cpu_usage,
    host_disk_usage,
    host_memory_usage,
    host_uptime,
)


class CalledProcessError(Exception):
    '''Raised when a host command (e.g. lxc-net restart) fails.'''
    pass

cgroup = {}
cgroup['type'] = 'lxc.network.type'
cgroup['link'] = 'lxc.network.link'
cgroup['flags'] = 'lxc.network.flags'
cgroup['hwaddr'] = 'lxc.network.hwaddr'
cgroup['rootfs'] = 'lxc.rootfs'
cgroup['utsname'] = 'lxc.utsname'
cgroup['arch'] = 'lxc.arch'
cgroup['ipv4'] = 'lxc.network.ipv4'
cgroup['ipv6'] = 'lxc.network.ipv6'
cgroup['memlimit'] = 'lxc.cgroup.memory.limit_in_bytes'
cgroup['swlimit'] = 'lxc.cgroup.memory.memsw.limit_in_bytes'
cgroup['cpus'] = 'lxc.cgroup.cpuset.cpus'
cgroup['shares'] = 'lxc.cgroup.cpu.shares'
cgroup['deny'] = 'lxc.cgroup.devices.deny'
cgroup['allow'] = 'lxc.cgroup.devices.allow'

# LXC 1.0+ renamed several keys. Try legacy names first, then current ones.
SETTING_KEYS = {
    'type': (cgroup['type'], 'lxc.net.0.type'),
    'link': (cgroup['link'], 'lxc.net.0.link'),
    'flags': (cgroup['flags'], 'lxc.net.0.flags'),
    'hwaddr': (cgroup['hwaddr'], 'lxc.net.0.hwaddr'),
    'rootfs': (cgroup['rootfs'], 'lxc.rootfs.path'),
    'utsname': (cgroup['utsname'], 'lxc.uts.name'),
    'arch': (cgroup['arch'],),
    'ipv4': (cgroup['ipv4'], 'lxc.net.0.ipv4', 'lxc.net.0.ipv4.address'),
    'ipv6': (cgroup['ipv6'], 'lxc.net.0.ipv6', 'lxc.net.0.ipv6.address'),
    'memlimit': (cgroup['memlimit'],),
    'swlimit': (cgroup['swlimit'],),
    'cpus': (cgroup['cpus'],),
    'shares': (cgroup['shares'],),
}

# Keys that commonly appear more than once in a container config.
REPEATABLE_KEYS = (
    'lxc.include',
    cgroup['deny'],
    cgroup['allow'],
    'lxc.mount.entry',
)


def FakeSection(fp):
    '''Wrap a section-less LXC file so ConfigParser can read it.'''

    content = u"[DEFAULT]\n%s" % fp.read()

    return StringIO(content)


def _make_parser():
    '''
    LXC configs are not INI files: they have no sections and often repeat
    keys such as lxc.include. Python 3 ConfigParser rejects duplicates
    unless strict=False.
    '''
    try:
        parser = configparser.RawConfigParser(strict=False,
                                              interpolation=None)
    except TypeError:
        parser = configparser.RawConfigParser()
    parser.optionxform = str
    return parser


def _load_unsectioned(filename):
    '''Parse an LXC or lxc-net file that has no INI sections.'''

    parser = _make_parser()
    with open(filename) as fp:
        wrapped = FakeSection(fp)
    if hasattr(parser, 'read_file'):
        parser.read_file(wrapped, source=filename)
    else:
        parser.readfp(wrapped)
    return parser


def _config_get(config, keys, default=''):
    '''First matching key from aliases, or default.'''

    if isinstance(keys, str):
        keys = (keys,)
    for key in keys:
        try:
            return config.get('DEFAULT', key)
        except (configparser.NoOptionError, configparser.NoSectionError):
            continue
    return default


def _resolve_config_key(config, key):
    '''Use the key already present in the file, else the modern alias.'''
    aliases = SETTING_KEYS.get(key)
    if aliases is None:
        for names in SETTING_KEYS.values():
            if key in names:
                aliases = names
                break
    if aliases is None:
        aliases = (key,)
    for alias in aliases:
        if config.has_option('DEFAULT', alias):
            return alias
    return aliases[-1]


def DelSection(filename=None):
    '''Strip the fake [DEFAULT] line ConfigParser writes back.'''

    if filename:
        load = open(filename, 'r')
        read = load.readlines()
        load.close()
        i = 0
        while i < len(read):
            if '[DEFAULT]' in read[i]:
                del read[i]
                break
        load = open(filename, 'w')
        load.writelines(read)
        load.close()


def file_exist(filename):
    '''True if filename can be opened for reading.'''

    try:
        with open(filename) as f:
            f.close()
            return True
    except IOError:
        return False


def ls_auto():
    '''Names of autostart drop-ins in /etc/lxc/auto/.'''

    try:
        auto_list = os.listdir('/etc/lxc/auto/')
    except OSError:
        auto_list = []
    return auto_list


def _is_cgroup_v2():
    '''Host uses cgroup v2 (memory.current vs memory.usage_in_bytes).'''

    return os.path.exists('/sys/fs/cgroup/cgroup.controllers')


def memory_usage(name, known_live=None):
    '''Guest RAM in MB via lxc-cgroup, or 0 if not running/frozen.'''

    if not exists(name):
        raise ContainerDoesntExists(
            "The container (%s) does not exist!" % name)

    if known_live is None:
        states = listx()
        known_live = name in states.get('RUNNING', []) or \
            name in states.get('FROZEN', [])
    if not known_live:
        return 0

    # cgroup v2: memory.current; cgroup v1: memory.usage_in_bytes
    keys = ('memory.current', 'memory.usage_in_bytes')
    if not _is_cgroup_v2():
        keys = ('memory.usage_in_bytes', 'memory.current')

    for key in keys:
        try:
            out = subprocess.check_output(
                ['lxc-cgroup', '-n', name, key],
                stderr=subprocess.DEVNULL,
                universal_newlines=True).splitlines()
            if out:
                return int(int(out[0]) / 1024 / 1024)
        except (subprocess.CalledProcessError, ValueError, OSError):
            continue
    return 0


_disk_cache = {}
_DISK_TTL = 60


def _rootfs_dir(raw):
    '''Live rootfs directory from lxc.rootfs.path (not the CT parent, not snaps).'''

    raw = (raw or '').strip().strip('"')
    if not raw:
        return ''
    path = raw
    if not raw.startswith('/') and ':' in raw:
        path = raw.split(':', 1)[1].split(':')[0]
    if not path.startswith('/'):
        return ''
    path = path.rstrip('/') or '/'
    if os.path.isdir(path):
        return os.path.realpath(path)
    return ''


def _path_covered(path, counted):
    '''True if path is counted already or is a parent of a counted dir.'''

    for other in counted:
        if path == other or path.startswith(other + os.sep):
            return True
        if other.startswith(path + os.sep):
            return True
    return False


def _du_sm(path):
    '''du -sm of one path, or 0 if it fails.'''

    try:
        out = subprocess.check_output(
            ['du', '-sm', path],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=60)
        return int(out.split()[0])
    except (subprocess.CalledProcessError, ValueError, OSError,
            subprocess.TimeoutExpired, IndexError):
        return 0


def container_disk_usage(name):
    '''
    On-disk size in MB of the live container (rootfs plus config/log in the
    CT dir). Snapshot trees under snaps/ are excluded; they have their own
    size labels on Overview.
    '''
    now = time.time()
    cached = _disk_cache.get(name)
    if cached and (now - cached[0]) < _DISK_TTL:
        return cached[1]

    counted = []
    mb = 0
    rootfs = ''
    try:
        _backend, raw = rootfs_backend(name)
        rootfs = _rootfs_dir(raw)
    except Exception:
        rootfs = ''
    if not rootfs:
        guess = os.path.join(_container_path(name), 'rootfs')
        if os.path.isdir(guess):
            rootfs = os.path.realpath(guess)
    if rootfs:
        mb += _du_sm(rootfs)
        counted.append(rootfs)

    ct = _container_path(name)
    try:
        entries = os.listdir(ct)
    except OSError:
        entries = []
    for entry in entries:
        if entry == 'snaps':
            continue
        full = os.path.join(ct, entry)
        try:
            real = os.path.realpath(full)
        except OSError:
            continue
        if not os.path.exists(real) or _path_covered(real, counted):
            continue
        mb += _du_sm(real)
        counted.append(real)

    _disk_cache[name] = (now, mb)
    return mb


def get_templates_list():
    '''Template names from /usr/share/lxc/templates (lxc- prefix stripped).'''

    templates = []
    path = None

    try:
        path = os.listdir('/usr/share/lxc/templates')
    except:
        path = os.listdir('/usr/lib/lxc/templates')

    if path:
        for line in path:
                templates.append(line.replace('lxc-', ''))

    return sorted(templates)


_IMAGES_DOWNLOAD = '/var/cache/lxc/download'


def _normalize_images_dir(path):
    '''Absolute download tree. A cache base that contains download/ is accepted.'''

    path = (path or '').strip() or '/var/cache/lxc/download'
    if not path.startswith('/'):
        path = '/var/cache/lxc/download'
    path = os.path.normpath(path)
    if os.path.basename(path) != 'download':
        nested = os.path.join(path, 'download')
        if os.path.isdir(nested):
            return nested
    return path


def init_images_dir(path):
    '''Set the lxc-download cache tree from lwp.conf [lxc] images.'''

    global _IMAGES_DOWNLOAD
    _IMAGES_DOWNLOAD = _normalize_images_dir(path)


def images_download_dir():
    '''Directory scanned for dist/release/arch/variant (rootfs.tar.xz).'''

    return _IMAGES_DOWNLOAD


def images_cache_base():
    '''Parent of download/; passed to lxc-download as LXC_CACHE_PATH.'''

    d = _IMAGES_DOWNLOAD.rstrip(os.sep)
    if os.path.basename(d) == 'download':
        parent = os.path.dirname(d)
        return parent or '/'
    return d


def create_env():
    '''os.environ plus LXC_CACHE_PATH so lxc-create uses [lxc] images.'''

    env = os.environ.copy()
    env['LXC_CACHE_PATH'] = images_cache_base()
    return env


def get_cached_images():
    '''
    Cached lxc-download images under [lxc] images (default /var/cache/lxc/download).
    Each item: id, label, dist, release, arch, variant.
    '''
    base = images_download_dir()
    images = []
    if not os.path.isdir(base):
        return images

    for dist in sorted(os.listdir(base)):
        dist_path = os.path.join(base, dist)
        if not os.path.isdir(dist_path):
            continue
        for release in sorted(os.listdir(dist_path)):
            release_path = os.path.join(dist_path, release)
            if not os.path.isdir(release_path):
                continue
            for arch in sorted(os.listdir(release_path)):
                arch_path = os.path.join(release_path, arch)
                if not os.path.isdir(arch_path):
                    continue
                for variant in sorted(os.listdir(arch_path)):
                    variant_path = os.path.join(arch_path, variant)
                    rootfs = os.path.join(variant_path, 'rootfs.tar.xz')
                    if not os.path.isfile(rootfs):
                        continue
                    images.append({
                        'id': '%s:%s:%s:%s' % (dist, release, arch, variant),
                        'label': '%s / %s / %s' % (dist, release, arch),
                        'dist': dist,
                        'release': release,
                        'arch': arch,
                        'variant': variant,
                    })
    return images


def cached_image_xargs(image_id):
    '''
    Return lxc-download arguments for a cached image id, or None.
    '''
    if not image_id:
        return None
    for img in get_cached_images():
        if img['id'] == image_id:
            return '-d %s -r %s -a %s --variant %s --force-cache' % (
                img['dist'], img['release'], img['arch'], img['variant'])
    return None


def parse_create_source(source):
    '''
    Parse the Create CT source dropdown.
    Returns (template, xargs) or (None, None).
    Cached images always use template "download" with --force-cache.
    '''
    if not source:
        return None, None
    if source.startswith('image:'):
        xargs = cached_image_xargs(source[6:])
        if xargs:
            return 'download', xargs
        return None, None
    if source.startswith('template:'):
        tmpl = source[9:]
        if tmpl in get_templates_list():
            return tmpl, None
    return None, None


def check_version():
    '''Local version from the version file (no network).'''

    with open('version') as f:
        current = float(f.read().strip())
    return {'current': current,
            'latest': current}

def get_net_settings_fname():
    '''Path of lxc-net defaults, or None if neither file exists.'''

    filename = '/etc/default/lxc-net'
    if not file_exist(filename):
        filename = '/etc/default/lxc'
    if not file_exist(filename):
        filename = None
    return filename


def get_net_settings():
    '''Bridge/DHCP vars from /etc/default/lxc-net (or lxc). False if missing.'''

    filename = get_net_settings_fname()
    if not filename:
        return False

    config = _load_unsectioned(filename)
    cfg = {}
    cfg['use'] = config.get('DEFAULT', 'USE_LXC_BRIDGE').strip('"')
    cfg['bridge'] = config.get('DEFAULT', 'LXC_BRIDGE').strip('"')
    cfg['address'] = config.get('DEFAULT', 'LXC_ADDR').strip('"')
    cfg['netmask'] = config.get('DEFAULT', 'LXC_NETMASK').strip('"')
    cfg['network'] = config.get('DEFAULT', 'LXC_NETWORK').strip('"')
    cfg['range'] = config.get('DEFAULT', 'LXC_DHCP_RANGE').strip('"')
    cfg['max'] = config.get('DEFAULT', 'LXC_DHCP_MAX').strip('"')
    return cfg


def empty_container_settings(error=''):
    '''
    Placeholder settings when the container config is missing or unreadable.
    Always a dict so templates never crash on a missing config.
    '''

    return {
        'type': '',
        'link': '',
        'flags': '',
        'hwaddr': '',
        'rootfs': '',
        'utsname': '',
        'arch': '',
        'ipv4': '',
        'ipv6': '',
        'ipv4_addrs': [],
        'ipv6_addrs': [],
        'memlimit': '',
        'swlimit': '',
        'cpus': '',
        'shares': '',
        'auto': False,
        'config_error': error,
    }


def get_container_settings(name):
    '''Parsed CT config for the edit form (legacy + modern key names).'''


    filename = os.path.join(_container_path(name), 'config')

    if not file_exist(filename):
        return empty_container_settings('Config file is missing.')

    try:
        config = _load_unsectioned(filename)
    except (OSError, IOError, configparser.Error, UnicodeDecodeError,
            ValueError, TypeError):
        return empty_container_settings('Config file cannot be read.')

    cfg = empty_container_settings()
    cfg['type'] = _config_get(config, SETTING_KEYS['type'])
    cfg['link'] = _config_get(config, SETTING_KEYS['link'])
    cfg['flags'] = _config_get(config, SETTING_KEYS['flags'])
    cfg['hwaddr'] = _config_get(config, SETTING_KEYS['hwaddr'])
    cfg['rootfs'] = _config_get(config, SETTING_KEYS['rootfs'])
    cfg['utsname'] = _config_get(config, SETTING_KEYS['utsname'])
    cfg['arch'] = _config_get(config, SETTING_KEYS['arch'])
    cfg['ipv4'] = _config_get(config, SETTING_KEYS['ipv4'])
    cfg['ipv6'] = _config_get(config, SETTING_KEYS['ipv6'])
    cfg['ipv4_addrs'], cfg['ipv6_addrs'] = merge_ip_texts(
        cfg['ipv4'], cfg['ipv6'])
    memlimit = _config_get(config, SETTING_KEYS['memlimit'])
    cfg['memlimit'] = re.sub(r'[a-zA-Z]', '', memlimit) if memlimit else ''
    swlimit = _config_get(config, SETTING_KEYS['swlimit'])
    cfg['swlimit'] = re.sub(r'[a-zA-Z]', '', swlimit) if swlimit else ''
    cfg['cpus'] = _config_get(config, SETTING_KEYS['cpus'])
    cfg['shares'] = _config_get(config, SETTING_KEYS['shares'])

    auto = _config_get(config, ('lxc.start.auto',))
    if auto.strip() in ('1', 'true', 'yes'):
        cfg['auto'] = True
    else:
        cfg['auto'] = '%s.conf' % name in ls_auto()

    return cfg


def container_config_path(name):
    '''
    Absolute path of a container config, or '' if the name is unsafe.
    '''

    if not name or not re.match(r'^[A-Za-z0-9_-]+$', name):
        return ''
    return os.path.join(_container_path(name), 'config')


def read_container_config(name):
    '''
    Return (text, error). text is None if the file is missing or unreadable.
    error is 'missing' when the file does not exist.
    '''

    path = container_config_path(name)
    if not path:
        return None, 'Invalid container name.'
    try:
        with open(path) as fh:
            return fh.read(), ''
    except FileNotFoundError:
        return None, 'missing'
    except (OSError, IOError) as e:
        return None, str(e)


def write_container_config(name, text):
    '''
    Write the container config as-is (comments and duplicate keys kept).
    Replaces the live file atomically and keeps one config.bak copy.
    '''

    path = container_config_path(name)
    if not path:
        return False, 'Invalid container name.'
    if text is None:
        text = ''
    if '\0' in text:
        return False, 'Config contains a null byte.'
    if len(text) > 1024 * 1024:
        return False, 'Config is too large (1 MB max).'

    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if text and not text.endswith('\n'):
        text += '\n'

    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        return False, 'Container directory does not exist.'

    orig_mode = 0o644
    if os.path.isfile(path):
        orig_mode = stat.S_IMODE(os.stat(path).st_mode)

    fd, tmp = tempfile.mkstemp(prefix='.config.', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if os.path.isfile(path):
            shutil.copy2(path, path + '.bak')
        os.rename(tmp, path)
        os.chmod(path, orig_mode)
    except (OSError, IOError) as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        try:
            from lwp.ctlog import log_cmd
            log_cmd('write %s' % path, 1, str(e))
        except Exception:
            pass
        return False, str(e)
    try:
        from lwp.ctlog import log_cmd
        log_cmd('write %s' % path, 0, 'saved %d bytes' % len(text))
    except Exception:
        pass
    return True, ''


def restore_container_config_backup(name):
    '''Replace the live config with config.bak if that file exists.'''

    path = container_config_path(name)
    if not path:
        return False, 'Invalid container name.'
    bak = path + '.bak'
    if not os.path.isfile(bak):
        return False, 'No backup file (config.bak).'
    try:
        shutil.copy2(bak, path)
    except (OSError, IOError) as e:
        try:
            from lwp.ctlog import log_cmd
            log_cmd('restore_bak %s' % path, 1, str(e))
        except Exception:
            pass
        return False, str(e)
    try:
        from lwp.ctlog import log_cmd
        log_cmd('restore_bak %s' % path, 0, 'restored from config.bak')
    except Exception:
        pass
    return True, ''


def push_net_value(key, value):
    '''Write one KEY="value" into the lxc-net defaults file.'''

    filename = get_net_settings_fname()

    if filename:
        config = _load_unsectioned(filename)
        if not value:
            config.remove_option('DEFAULT', key)
        else:
            config.set('DEFAULT', key, value)

        with open(filename, 'w') as configfile:
            config.write(configfile)

        DelSection(filename=filename)

        load = open(filename, 'r')
        read = load.readlines()
        load.close()
        i = 0
        while i < len(read):
            if ' = ' in read[i]:
                split = read[i].split(' = ')
                split[1] = split[1].strip('\n')
                if '\"' in split[1]:
                    read[i] = '%s=%s\n' % (split[0].upper(), split[1])
                else:
                    read[i] = '%s=\"%s\"\n' % (split[0].upper(), split[1])
            i += 1
        load = open(filename, 'w')
        load.writelines(read)
        load.close()


def push_config_value(key, value, container=None):
    '''Set or unset one key in a container config (keeps duplicate includes).'''


    def save_repeatable_options(filename=None):
        '''
        ConfigParser collapses duplicate keys. Keep the original lines for
        options that LXC repeats (lxc.include, devices, mount entries).
        '''
        if filename:
            values = []
            with open(filename, 'r') as load:
                for line in load:
                    stripped = line.lstrip()
                    if stripped.startswith('#'):
                        continue
                    if any(stripped.startswith(k) for k in REPEATABLE_KEYS):
                        values.append(line)
            return values

    if container:
        filename = os.path.join(_container_path(container), 'config')

        save = save_repeatable_options(filename=filename)

        config = _load_unsectioned(filename)
        write_key = _resolve_config_key(config, key)
        if not value:
            if config.has_option('DEFAULT', write_key):
                config.remove_option('DEFAULT', write_key)
        elif key == cgroup['memlimit'] or key == cgroup['swlimit'] \
                and value is not False:
            config.set('DEFAULT', write_key, '%sM' % value)
        else:
            config.set('DEFAULT', write_key, value)

        for repeatable in REPEATABLE_KEYS:
            if config.has_option('DEFAULT', repeatable):
                config.remove_option('DEFAULT', repeatable)

        with open(filename, 'w') as configfile:
            config.write(configfile)

        DelSection(filename=filename)

        with open(filename, "a") as configfile:
            configfile.writelines(save)
        try:
            from lwp.ctlog import log_cmd
            log_cmd('push_config %s %s=%s' % (
                container, write_key, value if value else '(unset)'),
                    0, 'saved')
        except Exception:
            pass


def net_restart():
    '''Restart the lxc-net service. 0 on success, 1 on failure.'''

    cmd = ['/usr/sbin/service lxc-net restart']
    try:
        subprocess.check_call(cmd, shell=True)
        return 0
    except CalledProcessError:
        return 1
