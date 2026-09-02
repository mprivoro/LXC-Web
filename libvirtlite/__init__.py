# libvirt CLI wrapper (virsh / virt-clone). KVM/QEMU domains only.
# Connect URI comes from lwp.conf [vm] uri (default qemu:///system).

import os
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET

_URI = 'qemu:///system'
_DISK_DIR = ''
_RE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
_RE_IPV4 = re.compile(
    r'^(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)'
    r'(?:\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)){3}'
    r'(?:/(?:3[0-2]|[12]?[0-9]))?$'
)

_STATE_MAP = {
    'running': 'RUNNING',
    'idle': 'RUNNING',
    'in shutdown': 'RUNNING',
    'paused': 'FROZEN',
    'pmsuspended': 'FROZEN',
    'shut off': 'STOPPED',
    'crashed': 'BROKEN',
    'dying': 'BROKEN',
}

_VIRSH_RO = {
    'list', 'dominfo', 'domstate', 'dumpxml', 'domuuid', 'domiflist',
    'domifaddr', 'domblklist', 'domblkinfo', 'snapshot-list', 'cpu-stats',
    'domifstat', 'dommemstat', 'version', 'capabilities', 'nodecpustats',
    'cpu-models', 'net-list', 'net-dumpxml', 'net-info', 'pool-dumpxml',
}


def init_uri(uri):
    '''Set the libvirt connect URI for later virsh calls.'''

    global _URI
    _URI = (uri or '').strip() or 'qemu:///system'


def connect_uri():
    '''Current qemu:///… URI.'''

    return _URI


def init_disk_dir(path):
    '''Default directory for new VM disks ([vm] disk in lwp.conf).'''

    global _DISK_DIR
    _DISK_DIR = (path or '').strip()


def _log_cmd(cmd, rc, output=''):
    try:
        from lwp.ctlog import log_cmd
        log_cmd(cmd, rc, output)
    except Exception:
        pass


def _run(args, timeout=120, env=None):
    '''Run virsh -c URI <args>. Log mutations. Raise on failure.'''

    cmd = ['virsh', '-c', _URI] + list(args)
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, universal_newlines=True,
            timeout=timeout, env=env)
        if args and args[0] not in _VIRSH_RO:
            _log_cmd(cmd, 0, out)
        return out or ''
    except subprocess.CalledProcessError as e:
        _log_cmd(cmd, e.returncode, e.output)
        raise
    except Exception as e:
        _log_cmd(cmd, -1, str(e))
        raise


def virsh_error_message(output):
    '''Short error from virsh stderr/stdout.'''

    if not output:
        return ''
    if isinstance(output, bytes):
        output = output.decode('utf-8', 'replace')
    lines = [ln.strip() for ln in str(output).splitlines() if ln.strip()]
    if not lines:
        return ''
    for line in lines:
        if line.lower().startswith('error:'):
            return line[6:].strip() or line
    return lines[-1]


class ContainerAlreadyExists(Exception):
    pass


class ContainerDoesntExists(Exception):
    pass


class ContainerAlreadyRunning(Exception):
    pass


class ContainerNotRunning(Exception):
    pass


class InvalidSnapshot(Exception):
    pass


class SnapshotDoesntExists(Exception):
    pass


class SnapshotNotPossible(Exception):
    pass


class SnapshotNeedsConfirm(Exception):
    pass


def _need_name(name):
    if not name or not _RE_NAME.match(name):
        raise ContainerDoesntExists('Invalid domain name.')
    return name


def exists(name):
    '''True if this domain is defined.'''

    if not name or not _RE_NAME.match(name):
        return False
    try:
        _run(['domstate', name], timeout=20)
        return True
    except subprocess.CalledProcessError:
        return False


def ls():
    '''All defined domain names (any state).'''

    try:
        out = _run(['list', '--all', '--name'], timeout=30)
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        name = line.strip()
        if name and _RE_NAME.match(name):
            names.append(name)
    return names


def _domstate(name):
    out = _run(['domstate', name], timeout=20).strip().lower()
    return out.split('\n')[0].strip()


def info(name):
    '''state / pid-ish id / error, plus vcpus and memory_kib.'''

    name = _need_name(name)
    if not exists(name):
        raise ContainerDoesntExists('Domain %s does not exist.' % name)
    raw = _domstate(name)
    state = _STATE_MAP.get(raw, 'BROKEN')
    data = {
        'state': state,
        'pid': '0',
        'error': '' if state != 'BROKEN' else 'libvirt state: %s' % raw,
        'links': [],
        'virsh_state': raw,
        'vcpus': 0,
        'memory_kib': 0,
        'autostart': False,
        'uuid': '',
    }
    try:
        blob = _run(['dominfo', name], timeout=20)
    except Exception as e:
        data['error'] = data['error'] or str(e)
        return data
    for line in blob.splitlines():
        if ':' not in line:
            continue
        key, val = line.split(':', 1)
        key = key.strip().lower()
        val = val.strip()
        if key == 'id' and val not in ('-', ''):
            data['pid'] = val
        elif key == 'uuid':
            data['uuid'] = val
        elif key == 'cpu(s)':
            try:
                data['vcpus'] = int(val)
            except ValueError:
                pass
        elif key == 'max memory':
            try:
                data['memory_kib'] = int(val.split()[0])
            except (ValueError, IndexError):
                pass
        elif key == 'autostart':
            data['autostart'] = val.lower() in ('enable', 'enabled')
    try:
        data['links'] = _iface_targets(name)
    except Exception:
        data['links'] = []
    return data


def listx():
    '''Names grouped like LXC: RUNNING / FROZEN / STOPPED / BROKEN.'''

    grouped = {'RUNNING': [], 'FROZEN': [], 'STOPPED': [], 'BROKEN': []}
    for name in ls():
        try:
            state = info(name).get('state', 'BROKEN')
        except Exception:
            state = 'BROKEN'
        grouped.setdefault(state, []).append(name)
    return grouped


