# MCP tools for KVM/QEMU (virsh). Registered on the shared NoDeck MCP server.

from __future__ import annotations

import libvirtlite as virt
import lwp.virt as vhelp
from lwp.util import ok_vm_name
from lwp.mcp_server import (
    _DEST, _RO, _RW, _fail, _ok, _write_guard, ensure_runtime, mcp)


def _need(name):
    if not ok_vm_name(name):
        raise virt.ContainerDoesntExists('Invalid domain name.')
    if not virt.exists(name):
        raise virt.ContainerDoesntExists('Domain %s does not exist.' % name)
    return name


def _brief(name, state):
    row = {'name': name, 'state': state}
    try:
        cfg = virt.parse_settings(name)
    except Exception:
        cfg = {}
    row['uuid'] = cfg.get('uuid') or ''
    row['vcpus'] = cfg.get('vcpus') or 0
    row['memory_mb'] = cfg.get('memlimit') or 0
    row['ipv4'] = cfg.get('ipv4_addrs') or []
    row['ipv6'] = cfg.get('ipv6_addrs') or []
    if state == 'RUNNING':
        try:
            addrs = virt.ip_addresses(name)
            row['ipv4'] = addrs.get('ipv4') or []
            row['ipv6'] = addrs.get('ipv6') or []
        except Exception:
            pass
    return row


@mcp.tool(annotations=_RO)
def list_vms() -> dict:
    '''List KVM/QEMU domains grouped by state.'''

    ensure_runtime()
    try:
        grouped = virt.listx()
    except Exception as e:
        return _fail(e)
    vms = []
    for state in ('RUNNING', 'FROZEN', 'STOPPED', 'BROKEN'):
        for name in grouped.get(state, []):
            try:
                vms.append(_brief(name, state))
            except Exception as e:
                vms.append({'name': name, 'state': state, 'error': str(e)})
    return _ok(
        uri=virt.connect_uri(),
        counts={k.lower(): len(grouped.get(k, []))
                for k in ('RUNNING', 'FROZEN', 'STOPPED', 'BROKEN')},
        vms=vms)


@mcp.tool(annotations=_RO)
def vm_info(name: str) -> dict:
    '''State, memory, disks, NICs, snapshots, live CPU/net if running.'''

    ensure_runtime()
    try:
        name = _need(name)
        inf = virt.info(name)
        settings = virt.parse_settings(name)
        state = inf.get('state', '')
        live = vhelp.empty_live_metrics()
        mem = virt.disk_usage_mb(name)
        ram = 0
        snaps = []
        addrs = {'ipv4': [], 'ipv6': []}
        if state != 'BROKEN':
            try:
                ram = vhelp.memory_usage(
                    name, known_live=(state in ('RUNNING', 'FROZEN')))
            except Exception:
                ram = 0
            try:
                snaps = virt.snapshots(name)
            except Exception:
                snaps = []
            if state in ('RUNNING', 'FROZEN'):
                try:
                    live = vhelp.vm_live_metrics(name)
                except Exception:
                    live = vhelp.empty_live_metrics()
                try:
                    addrs = virt.ip_addresses(name)
                except Exception:
                    pass
        return _ok(
            name=name,
            state=state,
            id=inf.get('pid') or '0',
            uuid=settings.get('uuid') or inf.get('uuid') or '',
            vcpus=settings.get('vcpus') or inf.get('vcpus') or 0,
            memory_mb=ram or settings.get('memlimit') or 0,
            disk_mb=mem,
            autostart=bool(settings.get('auto')),
            nics=settings.get('nics') or [],
            disks=settings.get('disks') or [],
            cpu_style=settings.get('cpu_style') or 'host',
            cpu_model=settings.get('cpu_model') or '',
            ipv4=addrs.get('ipv4') or [],
            ipv6=addrs.get('ipv6') or [],
            live={
                'cpu': live.get('cpu_label') or None,
                'net_rx': live.get('net_rx_label') or None,
                'net_tx': live.get('net_tx_label') or None,
            },
            snapshots=[{'name': s.get('name'), 'created': s.get('created'),
                        'comment': s.get('comment')} for s in snaps],
        )
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RO)
def read_xml(name: str) -> dict:
    '''Domain XML (virsh dumpxml).'''

    ensure_runtime()
    try:
        name = _need(name)
        return _ok(name=name, uri=virt.connect_uri(), text=virt.dumpxml(name))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_DEST)
