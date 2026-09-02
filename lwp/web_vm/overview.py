# Overview for libvirt VMs (/virsh).

import libvirtlite as virt
import lwp
import lwp.virt as vhelp
from lwp.web.helpers import console_ok
from flask import current_app, flash, jsonify, render_template, session


def _overview_row(name, running=False):
    error = ''
    try:
        inf = virt.info(name)
    except Exception as e:
        inf = {'state': 'BROKEN', 'pid': '0', 'error': str(e), 'links': []}
    error = inf.get('error') or ''

    try:
        settings = virt.parse_settings(name)
    except Exception as e:
        settings = {
            'utsname': name, 'arch': '', 'auto': False, 'memlimit': 0,
            'vcpus': 0, 'flags': 'down', 'ipv4_addrs': [], 'ipv6_addrs': [],
            'config_error': str(e), 'nics': [], 'disks': [],
        }
        error = error or str(e)

    memusg = 0
    diskusg = 0
    snaps = []
    live = vhelp.empty_live_metrics()
    try:
        diskusg = virt.disk_usage_mb(name)
    except Exception:
        diskusg = 0
    if inf.get('state') != 'BROKEN':
        try:
            if inf.get('state') in ('RUNNING', 'FROZEN'):
                memusg = vhelp.memory_usage(name, known_live=True)
        except Exception:
            memusg = 0
        try:
            snaps = virt.snapshots(name)
        except Exception:
            snaps = []
        if inf.get('state') in ('RUNNING', 'FROZEN'):
            try:
                live = vhelp.vm_live_metrics(name)
            except Exception:
                live = vhelp.empty_live_metrics()
            if running:
                try:
                    addrs = virt.ip_addresses(name)
                    if addrs.get('ipv4') or addrs.get('ipv6'):
                        settings['ipv4_addrs'] = addrs.get('ipv4') or []
                        settings['ipv6_addrs'] = addrs.get('ipv6') or []
                        settings['ipv4'] = ' '.join(settings['ipv4_addrs'])
                        settings['ipv6'] = ' '.join(settings['ipv6_addrs'])
                except Exception:
                    pass
        else:
            vhelp.forget_live_sample(name)

    row = {
        'name': name,
        'memusg': memusg,
        'diskusg': diskusg,
        'settings': settings,
        'snapshots': snaps,
        'error': error,
    }
    row.update(live)
    return row


def _overview_groups(listx):
    containers_all = []
    for status in ['RUNNING', 'FROZEN', 'STOPPED', 'BROKEN']:
        rows = []
        running = (status == 'RUNNING')
        for name in listx.get(status, []):
            try:
                rows.append(_overview_row(name, running))
            except Exception as e:
                rows.append({
                    'name': name,
                    'memusg': 0,
                    'diskusg': 0,
                    'settings': {'utsname': name, 'ipv4_addrs': [],
                                 'ipv6_addrs': [], 'flags': 'down',
                                 'config_error': str(e)},
                    'snapshots': [],
                    'error': str(e),
                    **vhelp.empty_live_metrics(),
                })
        containers_all.append({
            'status': status.lower(),
            'containers': rows,
        })
    return containers_all


def home():
    if 'logged_in' in session:
        try:
            listx = virt.listx()
        except Exception as e:
            flash(u'Unable to list VMs: %s' % e, 'error')
            listx = {'RUNNING': [], 'FROZEN': [], 'STOPPED': [], 'BROKEN': []}
        containers_all = _overview_groups(listx)
        try:
            names = virt.ls()
        except Exception:
            names = []
        try:
            vm_networks = virt.list_networks()
        except Exception:
            vm_networks = []
        try:
            vm_bridges = virt.list_bridges()
        except Exception:
            vm_bridges = []
        try:
            vm_dhcp_networks = virt.list_dhcp_networks(vm_networks)
        except Exception:
            vm_dhcp_networks = []
        try:
            vm_disk_dir = virt.default_disk_dir()
        except Exception:
            vm_disk_dir = '/var/lib/libvirt/images'
        try:
            vm_domain_types = virt.list_domain_types()
        except Exception:
            vm_domain_types = ['kvm', 'qemu']
        try:
            vm_boot_devices = virt.list_boot_devices()
        except Exception:
            vm_boot_devices = [('hd', 'Disk'), ('cdrom', 'CD-ROM / ISO')]
        try:
            vm_cpu_models = virt.list_cpu_models()
        except Exception:
            vm_cpu_models = []
        try:
            host_memory = lwp.host_memory_usage()
        except Exception:
            host_memory = {'total': 4096}
        return render_template('index.html', containers=names,
                               containers_all=containers_all,
                               dist=lwp.check_ubuntu(),
                               templates=[],
                               images=[],
                               images_dir='',
                               lxc_conf=virt.connect_uri(),
                               lxcpath=virt.connect_uri(),
                               console_available=console_ok(),
                               overview_refresh=current_app.config.get(
                                   'OVERVIEW_REFRESH', 60),
                               vm_networks=vm_networks,
                               vm_bridges=vm_bridges,
                               vm_dhcp_networks=vm_dhcp_networks,
                               vm_disk_dir=vm_disk_dir,
                               vm_domain_types=vm_domain_types,
                               vm_boot_devices=vm_boot_devices,
                               vm_cpu_models=vm_cpu_models,
                               host_memory=host_memory)
    return render_template('login.html')


def refresh_memory_containers(name=None):
    if 'logged_in' in session:
        if name == 'containers':
            try:
                running = virt.running()
            except Exception:
                running = []
            containers = []
            for vm in running:
                try:
                    memusg = vhelp.memory_usage(vm)
                except Exception:
                    memusg = 0
                containers.append({'name': vm, 'memusg': memusg})
            return jsonify(data=containers)
        elif name == 'host':
            return jsonify(lwp.host_memory_usage())
        try:
            return jsonify({'memusg': vhelp.memory_usage(name)})
        except Exception:
            return jsonify({'memusg': 0})


def refresh_overview():
    if 'logged_in' not in session:
        return jsonify({}), 401
    try:
        listx = virt.listx()
    except Exception:
        listx = {'RUNNING': [], 'FROZEN': [], 'STOPPED': [], 'BROKEN': []}
    containers_all = _overview_groups(listx)
    return jsonify(
        counts=render_template('includes/overview_counts.html',
                               containers_all=containers_all),
        live=render_template('includes/overview_live.html',
                             containers_all=containers_all,
                             console_available=console_ok()),
    )


def register(app):
    '''Bind VM Overview and /virsh/_refresh_* AJAX URLs.'''

    app.add_url_rule('/virsh', view_func=home, endpoint='vm_home')
    app.add_url_rule('/virsh/_refresh_memory_<name>',
                     view_func=refresh_memory_containers,
                     endpoint='vm_refresh_memory')
    app.add_url_rule('/virsh/_refresh_overview', view_func=refresh_overview,
                     endpoint='vm_refresh_overview')