def running():
    return listx().get('RUNNING', [])


def start(name):
    name = _need_name(name)
    inf = info(name)
    if inf['state'] == 'RUNNING':
        raise ContainerAlreadyRunning('Domain %s is already running.' % name)
    if inf['state'] == 'FROZEN':
        _run(['resume', name])
        return
    _run(['start', name])


def stop(name):
    '''ACPI shutdown (virsh shutdown).'''

    name = _need_name(name)
    inf = info(name)
    if inf['state'] == 'STOPPED':
        raise ContainerNotRunning('Domain %s is already shut off.' % name)
    _run(['shutdown', name])


def force_stop(name):
    '''Immediate power off (virsh destroy). Does not undefine.'''

    name = _need_name(name)
    inf = info(name)
    if inf['state'] == 'STOPPED':
        raise ContainerNotRunning('Domain %s is already shut off.' % name)
    _run(['destroy', name])


def freeze(name):
    name = _need_name(name)
    inf = info(name)
    if inf['state'] != 'RUNNING':
        raise ContainerNotRunning('Domain %s is not running.' % name)
    _run(['suspend', name])


def unfreeze(name):
    name = _need_name(name)
    inf = info(name)
    if inf['state'] != 'FROZEN':
        raise ContainerNotRunning('Domain %s is not paused.' % name)
    _run(['resume', name])


def reboot(name):
    name = _need_name(name)
    inf = info(name)
    if inf['state'] == 'STOPPED':
        start(name)
        return
    if inf['state'] == 'FROZEN':
        unfreeze(name)
    _run(['reboot', name])


def destroy(name, remove_storage=False):
    '''Undefine the domain. Disks stay unless remove_storage is true.'''

    name = _need_name(name)
    if not exists(name):
        raise ContainerDoesntExists('Domain %s does not exist.' % name)
    inf = info(name)
    if inf['state'] != 'STOPPED':
        _run(['destroy', name])
    args = ['undefine', name, '--nvram', '--managed-save']
    if remove_storage:
        args.append('--remove-all-storage')
    try:
        _run(args)
    except subprocess.CalledProcessError:
        args = ['undefine', name]
        if remove_storage:
            args.append('--remove-all-storage')
        _run(args)


def dumpxml(name):
    name = _need_name(name)
    if not exists(name):
        raise ContainerDoesntExists('Domain %s does not exist.' % name)
    return _run(['dumpxml', name], timeout=30)


def define_xml(text, backup_name=''):
    '''virsh define from XML text. Optionally keep previous XML as .bak.'''

    text = text if isinstance(text, str) else text.decode('utf-8')
    if backup_name and exists(backup_name):
        try:
            old = dumpxml(backup_name)
            _xml_backup_path(backup_name, old)
        except Exception:
            pass
    fd, path = tempfile.mkstemp(prefix='lwp-vm-', suffix='.xml')
    try:
        os.write(fd, text.encode('utf-8'))
        os.close(fd)
        fd = None
        out = _run(['define', path], timeout=60)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(path)
        except OSError:
            pass
    return out


def _xml_backup_path(name, xml_text):
    directory = os.path.join(os.getcwd(), 'vm-xml.bak')
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return
    path = os.path.join(directory, '%s.xml.bak' % name)
    try:
        with open(path, 'w') as fh:
            fh.write(xml_text)
    except OSError:
        pass


def restore_xml_backup(name):
    path = os.path.join(os.getcwd(), 'vm-xml.bak', '%s.xml.bak' % name)
    if not os.path.isfile(path):
        return False, 'No XML backup.'
    try:
        with open(path) as fh:
            text = fh.read()
        define_xml(text)
        return True, ''
    except Exception as e:
        return False, str(e)


def xml_backup_exists(name):
    return os.path.isfile(
        os.path.join(os.getcwd(), 'vm-xml.bak', '%s.xml.bak' % name))


def set_autostart(name, enabled):
    name = _need_name(name)
    if enabled:
        _run(['autostart', name])
    else:
        _run(['autostart', '--disable', name])


def set_memory_mb(name, mb):
    '''Set max + current memory (KiB) in config; live if the VM is up.'''

    name = _need_name(name)
    mb = int(mb)
    if mb < 16:
        raise ValueError('Memory must be at least 16 MB.')
    kib = mb * 1024
    inf = info(name)
    flags = ['--config']
    if inf['state'] in ('RUNNING', 'FROZEN'):
        flags.append('--live')
    _run(['setmaxmem', name, str(kib)] + flags)
    _run(['setmem', name, str(kib)] + flags)


def set_vcpus(name, count):
    name = _need_name(name)
    count = int(count)
    if count < 1:
        raise ValueError('vCPUs must be at least 1.')
    inf = info(name)
    flags = ['--config']
    if inf['state'] in ('RUNNING', 'FROZEN'):
        flags.append('--live')
    try:
        _run(['setvcpus', name, str(count), '--maximum', '--config'])
    except subprocess.CalledProcessError:
        pass
    _run(['setvcpus', name, str(count)] + flags)


def _iface_targets(name):
    out = _run(['domiflist', name], timeout=20)
    links = []
    for line in out.splitlines()[2:]:
        parts = line.split()
        if parts and parts[0] != '-':
            links.append(parts[0])
    return links