@_write_guard
def write_xml(name: str, text: str) -> dict:
    '''virsh define this XML (backup kept). Does not restart the VM.'''

    ensure_runtime()
    try:
        name = _need(name)
        virt.define_xml(text, backup_name=name)
        return _ok(name=name)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def set_vm(name: str, memory_mb: int = 0, vcpus: int = 0,
           autostart: bool | None = None) -> dict:
    '''Set memory (MB), vCPUs, and/or autostart. Zero/None leaves a field.'''

    ensure_runtime()
    try:
        name = _need(name)
        if memory_mb:
            virt.set_memory_mb(name, memory_mb)
        if vcpus:
            virt.set_vcpus(name, vcpus)
        if autostart is not None:
            virt.set_autostart(name, bool(autostart))
        return _ok(name=name, settings=virt.parse_settings(name))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def start_vm(name: str) -> dict:
    '''Start a shut-off domain, or resume a paused one.'''

    ensure_runtime()
    try:
        name = _need(name)
        virt.start(name)
        return _ok(name=name, state=virt.info(name).get('state'))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def stop_vm(name: str) -> dict:
    '''ACPI shutdown (virsh shutdown).'''

    ensure_runtime()
    try:
        name = _need(name)
        virt.stop(name)
        return _ok(name=name, state=virt.info(name).get('state'))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def force_stop_vm(name: str) -> dict:
    '''Immediate power off (virsh destroy). Does not undefine.'''

    ensure_runtime()
    try:
        name = _need(name)
        virt.force_stop(name)
        return _ok(name=name, state=virt.info(name).get('state'))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def reboot_vm(name: str) -> dict:
    '''virsh reboot, or start if shut off.'''

    ensure_runtime()
    try:
        name = _need(name)
        virt.reboot(name)
        return _ok(name=name, state=virt.info(name).get('state'))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def pause_vm(name: str) -> dict:
    '''virsh suspend.'''

    ensure_runtime()
    try:
        name = _need(name)
        virt.freeze(name)
        return _ok(name=name, state=virt.info(name).get('state'))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def resume_vm(name: str) -> dict:
    '''virsh resume.'''

    ensure_runtime()
    try:
        name = _need(name)
        virt.unfreeze(name)
        return _ok(name=name, state=virt.info(name).get('state'))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RO)
def list_vm_snapshots(name: str) -> dict:
    '''List libvirt snapshots of a domain.'''

    ensure_runtime()
    try:
        name = _need(name)
        snaps = virt.snapshots(name)
        return _ok(name=name, snapshots=snaps, plan=virt.snapshot_plan(name))
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def create_vm_snapshot(name: str, comment: str = '',
                       allow_running: bool = False) -> dict:
    '''Create a libvirt snapshot. Live snapshot of a running VM needs allow_running.'''

    ensure_runtime()
    try:
        name = _need(name)
        snap = virt.snapshot_create(name, comment or None,
                                    allow_running=allow_running)
        return _ok(name=name, snapshot=snap)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_DEST)
@_write_guard
def restore_vm_snapshot(name: str, snapshot: str) -> dict:
    '''Revert in place. VM must be shut off.'''

    ensure_runtime()
    try:
        name = _need(name)
        virt.snapshot_restore(name, snapshot)
        return _ok(name=name, snapshot=snapshot)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_DEST)
@_write_guard
def destroy_vm_snapshot(name: str, snapshot: str) -> dict:
    '''Delete a libvirt snapshot.'''

    ensure_runtime()
    try:
        name = _need(name)
        virt.snapshot_destroy(name, snapshot)
        return _ok(name=name, snapshot=snapshot)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def clone_vm(name: str, new_name: str) -> dict:
    '''virt-clone --auto-clone.'''

    ensure_runtime()
    try:
        orig = _need(name)
        if not ok_vm_name(new_name):
            return {'ok': False, 'error': 'Invalid new_name.'}
        virt.clone(orig=orig, new=new_name)
        return _ok(name=new_name, cloned_from=orig)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_RW)
@_write_guard
def define_vm(name: str, xml: str) -> dict:
    '''Define a new domain from XML.'''

    ensure_runtime()
    try:
        if not ok_vm_name(name):
            return {'ok': False, 'error': 'Invalid name.'}
        virt.create_from_xml(name, xml)
        return _ok(name=name)
    except Exception as e:
        return _fail(e)


@mcp.tool(annotations=_DEST)
@_write_guard
def destroy_vm(name: str, confirm_name: str,
               remove_storage: bool = False) -> dict:
    '''Undefine. confirm_name must match. Disks kept unless remove_storage.'''

    ensure_runtime()
    try:
        name = _need(name)
        if confirm_name != name:
            return {'ok': False, 'error': 'confirm_name must match name.'}
        virt.destroy(name, remove_storage=remove_storage)
        vhelp.forget_live_sample(name)
        return _ok(name=name, destroyed=True, storage_removed=remove_storage)
    except Exception as e:
        return _fail(e)
