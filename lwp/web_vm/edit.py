# Edit one libvirt domain (form fields + XML) and the console page.

import libvirtlite as virt
import lwp
import os
from lwp.util import ok_vm_name
from lwp.web.helpers import console_ok
from flask import (abort, flash, redirect, render_template, request, session,
                   url_for)


def container_console(container=None):
    '''Full-page virsh console (opened in a separate window/tab).'''

    if 'logged_in' not in session:
        return render_template('login.html')
    if session.get('su') != 'Yes' or not console_ok():
        return abort(403)
    if not ok_vm_name(container):
        return abort(404)
    if not virt.exists(container):
        flash(u'VM %s does not exist!' % container, 'error')
        return redirect(url_for('vm_home'))
    return render_template('console.html', container=container)


def edit(container=None):
    '''GET: edit form. POST: save fields or the domain XML.'''

    if 'logged_in' not in session:
        return render_template('login.html')
    if not ok_vm_name(container) or not virt.exists(container):
        flash(u'VM %s does not exist!' % container, 'error')
        return redirect(url_for('vm_home'))

    host_memory = lwp.host_memory_usage()
    try:
        host_cpus = int(os.cpu_count() or 0)
    except Exception:
        host_cpus = 0
    if host_cpus < 1:
        host_cpus = 8

    if request.method == 'POST' and request.form.get('save_config'):
        if session.get('su') != 'Yes':
            return abort(403)
        if request.form.get('token') != session.get('token'):
            flash(u'Invalid token!', 'error')
        elif request.form.get('restore_bak'):
            ok, err = virt.restore_xml_backup(container)
            if ok:
                flash(u'XML backup restored for %s.' % container, 'success')
            else:
                flash(u'Unable to restore backup: %s' % err, 'error')
        else:
            try:
                virt.define_xml(request.form.get('raw_config', ''),
                                backup_name=container)
                flash(u'Domain XML saved for %s. Restart the VM if it is running.'
                      % container, 'success')
            except Exception as e:
                flash(u'Unable to define XML: %s' % e, 'error')

    elif request.method == 'POST' and session.get('su') == 'Yes':
        from lwp.util import IPV4_CIDR, RE_HWADDR, RE_IFACE, RE_ROOTFS, matches
        try:
            title = (request.form.get('hostname') or '').strip()
            net_type = (request.form.get('type') or '').strip()
            net_link = (request.form.get('link') or '').strip()
            hwaddr = (request.form.get('hwaddress') or '').strip()
            flags = request.form.get('flags') or 'down'
            disk_path = (request.form.get('disk_path') or '').strip()
            iso = (request.form.get('iso') or '').strip()
            virt_type = (request.form.get('virt_type') or '').strip()
            boot = request.form.getlist('boot')
            cpu_style = (request.form.get('cpu_style') or 'host').strip()
            cpu_model = (request.form.get('cpu_model') or '').strip()
            ipv4 = None
            if 'ipaddress' in request.form:
                ipv4 = (request.form.get('ipaddress') or '').strip()
                if ipv4.lower() == 'undefined':
                    ipv4 = ''
            mem = request.form.get('memlimit', '').strip()
            vcpus = request.form.get('vcpus', '').strip()
            if title and (len(title) > 80 or '<' in title or '>' in title):
                raise ValueError('Invalid title.')
            if net_type and net_type not in ('network', 'bridge'):
                raise ValueError('Network type must be network or bridge.')
            if net_link and not matches(RE_IFACE, net_link):
                raise ValueError('Invalid network link.')
            if net_type in ('network', 'bridge') and net_link:
                known = virt.list_link_names(net_type)
                if known and net_link not in known:
                    raise ValueError('Unknown %s %s.' % (
                        'bridge' if net_type == 'bridge' else 'network',
                        net_link))
            if hwaddr and not matches(RE_HWADDR, hwaddr):
                raise ValueError('Invalid MAC address.')
            if flags not in ('up', 'down'):
                flags = 'down'
            if disk_path and not matches(RE_ROOTFS, disk_path):
                raise ValueError('Invalid disk path.')
            if iso and not matches(RE_ROOTFS, iso):
                raise ValueError('Invalid ISO path.')
            if iso and not os.path.isfile(iso):
                raise ValueError('ISO file does not exist.')
            types = virt.list_domain_types()
            if virt_type and virt_type not in types:
                raise ValueError('Unsupported domain type %s.' % virt_type)
            if cpu_style not in ('host', 'simulate'):
                raise ValueError('CPU style must be Host or Simulate.')
            if ipv4 and not matches('^%s$' % IPV4_CIDR, ipv4):
                raise ValueError('Invalid IP address.')
            virt.apply_settings(
                container,
                memory_mb=int(mem) if mem.isdigit() else None,
                vcpus=int(vcpus) if vcpus.isdigit() else None,
                autostart=bool(request.form.get('autostart')),
                title=title,
                net_type=net_type or None,
                net_link=net_link or None,
                hwaddr=hwaddr or None,
                net_flags=flags,
                disk_path=disk_path or None,
                virt_type=virt_type or None,
                boot=boot,
                iso=iso,
                cpu_style=cpu_style,
                cpu_model=cpu_model,
                ipv4=ipv4)
            flash(u'Updated %s.' % container, 'success')
        except Exception as e:
            flash(u'Unable to update %s: %s' % (container, e), 'error')

    try:
        inf = virt.info(container)
    except Exception as e:
        inf = {'state': 'BROKEN', 'pid': '0', 'error': str(e)}
    infos = {
        'status': inf.get('state') or 'BROKEN',
        'pid': inf.get('pid') or '0',
        'error': inf.get('error') or '',
        'memusg': 0,
    }
    try:
        import lwp.virt as vhelp
        if infos['status'] in ('RUNNING', 'FROZEN'):
            infos['memusg'] = vhelp.memory_usage(container, known_live=True)
    except Exception:
        pass

    try:
        settings = virt.parse_settings(container)
        raw_config = virt.dumpxml(container)
        config_read_error = ''
    except Exception as e:
        settings = {'utsname': container, 'title': '', 'memlimit': 0, 'vcpus': 0,
                    'auto': False, 'arch': '', 'nics': [], 'disks': [],
                    'flags': 'down', 'type': 'network', 'link': '', 'hwaddr': '',
                    'domain_type': '', 'boot': [], 'iso': '',
                    'cpu_style': 'host', 'cpu_model': '', 'ipv4': '',
                    'config_error': str(e)}
        raw_config = ''
        config_read_error = str(e)

    if infos['status'] in ('RUNNING', 'FROZEN'):
        try:
            addrs = virt.ip_addresses(container)
            if addrs.get('ipv4'):
                settings['ipv4_addrs'] = addrs['ipv4']
            if addrs.get('ipv6'):
                settings['ipv6_addrs'] = addrs['ipv6']
        except Exception:
            pass

    try:
        snapshots = virt.snapshots(container)
        snap_plan = virt.snapshot_plan(container)
    except Exception:
        snapshots = []
        snap_plan = {'can': False, 'need_confirm': False, 'reason': ''}

    try:
        names = virt.ls()
    except Exception:
        names = []
    try:
        domain_types = virt.list_domain_types()
    except Exception:
        domain_types = ['kvm', 'qemu']
    if settings.get('domain_type') and settings['domain_type'] not in domain_types:
        domain_types.append(settings['domain_type'])
    try:
        boot_devices = virt.list_boot_devices()
    except Exception:
        boot_devices = [('hd', 'Disk'), ('cdrom', 'CD-ROM / ISO')]
    try:
        cpu_models = virt.list_cpu_models(settings.get('arch') or 'x86_64')
    except Exception:
        cpu_models = []
    current_model = settings.get('cpu_model') or ''
    if current_model and current_model not in cpu_models:
        cpu_models = [current_model] + cpu_models
    try:
        vm_networks = virt.list_networks()
    except Exception:
        vm_networks = []
    try:
        vm_bridges = virt.list_bridges()
    except Exception:
        vm_bridges = []
    current_link = settings.get('link') or ''
    if settings.get('type') == 'bridge':
        if current_link and current_link not in vm_bridges:
            vm_bridges = [current_link] + vm_bridges
    elif current_link and current_link not in vm_networks:
        vm_networks = [current_link] + vm_networks
    try:
        vm_dhcp_networks = virt.list_dhcp_networks(vm_networks)
    except Exception:
        vm_dhcp_networks = []
    net_types = []
    if vm_networks:
        net_types.append('network')
    if vm_bridges:
        net_types.append('bridge')
    if settings.get('type') and settings['type'] not in net_types:
        net_types.append(settings['type'])
    if not net_types:
        net_types = ['network']

    return render_template(
        'vm/edit.html', containers=names, container=container,
        infos=infos, settings=settings, host_memory=host_memory,
        host_cpus=host_cpus, domain_types=domain_types,
        boot_devices=boot_devices, cpu_models=cpu_models,
        net_types=net_types, vm_networks=vm_networks, vm_bridges=vm_bridges,
        vm_dhcp_networks=vm_dhcp_networks,
        snapshots=snapshots, snap_plan=snap_plan, raw_config=raw_config,
        config_path=virt.connect_uri() + ' ' + container,
        config_missing=not raw_config,
        config_read_error=config_read_error,
        config_has_bak=virt.xml_backup_exists(container),
        console_available=console_ok())


def register(app):
    '''Bind /virsh/<container>/edit and /virsh/<container>/console.'''

    app.add_url_rule('/virsh/<container>/console', view_func=container_console,
                     endpoint='vm_console')
    app.add_url_rule('/virsh/<container>/edit', view_func=edit, endpoint='vm_edit',
                     methods=['GET', 'POST'])
