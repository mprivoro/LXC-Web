# Host metrics: CPU, load, memory, disk, uptime, distro.
# Independent of LXC / LXD.

import os
import platform
import subprocess
import time


def host_memory_usage():
    '''
    Host memory in MB.
    {'percent', 'percent_cached', 'used', 'total'}
    '''

    total = free = buffers = cached = 0.0
    with open('/proc/meminfo') as fh:
        for line in fh:
            key = line.split()[0]
            val = float(line.split()[1])
            if key == 'MemTotal:':
                total = val
            elif key == 'MemFree:':
                free = val
            elif key == 'Buffers:':
                buffers = val
            elif key == 'Cached:':
                cached = val
    used = total - (free + buffers + cached)
    if total <= 0:
        return {'percent': 0, 'percent_cached': 0, 'used': 0, 'total': 0}
    return {
        'percent': int((used / total) * 100),
        'percent_cached': int((cached / total) * 100),
        'used': int(used / 1024),
        'total': int(total / 1024),
    }


def _cpu_times():
    '''Aggregate /proc/stat cpu line: total, idle, busy (iowait counts as busy).'''

    with open('/proc/stat') as f:
        parts = f.readline().split()
    nums = [float(x) for x in parts[1:]]
    while len(nums) < 8:
        nums.append(0.0)
    user, nice, system, idle, iowait, irq, softirq, steal = nums[:8]
    busy = user + nice + system + iowait + irq + softirq + steal
    total = busy + idle
    return total, idle, busy


def host_cpu_usage():
    '''CPU busy % over a short interval, plus load average and CPU count.'''

    t0, i0, _ = _cpu_times()
    time.sleep(0.25)
    t1, i1, _ = _cpu_times()
    dt = t1 - t0
    if dt <= 0:
        percent = 0.0
    else:
        percent = 100.0 * (1.0 - ((i1 - i0) / dt))
    percent = max(0.0, min(100.0, percent))

    load1 = load5 = load15 = 0.0
    try:
        with open('/proc/loadavg') as f:
            fields = f.read().split()
            load1, load5, load15 = (
                float(fields[0]), float(fields[1]), float(fields[2]))
    except (OSError, ValueError, IndexError):
        pass

    return {
        'percent': round(percent, 1),
        'load1': load1,
        'load5': load5,
        'load15': load15,
        'cpus': os.cpu_count() or 1,
    }


def host_cpu_percent():
    '''CPU busy percent as a string (legacy helper).'''

    return '%.1f' % host_cpu_usage()['percent']


def host_disk_usage(partition=None):
    '''df -h of one mount: Size, Used, Avail, Use% (total, used, free, percent).'''

    if not partition:
        partition = '/'
    usage = subprocess.check_output(
        ['df', '-h', partition],
        universal_newlines=True).split('\n')[1].split()
    return {
        'total': usage[1],
        'used': usage[2],
        'free': usage[3],
        'percent': usage[4],
    }


def host_uptime():
    '''Uptime from /proc/uptime: days (float) and H:MM.'''

    with open('/proc/uptime') as f:
        uptime = int(f.readlines()[0].split('.')[0])
    minutes = int(uptime / 60) % 60
    hours = int(uptime / 3600) % 24
    days = float('%.2f' % (uptime / 86400.0))
    return {'day': days, 'time': '%d:%02d' % (hours, minutes)}


def check_ubuntu():
    '''Host distro string from os-release (name kept from the old helper).'''

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
