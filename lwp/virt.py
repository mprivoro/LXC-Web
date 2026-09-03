# Libvirt helpers for the VM panel: live CPU/net, memory, settings wrapper.

import time

import libvirtlite as virt
from lwp.util import apply_live_delta, empty_live_metrics

_live_samples = {}


def forget_live_sample(name):
    '''Drop the last CPU/net sample (VM stopped or restarted).'''

    _live_samples.pop(name, None)


def memory_usage(name, known_live=None):
    '''Current balloon/RSS in MB when running; otherwise configured max.'''

    inf = virt.info(name)
    live = inf.get('state') in ('RUNNING', 'FROZEN')
    if known_live is False or not live:
        kib = inf.get('memory_kib') or 0
        return int(round(kib / 1024.0)) if kib else 0
    try:
        out = virt._run(['dommemstat', name], timeout=15)
    except Exception:
        kib = inf.get('memory_kib') or 0
        return int(round(kib / 1024.0)) if kib else 0
    rss = actual = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            val = int(parts[1])
        except ValueError:
            continue
        if parts[0] == 'rss':
            rss = val
        elif parts[0] == 'actual':
            actual = val
    kib = rss or actual or inf.get('memory_kib') or 0
    return int(round(kib / 1024.0)) if kib else 0


def vm_live_metrics(name):
    '''CPU % of one core and NIC rates since the last sample.'''

    out = empty_live_metrics()
    cpu = virt.cpu_time_nsec(name)
    rx, tx = virt.if_bytes(name)
    now = time.time()
    prev = _live_samples.get(name)
    _live_samples[name] = {'t': now, 'cpu': cpu, 'rx': rx, 'tx': tx}
    return apply_live_delta(out, prev, now, cpu, rx, tx, 1e9)