def ip_addresses(name, _unused=True):
    '''Guest IPv4/IPv6 from lease or agent.'''

    ipv4, ipv6 = [], []
    if not exists(name):
        return {'ipv4': ipv4, 'ipv6': ipv6}
    for source in ('lease', 'agent'):
        try:
            out = _run(['domifaddr', name, '--source', source], timeout=15)
        except Exception:
            continue
        for line in out.splitlines()[2:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            addr = parts[-1]
            ip = addr.split('/')[0]
            if ':' in ip:
                if ip not in ipv6:
                    ipv6.append(ip)
            elif ip and ip not in ipv4:
                ipv4.append(ip)
        if ipv4 or ipv6:
            break
    return {'ipv4': ipv4, 'ipv6': ipv6}


def cpu_time_nsec(name):
    '''Total CPU time in nanoseconds, or None.'''

    try:
        out = _run(['cpu-stats', name, '--total'], timeout=15)
    except Exception:
        return None
    for line in out.splitlines():
        if 'cpu_time' in line.lower():
            try:
                return int(line.split()[-1])
            except (ValueError, IndexError):
                return None
    return None


def if_bytes(name):
    '''Sum rx_bytes / tx_bytes across host tap interfaces (guest RX/TX).'''

    rx = tx = 0
    for iface in _iface_targets(name) or []:
        try:
            out = _run(['domifstat', name, iface], timeout=15)
        except Exception:
            continue
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            key = parts[-2] if len(parts) >= 2 else ''
            try:
                val = int(parts[-1])
            except ValueError:
                continue
            if key == 'rx_bytes':
                rx += val
            elif key == 'tx_bytes':
                tx += val
    return rx, tx


def disk_paths(name):
    '''Backing files for disk devices.'''

    paths = []
    try:
        out = _run(['domblklist', name, '--details'], timeout=20)
    except Exception:
        return paths
    for line in out.splitlines()[2:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        kind, role, _target, source = parts[0], parts[1], parts[2], parts[3]
        if kind == 'file' and role == 'disk' and source != '-':
            paths.append(source)
    return paths


def disk_usage_mb(name):
    '''Allocated disk files in MB (sum of st_size).'''

    total = 0
    for path in disk_paths(name):
        try:
            total += os.path.getsize(path)
        except OSError:
            pass
    return int(total / (1024 * 1024))


def parse_settings(name, xml_text=None):
    '''Friendly settings dict for Overview / Edit.'''

    xml_text = xml_text if xml_text is not None else dumpxml(name)
    inf = info(name)
    settings = {
        'utsname': name,
        'title': '',
        'arch': '',
        'auto': inf.get('autostart'),
        'memlimit': int(round((inf.get('memory_kib') or 0) / 1024.0)) or 0,
        'vcpus': inf.get('vcpus') or 0,
        'uuid': inf.get('uuid') or '',
        'flags': 'down',
        'type': 'network',
        'link': '',
        'hwaddr': '',
        'ipv4': '',
        'ipv6': '',
        'ipv4_addrs': [],
        'ipv6_addrs': [],
        'disks': [],
        'nics': [],
        'domain_type': '',
        'boot': [],
        'iso': '',
        'cpu_style': 'host',
        'cpu_mode': '',
        'cpu_model': '',
        'config_error': inf.get('error') or '',
    }
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        settings['config_error'] = str(e)
        return settings
    settings['domain_type'] = root.get('type') or ''
    os_type = root.find('./os/type')
    if os_type is not None:
        settings['arch'] = os_type.get('arch') or (os_type.text or '')
    cpu_el = root.find('cpu')
    if cpu_el is not None:
        mode = (cpu_el.get('mode') or '').strip()
        model_el = cpu_el.find('model')
        model = ''
        if model_el is not None and (model_el.text or '').strip():
            model = model_el.text.strip()
        settings['cpu_mode'] = mode
        settings['cpu_model'] = model
        if mode in ('host-passthrough', 'host-model') or not mode:
            settings['cpu_style'] = 'host'
        else:
            settings['cpu_style'] = 'simulate'
    memory = root.find('memory')
    if memory is not None and (memory.text or '').strip():
        try:
            kib = _xml_kib(memory)
            if kib:
                settings['memlimit'] = int(round(kib / 1024.0))
        except ValueError:
            pass
    vcpu = root.find('vcpu')
    if vcpu is not None and (vcpu.text or '').strip().isdigit():
        settings['vcpus'] = int(vcpu.text.strip())
    settings['boot'] = [
        el.get('dev') for el in root.findall('./os/boot') if el.get('dev')]
    for disk in root.findall('./devices/disk'):
        source = disk.find('source')
        target = disk.find('target')
        path = ''
        if source is not None:
            path = source.get('file') or source.get('dev') or ''
        kind = disk.get('device') or 'disk'
        if kind == 'cdrom':
            if path and not settings['iso']:
                settings['iso'] = path
            continue
        if kind not in (None, 'disk'):
            continue
        settings['disks'].append({
            'path': path,
            'target': target.get('dev') if target is not None else '',
        })
    title_el = root.find('title')
    if title_el is not None and (title_el.text or '').strip():
        settings['title'] = title_el.text.strip()
        settings['utsname'] = settings['title']
    else:
        settings['title'] = ''
    nics = root.findall('./devices/interface')
    if nics:
        settings['flags'] = 'up'
        link_el = nics[0].find('link')
        if link_el is not None and (link_el.get('state') or '') == 'down':
            settings['flags'] = 'down'
    for nic in nics:
        mac_el = nic.find('mac')
        src = nic.find('source')
        model = nic.find('model')
        item = {
            'type': nic.get('type') or '',
            'mac': mac_el.get('address') if mac_el is not None else '',
            'network': '',
            'bridge': '',
            'model': model.get('type') if model is not None else '',
        }
        if src is not None:
            item['network'] = src.get('network') or ''
            item['bridge'] = src.get('bridge') or ''
        settings['nics'].append(item)
    if settings['nics']:
        first = settings['nics'][0]
        settings['type'] = first.get('type') or 'network'
        settings['link'] = first.get('network') or first.get('bridge') or ''
        settings['hwaddr'] = first.get('mac') or ''
    settings['ipv4'] = _meta_ipv4(root)
    if (not settings['ipv4'] and settings.get('type') == 'network'
            and settings.get('link') and settings.get('hwaddr')):
        try:
            settings['ipv4'] = dhcp_host_ip(settings['link'], settings['hwaddr'])
        except Exception:
            pass
    return settings


def _xml_kib(el):
    raw = (el.text or '').strip()
    n = int(float(raw))
    unit = (el.get('unit') or 'KiB').lower()
    if unit in ('b', 'bytes'):
        return n / 1024.0
    if unit in ('k', 'kb', 'kib'):
        return float(n)
    if unit in ('m', 'mb', 'mib'):
        return n * 1024.0
    if unit in ('g', 'gb', 'gib'):
        return n * 1024.0 * 1024.0
    return float(n)


def snapshots(name):
    name = _need_name(name)
    if not exists(name):
        raise ContainerDoesntExists('Domain %s does not exist.' % name)
    try:
        out = _run(['snapshot-list', name], timeout=30)
    except subprocess.CalledProcessError:
        return []
    snaps = []
    for line in out.splitlines()[2:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        snap_name = parts[0]
        created = ''
        if len(parts) >= 3:
            created = '%s %s' % (parts[1], parts[2])
        snaps.append({
            'name': snap_name,
            'path': '',
            'created': created,
            'comment': ' '.join(parts[3:]) if len(parts) > 3 else '',
            'directory': '',
            'size_mb': 0,
        })
    return snaps


def snapshot_info(name, snap):
    name = _need_name(name)
    if not _RE_NAME.match(snap or ''):
        raise InvalidSnapshot('Invalid snapshot name.')
    found = None
    for item in snapshots(name):
        if item['name'] == snap:
            found = dict(item)
            break
    if found is None:
        raise SnapshotDoesntExists(
            'Snapshot %s does not exist for %s.' % (snap, name))
    try:
        xml_text = _run(['snapshot-dumpxml', name, snap], timeout=30)
        found['path'] = 'libvirt:%s' % snap
        root = ET.fromstring(xml_text)
        desc = root.find('description')
        if desc is not None and desc.text:
            found['comment'] = desc.text.strip()
        ctime = root.find('creationTime')
        if ctime is not None and (ctime.text or '').isdigit():
            found['created'] = time.strftime(
                '%Y-%m-%d %H:%M:%S',
                time.localtime(int(ctime.text)))
    except Exception:
        pass
    found['size'] = ''
    found['rootfs'] = ''
    return found


def snapshot_plan(name):
    name = _need_name(name)
    inf = info(name)
    plan = {
        'can': True,
        'need_confirm': False,
        'reason': 'Internal libvirt snapshot of this domain.',
    }
    if inf['state'] in ('RUNNING', 'FROZEN'):
        plan['need_confirm'] = True
        plan['reason'] = (
            'The VM is %s. A snapshot is possible, but it is a live snapshot. '
            'Shut the VM off first for a consistent disk snapshot.'
        ) % inf['state'].lower()
    elif inf['state'] == 'BROKEN':
        plan['can'] = False
        plan['reason'] = 'Cannot snapshot a domain in this state.'
    return plan


def snapshot_create(name, comment=None, allow_running=False):
    name = _need_name(name)
    inf = info(name)
    if inf['state'] in ('RUNNING', 'FROZEN') and not allow_running:
        raise SnapshotNeedsConfirm(snapshot_plan(name)['reason'])
    snap = 'snap%s' % int(time.time())
    args = ['snapshot-create-as', name, snap]
    if comment:
        args.extend(['--description', comment])
    _run(args, timeout=300)
    for item in snapshots(name):
        if item['name'] == snap:
            return item
    return {'name': snap, 'comment': comment or '', 'created': '', 'size_mb': 0}


def snapshot_restore(name, snap, new_name=None):
    name = _need_name(name)
    if not _RE_NAME.match(snap or ''):
        raise InvalidSnapshot('Invalid snapshot name.')
    if new_name:
        raise SnapshotNotPossible(
            'Restore-as-new-name is not supported for libvirt snapshots. '
            'Clone the VM first, then revert.')
    inf = info(name)
    if inf['state'] != 'STOPPED':
        raise ContainerAlreadyRunning(
            'Shut off %s before reverting a snapshot.' % name)
    _run(['snapshot-revert', name, snap, '--force'], timeout=300)


def snapshot_destroy(name, snap):
    name = _need_name(name)
    if not _RE_NAME.match(snap or ''):
        raise InvalidSnapshot('Invalid snapshot name.')
    _run(['snapshot-delete', name, snap], timeout=120)


def clone(orig, new, snapshot=False):
    '''virt-clone --auto-clone. snapshot=True is ignored (qcow2 copy).'''

    orig = _need_name(orig)
    new = _need_name(new)
    if exists(new):
        raise ContainerAlreadyExists('Domain %s already exists.' % new)
    if not exists(orig):
        raise ContainerDoesntExists('Domain %s does not exist.' % orig)
    cmd = [
        'virt-clone', '--connect', _URI,
        '--original', orig, '--name', new, '--auto-clone',
    ]
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, universal_newlines=True,
            timeout=3600)
        _log_cmd(cmd, 0, out)
        return out
    except subprocess.CalledProcessError as e:
        _log_cmd(cmd, e.returncode, e.output)
        raise
    except Exception as e:
        _log_cmd(cmd, -1, str(e))
        raise


def create_from_xml(name, text):
    '''Define a new domain from XML. name must match <name> or we rewrite it.'''

    name = _need_name(name)
    if exists(name):
        raise ContainerAlreadyExists('Domain %s already exists.' % name)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError('Invalid XML: %s' % e)
    el = root.find('name')
    if el is None:
        el = ET.SubElement(root, 'name')
    el.text = name
    uuid_el = root.find('uuid')
    if uuid_el is not None:
        root.remove(uuid_el)
    define_xml(ET.tostring(root, encoding='unicode'))


def list_networks():
    '''Libvirt virtual network names (`<source network=…>`).'''

    try:
        out = _run(['net-list', '--all', '--name'], timeout=20)
    except Exception:
        return []
    names = []
    for line in out.splitlines():
        name = line.strip()
        if name and name.lower() != 'name' and _RE_NAME.match(name):
            names.append(name)
    return names


def _need_net(name):
    name = (name or '').strip()
    if not name or not _RE_NAME.match(name):
        raise ValueError('Invalid network name.')
    if name not in list_networks():
        raise ContainerDoesntExists('Network %s does not exist.' % name)
    return name


def _net_info_flags(name):
    info = {
        'active': False, 'autostart': False, 'persistent': True, 'bridge': '',
    }
    out = _run(['net-info', name], timeout=15)
    for line in out.splitlines():
        if ':' not in line:
            continue
        key, val = line.split(':', 1)
        key = key.strip().lower()
        val = val.strip()
        low = val.lower()
        if key == 'active':
            info['active'] = low in ('yes', 'true')
        elif key == 'autostart':
            info['autostart'] = low in ('yes', 'true')
        elif key == 'persistent':
            info['persistent'] = low in ('yes', 'true')
        elif key == 'bridge':
            info['bridge'] = val
    return info


def list_network_details():
    '''One row per libvirt network: state, bridge, forward, DHCP.'''

    rows = []
    for name in list_networks():
        row = {
            'name': name,
            'active': False,
            'autostart': False,
            'persistent': True,
            'bridge': '',
            'forward': '',
            'ip': '',
            'netmask': '',
            'prefix': '',
            'dhcp': False,
            'dhcp_start': '',
            'dhcp_end': '',
            'error': '',
        }
        try:
            row.update(_net_info_flags(name))
            root = _network_xml(name)
            fwd = root.find('forward')
            row['forward'] = (fwd.get('mode') if fwd is not None else '') or 'isolated'
            ip_el = root.find('ip')
            if ip_el is not None:
                row['ip'] = ip_el.get('address') or ''
                row['netmask'] = ip_el.get('netmask') or ''
                row['prefix'] = ip_el.get('prefix') or ''
            rng = root.find('.//dhcp/range')
            if rng is not None:
                row['dhcp'] = True
                row['dhcp_start'] = rng.get('start') or ''
                row['dhcp_end'] = rng.get('end') or ''
        except Exception as e:
            row['error'] = virsh_error_message(getattr(e, 'output', None)) or str(e)
        rows.append(row)
    return rows


def network_start(name):
    _run(['net-start', _need_net(name)], timeout=30)


def network_stop(name):
    '''Deactivate a network (`virsh net-destroy`). Does not undefine it.'''

    _run(['net-destroy', _need_net(name)], timeout=30)


def network_set_autostart(name, enabled):
    args = ['net-autostart', _need_net(name)]
    if not enabled:
        args.append('--disable')
    _run(args, timeout=20)


def list_bridges():
    '''Linux bridge names (`<source bridge=…>`).'''

    names = []
    base = '/sys/class/net'
    try:
        for name in sorted(os.listdir(base)):
            if not _RE_NAME.match(name):
                continue
            if os.path.isdir(os.path.join(base, name, 'bridge')):
                names.append(name)
    except OSError:
        return []
    return names


def list_link_names(net_type):
    '''Names for `<source network=…>` or `<source bridge=…>`.'''

    if (net_type or '').strip() == 'bridge':
        return list_bridges()
    return list_networks()


def _normalize_ipv4(value):
    v = (value or '').strip()
    if not v or v.lower() == 'undefined':
        return ''
    if not _RE_IPV4.match(v):
        raise ValueError('Invalid IP address.')
    return v


def _norm_mac(mac):
    raw = (mac or '').strip().lower().replace('-', ':')
    if re.match(r'^[0-9a-f]{12}$', raw):
        raw = ':'.join(raw[i:i + 2] for i in range(0, 12, 2))
    return raw


def _meta_ipv4(root):
    md = root.find('metadata')
    if md is None:
        return ''
    el = md.find('lwp_ipv4')
    if el is None:
        return ''
    return (el.text or '').strip()


def _patch_meta_ipv4(root, ipv4):
    ipv4 = _normalize_ipv4(ipv4)
    md = root.find('metadata')
    el = md.find('lwp_ipv4') if md is not None else None
    if not ipv4:
        if el is not None and md is not None:
            md.remove(el)
            if len(list(md)) == 0 and not md.attrib:
                root.remove(md)
        return
    if md is None:
        md = ET.Element('metadata')
        placed = False
        for tag in ('uuid', 'title', 'description', 'name'):
            prev = root.find(tag)
            if prev is not None:
                root.insert(list(root).index(prev) + 1, md)
                placed = True
                break
        if not placed:
            root.insert(0, md)
        el = None
    if el is None:
        el = ET.SubElement(md, 'lwp_ipv4')
    el.text = ipv4


def _network_xml(net):
    return ET.fromstring(_run(['net-dumpxml', net], timeout=20))


def network_has_dhcp(net):
    '''True if this libvirt network runs DHCP (NAT `default`, not a LAN bridge).'''

    try:
        return _network_xml(net).find('.//dhcp') is not None
    except Exception:
        return False


def list_dhcp_networks(names=None):
    '''Libvirt networks that can take a static DHCP reservation.'''

    out = []
    for n in (names if names is not None else list_networks()):
        if network_has_dhcp(n):
            out.append(n)
    return out


def dhcp_host_ip(net, mac):
    '''Static DHCP mapping for this MAC, or empty.'''

    mac = _norm_mac(mac)
    if not mac:
        return ''
    try:
        root = _network_xml(net)
    except Exception:
        return ''
    for host in root.findall('.//dhcp/host'):
        if _norm_mac(host.get('mac')) == mac:
            return (host.get('ip') or '').strip()
    return ''


def _net_update_dhcp_host(net, command, fragment):
    args = ['net-update', net, command, 'ip-dhcp-host', fragment, '--config']
    try:
        return _run(args + ['--live'], timeout=30)
    except Exception:
        return _run(args, timeout=30)


def _dhcp_host_delete(net, mac):
    mac = _norm_mac(mac)
    if not mac or not dhcp_host_ip(net, mac):
        return
    try:
        _net_update_dhcp_host(net, 'delete', "<host mac='%s'/>" % mac)
    except Exception:
        pass


def _dhcp_host_upsert(net, mac, ip, hostname):
    mac = _norm_mac(mac)
    hostname = hostname or 'guest'
    fragment = "<host mac='%s' name='%s' ip='%s'/>" % (mac, hostname, ip)
    if dhcp_host_ip(net, mac):
        _net_update_dhcp_host(net, 'modify', fragment)
        return
    try:
        _net_update_dhcp_host(net, 'add-last', fragment)
    except Exception:
        _net_update_dhcp_host(net, 'modify', fragment)


def _iface_mac_net(root):
    iface = root.find('./devices/interface')
    if iface is None:
        return '', '', ''
    mac_el = iface.find('mac')
    src = iface.find('source')
    mac = _norm_mac(mac_el.get('address') if mac_el is not None else '')
    net_type = iface.get('type') or ''
    net = ''
    if src is not None:
        net = src.get('network') or src.get('bridge') or ''
    return mac, net_type, net


def _sync_dhcp_ipv4(name, root, ipv4):
    '''DHCP reservation on libvirt networks that have DHCP; drop stale ones.'''

    mac, net_type, net = _iface_mac_net(root)
    addr = ipv4.split('/')[0] if ipv4 else ''
    if not mac:
        return
    for n in list_networks():
        if not network_has_dhcp(n):
            continue
        if n == net and net_type == 'network' and addr:
            _dhcp_host_upsert(n, mac, addr, name)
        else:
            _dhcp_host_delete(n, mac)


def hypervisor_info():
    '''URI, disk dir, domain types, and `virsh version` lines.'''

    info = {
        'uri': connect_uri(),
        'disk': default_disk_dir(),
        'types': list_domain_types(),
        'library': '',
        'hypervisor': '',
        'raw': '',
    }
    try:
        raw = _run(['version'], timeout=15).strip()
        info['raw'] = raw
        for line in raw.splitlines():
            if ':' not in line:
                continue
            key, val = line.split(':', 1)
            key = key.strip().lower()
            val = val.strip()
            if key == 'using library':
                info['library'] = val
            elif key == 'running hypervisor':
                info['hypervisor'] = val
    except Exception:
        pass
    return info


def host_validate():
    '''
    virt-host-validate qemu. Each item is
    {label, status, detail} with status pass/fail/warn.
    Empty list if the binary is missing.
    '''

    cmd = ['virt-host-validate', 'qemu']
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, universal_newlines=True, timeout=30)
    except FileNotFoundError:
        return []
    except subprocess.CalledProcessError as e:
        out = e.output or ''
    except Exception:
        return []
    rows = []
    line_re = re.compile(
        r'^(?:\s*\w+:\s*)?(Checking\s+.+?)\s*:\s*(PASS|FAIL|WARN)\s*(?:\((.*)\))?\s*$',
        re.I)
    for line in (out or '').splitlines():
        m = line_re.match(line.strip())
        if not m:
            continue
        rows.append({
            'label': m.group(1).strip(),
            'status': m.group(2).lower(),
            'detail': (m.group(3) or '').strip(),
        })
    return rows


def list_domain_types():
    '''Hypervisor domain types from `virsh capabilities` (kvm, qemu, …).'''

    types = []
    try:
        out = _run(['capabilities'], timeout=20)
        root = ET.fromstring(out)
        for el in root.findall('.//guest/arch/domain'):
            t = (el.get('type') or '').strip()
            if t and t not in types:
                types.append(t)
    except Exception:
        types = []
    if not types:
        types = ['kvm', 'qemu']
    if 'kvm' in types:
        types.remove('kvm')
        types.insert(0, 'kvm')
    return types


def list_boot_devices():
    '''Libvirt <boot dev=…> values the form can set.'''

    return [
        ('hd', 'Disk'),
        ('cdrom', 'CD-ROM / ISO'),
        ('network', 'Network (PXE)'),
        ('fd', 'Floppy'),
    ]


def list_cpu_models(arch='x86_64'):
    '''Named CPU models from `virsh cpu-models` for Simulate.'''

    arch = (arch or 'x86_64').strip()
    if not re.match(r'^[A-Za-z0-9_]+$', arch):
        arch = 'x86_64'
    try:
        out = _run(['cpu-models', arch], timeout=20)
    except Exception:
        return []
    models = []
    for line in out.splitlines():
        name = line.strip()
        if name and not name.lower().startswith('cpu '):
            models.append(name)
    return models


def default_disk_dir():
    '''[vm] disk, else the default storage pool, else /var/lib/libvirt/images.'''

    if _DISK_DIR:
        return _DISK_DIR
    try:
        out = _run(['pool-dumpxml', 'default'], timeout=20)
        root = ET.fromstring(out)
        path_el = root.find('./target/path')
        if path_el is not None and (path_el.text or '').strip():
            return path_el.text.strip()
    except Exception:
        pass
    return '/var/lib/libvirt/images'


def suggested_disk_path(name):
    '''{disk_dir}/{name}/{name}.qcow2'''

    name = (name or 'vm').strip() or 'vm'
    return os.path.join(default_disk_dir(), name, '%s.qcow2' % name)


def _is_disk_file(path):
    lower = (path or '').lower()
    return lower.endswith(('.qcow2', '.qcow', '.raw', '.img', '.vmdk'))


def resolve_new_disk_path(name, disk_path=''):
    '''Full qcow2 path: explicit file, a directory, or the config default.'''

    name = _need_name(name)
    disk_path = (disk_path or '').strip()
    if disk_path and _is_disk_file(disk_path):
        return disk_path
    if disk_path:
        return os.path.join(disk_path, name, '%s.qcow2' % name)
    return suggested_disk_path(name)


def _set_title(root, title):
    el = root.find('title')
    title = (title or '').strip()
    if not title:
        if el is not None:
            root.remove(el)
        return
    if el is None:
        el = ET.Element('title')
        name_el = root.find('name')
        if name_el is not None:
            children = list(root)
            root.insert(children.index(name_el) + 1, el)
        else:
            root.insert(0, el)
    el.text = title


def _patch_nic(root, net_type, net_link, hwaddr, flags):
    devices = root.find('devices')
    if devices is None:
        devices = ET.SubElement(root, 'devices')
    nics = devices.findall('interface')
    if flags == 'down' and not nics:
        return
    if not nics:
        iface = ET.SubElement(devices, 'interface')
        nics = [iface]
    iface = nics[0]
    if net_type in ('network', 'bridge'):
        iface.set('type', net_type)
        src = iface.find('source')
        if src is None:
            src = ET.SubElement(iface, 'source')
        src.attrib.pop('network', None)
        src.attrib.pop('bridge', None)
        if net_link:
            if net_type == 'bridge':
                src.set('bridge', net_link)
            else:
                src.set('network', net_link)
    if hwaddr:
        mac_el = iface.find('mac')
        if mac_el is None:
            mac_el = ET.SubElement(iface, 'mac')
        mac_el.set('address', hwaddr)
    model = iface.find('model')
    if model is None:
        model = ET.SubElement(iface, 'model')
        model.set('type', 'virtio')
    link_el = iface.find('link')
    if flags == 'down':
        if link_el is None:
            link_el = ET.SubElement(iface, 'link')
        link_el.set('state', 'down')
    elif link_el is not None:
        iface.remove(link_el)


def _patch_disk(root, disk_path):
    for disk in root.findall('./devices/disk'):
        if disk.get('device') not in (None, 'disk'):
            continue
        source = disk.find('source')
        if source is None:
            source = ET.SubElement(disk, 'source')
        if source.get('dev') and not source.get('file'):
            return
        source.set('file', disk_path)
        return


def _set_domain_type(root, virt_type):
    virt_type = (virt_type or '').strip()
    if virt_type:
        root.set('type', virt_type)


def _normalize_boot(boot):
    allowed = {k for k, _ in list_boot_devices()}
    out = []
    for dev in boot or []:
        dev = (dev or '').strip()
        if dev in allowed and dev not in out:
            out.append(dev)
    return out or ['hd']


def _patch_boot(root, boot):
    os_el = root.find('os')
    if os_el is None:
        os_el = ET.SubElement(root, 'os')
    for el in list(os_el.findall('boot')):
        os_el.remove(el)
    type_el = os_el.find('type')
    children = list(os_el)
    idx = children.index(type_el) + 1 if type_el is not None else 0
    for i, dev in enumerate(_normalize_boot(boot)):
        el = ET.Element('boot')
        el.set('dev', dev)
        os_el.insert(idx + i, el)


def _next_cdrom_target(devices):
    used = set()
    for disk in devices.findall('disk'):
        tgt = disk.find('target')
        if tgt is not None and tgt.get('dev'):
            used.add(tgt.get('dev'))
    for letter in 'abcdefghijklmnopqrstuvwxyz':
        dev = 'sd' + letter
        if dev not in used:
            return dev
    return 'sdd'


def _patch_cdrom(root, iso_path):
    devices = root.find('devices')
    if devices is None:
        devices = ET.SubElement(root, 'devices')
    cdroms = [d for d in devices.findall('disk') if d.get('device') == 'cdrom']
    iso_path = (iso_path or '').strip()
    if not iso_path:
        for disk in cdroms:
            src = disk.find('source')
            if src is not None:
                disk.remove(src)
        return
    if cdroms:
        disk = cdroms[0]
    else:
        disk = ET.SubElement(devices, 'disk')
        disk.set('type', 'file')
        disk.set('device', 'cdrom')
        drv = ET.SubElement(disk, 'driver')
        drv.set('name', 'qemu')
        drv.set('type', 'raw')
        tgt = ET.SubElement(disk, 'target')
        tgt.set('dev', _next_cdrom_target(devices))
        tgt.set('bus', 'sata')
        ET.SubElement(disk, 'readonly')
    disk.set('type', 'file')
    src = disk.find('source')
    if src is None:
        src = ET.SubElement(disk, 'source')
    src.set('file', iso_path)


def _normalize_cpu(style, model='', require_known=False):
    '''Host = host-passthrough. Simulate = a named model from virsh cpu-models.'''

    style = 'simulate' if (style or '').strip() == 'simulate' else 'host'
    model = (model or '').strip()
    if style != 'simulate':
        return 'host', ''
    if not model:
        raise ValueError('Pick a CPU model to simulate.')
    if not re.match(r'^[A-Za-z0-9._+-]+$', model):
        raise ValueError('Invalid CPU model.')
    if require_known:
        known = list_cpu_models()
        if known and model not in known:
            raise ValueError('Unknown CPU model %s.' % model)
    return style, model


def _patch_cpu(root, style, model=''):
    style, model = _normalize_cpu(style, model)
    cpu = root.find('cpu')
    if cpu is None:
        cpu = ET.Element('cpu')
        vcpu = root.find('vcpu')
        children = list(root)
        idx = children.index(vcpu) + 1 if vcpu is not None else 0
        root.insert(idx, cpu)
    else:
        for key in list(cpu.attrib):
            del cpu.attrib[key]
        for child in list(cpu):
            cpu.remove(child)
    if style == 'simulate':
        cpu.set('mode', 'custom')
        cpu.set('match', 'exact')
        el = ET.SubElement(cpu, 'model')
        el.set('fallback', 'allow')
        el.text = model
    else:
        cpu.set('mode', 'host-passthrough')
        cpu.set('check', 'none')
        cpu.set('migratable', 'on')


def apply_settings(name, memory_mb=None, vcpus=None, autostart=None,
                   title=None, net_type=None, net_link=None, hwaddr=None,
                   net_flags=None, disk_path=None, virt_type=None,
                   boot=None, iso=None, cpu_style=None, cpu_model=None,
                   ipv4=None):
    '''Write the Edit form into domain XML / virsh setmem, setvcpus, autostart.'''

    name = _need_name(name)
    xml_text = dumpxml(name)
    root = ET.fromstring(xml_text)
    patch_xml = False
    if title is not None:
        _set_title(root, title)
        patch_xml = True
    if virt_type:
        _set_domain_type(root, virt_type)
        patch_xml = True
    if boot is not None:
        _patch_boot(root, boot)
        patch_xml = True
    if iso is not None:
        _patch_cdrom(root, iso)
        patch_xml = True
    if cpu_style is not None:
        _patch_cpu(root, cpu_style, cpu_model or '')
        patch_xml = True
    if any(v is not None for v in (net_type, net_link, hwaddr, net_flags)):
        _patch_nic(root, net_type, net_link, hwaddr, net_flags)
        patch_xml = True
    if disk_path:
        _patch_disk(root, disk_path)
        patch_xml = True
    if ipv4 is not None:
        ipv4 = _normalize_ipv4(ipv4)
        _patch_meta_ipv4(root, ipv4)
        patch_xml = True
    if patch_xml:
        define_xml(ET.tostring(root, encoding='unicode'), backup_name=name)
    if ipv4 is not None:
        _sync_dhcp_ipv4(name, ET.fromstring(dumpxml(name)), ipv4)
    if memory_mb is not None:
        set_memory_mb(name, memory_mb)
    if vcpus is not None:
        set_vcpus(name, vcpus)
    if autostart is not None:
        set_autostart(name, autostart)


def create_from_preset(name, memory_mb=2048, vcpus=2, disk_gb=20,
                       disk_path='', net_type='network', net_link='default',
                       autostart=False, virt_type='kvm', boot=None, iso='',
                       cpu_style='host', cpu_model='', ipv4=''):
    '''New qcow2 disk + virt-install XML, then virsh define (not started).'''

    name = _need_name(name)
    if exists(name):
        raise ContainerAlreadyExists('Domain %s already exists.' % name)
    memory_mb = int(memory_mb)
    vcpus = int(vcpus)
    disk_gb = int(disk_gb)
    if memory_mb < 16:
        raise ValueError('Memory must be at least 16 MB.')
    if vcpus < 1:
        raise ValueError('vCPUs must be at least 1.')
    if disk_gb < 1 or disk_gb > 4096:
        raise ValueError('Disk size must be between 1 and 4096 GB.')
    net_type = (net_type or 'network').strip()
    net_link = (net_link or 'default').strip()
    if net_type not in ('network', 'bridge'):
        raise ValueError('Network type must be network or bridge.')
    if not _RE_NAME.match(net_link):
        raise ValueError('Invalid network link.')
    known = list_link_names(net_type)
    if known and net_link not in known:
        raise ValueError('Unknown %s %s.' % (
            'bridge' if net_type == 'bridge' else 'network', net_link))
    types = list_domain_types()
    virt_type = (virt_type or 'kvm').strip()
    if virt_type not in types:
        raise ValueError('Unsupported domain type %s.' % virt_type)
    boot = _normalize_boot(boot)
    iso = (iso or '').strip()
    if iso and not os.path.isfile(iso):
        raise ValueError('ISO file does not exist: %s' % iso)
    if iso and 'cdrom' not in boot:
        boot = ['cdrom'] + [b for b in boot if b != 'cdrom']
    cpu_style, cpu_model = _normalize_cpu(cpu_style, cpu_model, require_known=True)
    ipv4 = _normalize_ipv4(ipv4)
    path = resolve_new_disk_path(name, disk_path)
    if os.path.exists(path):
        raise ContainerAlreadyExists('Disk file %s already exists.' % path)
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as e:
        raise ValueError('Cannot create disk directory %s: %s' % (directory, e))
    img_cmd = ['qemu-img', 'create', '-f', 'qcow2', path, '%sG' % disk_gb]
    try:
        out = subprocess.check_output(
            img_cmd, stderr=subprocess.STDOUT, universal_newlines=True,
            timeout=120)
        _log_cmd(img_cmd, 0, out)
    except subprocess.CalledProcessError as e:
        _log_cmd(img_cmd, e.returncode, e.output)
        raise
    if net_type == 'bridge':
        net_arg = 'bridge=%s,model=virtio' % net_link
    else:
        net_arg = 'network=%s,model=virtio' % net_link
    inst = [
        'virt-install', '--connect', _URI,
        '--name', name,
        '--memory', str(memory_mb),
        '--vcpus', str(vcpus),
        '--virt-type', virt_type,
        '--cpu', 'host-passthrough' if cpu_style == 'host' else cpu_model,
        '--disk', 'path=%s,format=qcow2,bus=virtio' % path,
        '--network', net_arg,
        '--graphics', 'none',
        '--console', 'pty',
        '--os-variant', 'generic',
        '--boot', 'hd',
        '--print-xml', '--dry-run',
    ]
    try:
        xml_text = subprocess.check_output(
            inst, stderr=subprocess.STDOUT, universal_newlines=True,
            timeout=60)
        _log_cmd(inst, 0, 'print-xml')
    except subprocess.CalledProcessError as e:
        _log_cmd(inst, e.returncode, e.output)
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    try:
        idx = xml_text.find('<domain')
        if idx < 0:
            raise ValueError('virt-install did not print domain XML.')
        xml_text = xml_text[idx:]
        root = ET.fromstring(xml_text)
        _set_domain_type(root, virt_type)
        _patch_boot(root, boot)
        _patch_cpu(root, cpu_style, cpu_model)
        if iso:
            _patch_cdrom(root, iso)
        create_from_xml(name, ET.tostring(root, encoding='unicode'))
        if ipv4:
            apply_settings(name, ipv4=ipv4)
        if autostart:
            set_autostart(name, True)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


def lxc_error_message(output):
    '''Alias so copied view code can call the same helper name.'''

    return virsh_error_message(output)
