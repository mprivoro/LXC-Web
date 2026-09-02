# Shared helpers: number formatting and form-field regexes.
# Not LXC-specific. Used by Flask views when validating POST data.

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
