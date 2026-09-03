# Shared helpers: number formatting and form-field regexes.
# Not LXC-specific. Used by Flask views when validating POST data.

import os
import re

# Same patterns the panel already used (including unescaped '.' in IPv4).
IPV4_OCTET = r'(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)'
IPV4 = r'%s.%s.%s.%s' % (IPV4_OCTET, IPV4_OCTET, IPV4_OCTET, IPV4_OCTET)
IPV4_CIDR = IPV4 + r'(/(3[0-2]|[12]?[0-9]))?'
RE_CT_NAME = r'^[A-Za-z0-9_-]+$'
RE_CT_CREATE = r'^(?!^containers$)|[a-zA-Z0-9_-]+$'
RE_HOSTNAME = (
    r'(?!^containers$)|^(([a-zA-Z0-9]|[a-zA-Z0-9]'
    r'[a-zA-Z0-9\-]*[a-zA-Z0-9])\.)*([A-Za-z0-9]|'
    r'[A-Za-z0-9][a-zA-Z0-9\-]*[A-Za-z0-9])$'
)
RE_HWADDR = r'^([a-fA-F0-9]{2}[:|\-]?){6}$'
RE_IFACE = r'^[a-zA-Z0-9_-]+$'
RE_FLAGS = r'^(up|down)$'
RE_WORD = r'^\w+$'
RE_CPUS = r'^[0-9,-]+$'
RE_SHARES = r'^[0-9]+$'
RE_ROOTFS = r'^[a-zA-Z0-9_/\-\.]+'
RE_USERNAME = r'^\w+$'
RE_DISPLAY_NAME = r'[a-z A-Z0-9]{3,32}'
RE_ABS_DIR = r'^/[a-zA-Z0-9_/-]+$'
RE_ZFS = r'^[a-zA-Z0-9_/-]+$'
RE_FSTYPE = r'^[a-z0-9]+$'
RE_FSSIZE = r'^[1-9][0-9]*[G|M]$'
RE_BYTE = r'^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
RE_VM = r'^[A-Za-z0-9][A-Za-z0-9._-]*$'


def matches(pattern, value):
    '''True if value matches the regex. None never matches.'''

    if value is None:
        return False
    return bool(re.match(pattern, value))


def _group_thousands(intpart):
    '''Insert commas: 16384 -> 16,384. Used only by format_qty.'''

    sign = ''
    if intpart.startswith('-'):
        sign = '-'
        intpart = intpart[1:]
    intpart = intpart.lstrip('0') or '0'
    groups = []
    while intpart:
        groups.append(intpart[-3:])
        intpart = intpart[:-3]
    return sign + ','.join(reversed(groups))


def format_qty(value, decimals=0):
    '''1,234 or 1,234.5 — thousands comma, decimal point.'''

    try:
        n = float(value)
    except (TypeError, ValueError):
        return value if value is not None else ''
    if decimals:
        formatted = '%.*f' % (int(decimals), n)
        intpart, frac = formatted.split('.')
        return '%s.%s' % (_group_thousands(intpart), frac)
    return _group_thousands(str(int(round(n))))


def cpu_color(pct):
    '''Bootstrap class for a live CPU % bar (one core = 100).'''

    if pct is None:
        return ''
    if pct < 25:
        return 'success'
    if pct < 80:
        return 'warning'
    return 'danger'


def empty_live_metrics():
    '''Placeholder CPU/net fields for a stopped or broken overview row.'''

    return {
        'cpu_pct': None,
        'cpu_label': '',
        'cpu_color': '',
        'cpu_title': '',
        'net_rx_label': '',
        'net_tx_label': '',
        'net_title': '',
    }


def ok_vm_name(name):
    '''True if name is a legal libvirt domain name.'''

    return bool(name and re.match(RE_VM, name))


def apply_live_delta(out, prev, now, cpu, rx, tx, cpu_scale):
    '''
    Fill out (from empty_live_metrics) with CPU/net rates since prev.
    cpu_scale is 1e6 for LXC microseconds, 1e9 for libvirt nanoseconds.
    First sample: CPU empty, net is lifetime totals.
    '''

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
            pct = (dcpu / cpu_scale) / dt * 100.0
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


def format_bytes(n, per_sec=False):
    '''Compact size: whole B/KB/MB, GB with one decimal. Optional /s.'''

    try:
        n = float(n)
    except (TypeError, ValueError):
        return ''
    if n < 0:
        n = 0
    if n < 1024:
        label = '%s B' % format_qty(n)
    elif n < 1024 ** 2:
        label = '%s KB' % format_qty(n / 1024)
    elif n < 1024 ** 3:
        label = '%s MB' % format_qty(n / 1024 ** 2)
    else:
        label = '%s GB' % format_qty(n / 1024 ** 3, 1)
    if per_sec:
        label += '/s'
    return label
