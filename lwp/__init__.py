# LXC Python Library
# for compatibility with LXC 0.8 and 0.9
# on Ubuntu 12.04/12.10/13.04

# Author: Elie Deloumeau
# Contact: elie@deloumeau.fr

# The MIT License (MIT)
# Copyright (c) 2013 Elie Deloumeau

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

import sys
sys.path.append('../')
from lxclite import exists, stopped, ContainerDoesntExists

import os
import platform
import re
import subprocess
import time

from io import StringIO

try:
    import configparser
except ImportError:
    import ConfigParser as configparser


class CalledProcessError(Exception):
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
    parser = _make_parser()
    with open(filename) as fp:
        wrapped = FakeSection(fp)
    if hasattr(parser, 'read_file'):
        parser.read_file(wrapped, source=filename)
    else:
        parser.readfp(wrapped)
    return parser


def _config_get(config, keys, default=''):
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
    '''
    checks if a given file exist or not
    '''
    try:
        with open(filename) as f:
            f.close()
            return True
    except IOError:
        return False


def ls_auto():
    '''
    returns a list of autostart containers
    '''
    try:
        auto_list = os.listdir('/etc/lxc/auto/')
    except OSError:
        auto_list = []
    return auto_list


def _is_cgroup_v2():
    return os.path.exists('/sys/fs/cgroup/cgroup.controllers')


def memory_usage(name):
    '''
    returns memory usage in MB
    '''
    if not exists(name):
        raise ContainerDoesntExists(
            "The container (%s) does not exist!" % name)

    if name in stopped():
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


def host_memory_usage():
    '''
    returns a dict of host memory usage values
                    {'percent': int((used/total)*100),
                    'percent_cached':int((cached/total)*100),
                    'used': int(used/1024),
                    'total': int(total/1024)}
    '''
    out = open('/proc/meminfo')
    for line in out:
        if 'MemTotal:' == line.split()[0]:
            split = line.split()
            total = float(split[1])
        if 'MemFree:' == line.split()[0]:
            split = line.split()
            free = float(split[1])
        if 'Buffers:' == line.split()[0]:
            split = line.split()
            buffers = float(split[1])
        if 'Cached:' == line.split()[0]:
            split = line.split()
            cached = float(split[1])
    out.close()
    used = (total - (free + buffers + cached))
    return {'percent': int((used/total)*100),
            'percent_cached': int(((cached)/total)*100),
            'used': int(used/1024),
            'total': int(total/1024)}


def host_cpu_percent():
    '''
    returns CPU usage in percent
    '''
    f = open('/proc/stat', 'r')
    line = f.readlines()[0]
    data = line.split()
    previdle = float(data[4])
    prevtotal = float(data[1]) + float(data[2]) + \
        float(data[3]) + float(data[4])
    f.close()
    time.sleep(0.1)
    f = open('/proc/stat', 'r')
    line = f.readlines()[0]
    data = line.split()
    idle = float(data[4])
    total = float(data[1]) + float(data[2]) + float(data[3]) + float(data[4])
    f.close()
    intervaltotal = total - prevtotal
    percent = 100 * (intervaltotal - (idle - previdle)) / intervaltotal
    return str('%.1f' % percent)


def host_disk_usage(partition=None):
    '''
    returns a dict of disk usage values
                    {'total': usage[1],
                    'used': usage[2],
                    'free': usage[3],
                    'percent': usage[4]}
    '''
    if not partition:
        partition = '/'

    usage = subprocess.check_output(['df -h %s' % partition],
                                    universal_newlines=True,
                                    shell=True).split('\n')[1].split()
    return {'total': usage[1],
            'used': usage[2],
            'free': usage[3],
            'percent': usage[4]}


def host_uptime():
    '''
    returns a dict of the system uptime
            {'day': days,
            'time': '%d:%02d' % (hours,minutes)}
    '''
    f = open('/proc/uptime')
    uptime = int(f.readlines()[0].split('.')[0])
    minutes = int(uptime / 60) % 60
    hours = int(uptime / 3600) % 24
    days = float('%.2f' % (uptime / 86400.0))
    f.close()
    return {'day': days,
            'time': '%d:%02d' % (hours, minutes)}


def check_ubuntu():
    '''
    return the System version
    '''
    info = {}
    try:
        info = platform.freedesktop_os_release()
    except AttributeError:
        try:
            with open('/etc/os-release') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, val = line.split('=', 1)
                    info[key] = val.strip().strip('"')
        except (IOError, OSError):
            pass
    except OSError:
        pass

    if info:
        name = info.get('NAME') or info.get('ID') or 'Unknown'
        version = info.get('VERSION_ID') or info.get('VERSION') or ''
        return ('%s %s' % (name, version)).strip()

    try:
        dist = platform.linux_distribution()
        return ('%s %s' % (dist[0], dist[1])).strip() or 'Unknown'
    except AttributeError:
        return 'Unknown'


def get_templates_list():
    '''
    returns a sorted lxc templates list
    '''
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


def check_version():
    '''
    returns the local LWP version (no remote lookup)
    '''
    with open('version') as f:
        current = float(f.read().strip())
    return {'current': current,
            'latest': current}

def get_net_settings_fname():
    filename = '/etc/default/lxc-net'
    if not file_exist(filename):
        filename = '/etc/default/lxc'
    if not file_exist(filename):
        filename = None
    return filename


def get_net_settings():
    '''
    returns a dict of all known settings for LXC networking
    '''
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


def get_container_settings(name):
    '''
    returns a dict of all utils settings for a container
    '''

    if os.geteuid():
        filename = os.path.expanduser('~/.local/share/lxc/%s/config' % name)
    else:
        filename = '/var/lib/lxc/%s/config' % name

    if not file_exist(filename):
        return False
    config = _load_unsectioned(filename)
    cfg = {}
    cfg['type'] = _config_get(config, SETTING_KEYS['type'])
    cfg['link'] = _config_get(config, SETTING_KEYS['link'])
    cfg['flags'] = _config_get(config, SETTING_KEYS['flags'])
    cfg['hwaddr'] = _config_get(config, SETTING_KEYS['hwaddr'])
    cfg['rootfs'] = _config_get(config, SETTING_KEYS['rootfs'])
    cfg['utsname'] = _config_get(config, SETTING_KEYS['utsname'])
    cfg['arch'] = _config_get(config, SETTING_KEYS['arch'])
    cfg['ipv4'] = _config_get(config, SETTING_KEYS['ipv4'])
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


def push_net_value(key, value):
    '''
    replace a var in the lxc-net config file
    '''
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
    '''
    replace a var in a container config file
    '''

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
        if os.geteuid():
            filename = os.path.expanduser('~/.local/share/lxc/%s/config' %
                                          container)
        else:
            filename = '/var/lib/lxc/%s/config' % container

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


def net_restart():
    '''
    restarts LXC networking
    '''
    cmd = ['/usr/sbin/service lxc-net restart']
    try:
        subprocess.check_call(cmd, shell=True)
        return 0
    except CalledProcessError:
        return 1
