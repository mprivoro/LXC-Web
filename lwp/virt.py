# Libvirt helpers for the VM panel: live CPU/net, memory, settings wrapper.

import os
import time

import libvirtlite as virt
from lwp.util import cpu_color, empty_live_metrics, format_bytes, format_qty

_live_samples = {}


def forget_live_sample(name):
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

    if rx or tx:
        out['net_rx_label'] = format_bytes(rx)
        out['net_tx_label'] = format_bytes(tx)
        out['net_title'] = 'Lifetime: ↓ %s  ↑ %s' % (
            format_bytes(rx), format_bytes(tx))

    if not prev:
        return out
    dt = now - prev['t']
    if dt < 0.5:
        return out

    if cpu is not None and prev.get('cpu') is not None:
        dcpu = cpu - prev['cpu']
        if dcpu >= 0:
            pct = (dcpu / 1e9) / dt * 100.0
            ncpu = os.cpu_count() or 1
            host_pct = pct / float(ncpu)
            out['cpu_pct'] = pct
            out['cpu_label'] = '%s%% (%s%%)' % (
                format_qty(pct, 1), format_qty(host_pct, 1))
            out['cpu_color'] = cpu_color(pct)
            out['cpu_title'] = (
                'Since last refresh. 100%% = one full core; '
                '%s host CPUs = %s%%. '
                'Figure in parentheses is the share of the whole host.'
                % (ncpu, format_qty(ncpu * 100)))

    drx = rx - prev['rx']
    dtx = tx - prev['tx']
    if drx >= 0 and dtx >= 0:
        out['net_rx_label'] = format_bytes(drx / dt, per_sec=True)
        out['net_tx_label'] = format_bytes(dtx / dt, per_sec=True)
        out['net_title'] = 'Lifetime: ↓ %s  ↑ %s' % (
            format_bytes(rx), format_bytes(tx))
    return out
