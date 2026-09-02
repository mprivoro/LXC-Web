# Host-level libvirt settings: virtual networks and virt-host-validate.

import subprocess

import libvirtlite as virt
from flask import abort, flash, redirect, render_template, request, session, url_for


def _busy_vms():
    '''True if any domain is running or paused (stopping a net would drop them).'''

    try:
        grouped = virt.listx()
    except Exception:
        return False
    return bool(grouped.get('RUNNING') or grouped.get('FROZEN'))


def vm_net():
    '''List libvirt networks; start / stop / autostart. Does not edit XML.'''

    if 'logged_in' not in session:
        return render_template('login.html')
    if session.get('su') != 'Yes':
        return abort(403)

    if request.method == 'POST':
        if request.form.get('token') != session.get('token'):
            return abort(403)
        name = (request.form.get('name') or '').strip()
        action = (request.form.get('action') or '').strip()
        busy = _busy_vms()
        try:
            if action == 'start':
                virt.network_start(name)
                flash(u'Network %s started.' % name, 'success')
            elif action == 'stop':
                if busy:
                    flash(u'Shut off all VMs before stopping a network.',
                          'warning')
                else:
                    virt.network_stop(name)
                    flash(u'Network %s stopped (still defined).' % name,
                          'success')
            elif action == 'autostart_on':
                virt.network_set_autostart(name, True)
                flash(u'Autostart enabled for %s.' % name, 'success')
            elif action == 'autostart_off':
                virt.network_set_autostart(name, False)
                flash(u'Autostart disabled for %s.' % name, 'success')
            else:
                flash(u'Unknown action.', 'error')
        except subprocess.CalledProcessError as e:
            flash(u'%s' % virt.virsh_error_message(e.output), 'error')
        except Exception as e:
            flash(u'%s' % e, 'error')
        return redirect(url_for('vm_net'))

    try:
        networks = virt.list_network_details()
    except Exception as e:
        networks = []
        flash(u'Unable to list libvirt networks: %s' % e, 'error')
    try:
        bridges = virt.list_bridges()
    except Exception:
        bridges = []
    return render_template(
        'vm/net.html',
        networks=networks,
        bridges=bridges,
        busy=_busy_vms(),
        uri=virt.connect_uri())


def vm_checkconfig():
    '''virt-host-validate plus URI, disk dir, and hypervisor version.'''

    if 'logged_in' not in session:
        return render_template('login.html')
    if session.get('su') != 'Yes':
        return abort(403)

    try:
        info = virt.hypervisor_info()
    except Exception as e:
        info = {
            'uri': virt.connect_uri(), 'disk': '', 'types': [],
            'library': '', 'hypervisor': '', 'raw': str(e),
        }
    try:
        checks = virt.host_validate()
    except Exception:
        checks = []
    return render_template('vm/checkconfig.html', info=info, checks=checks)


def register(app):
    '''Bind /virsh/settings/net and /virsh/checkconfig.'''

    app.add_url_rule('/virsh/settings/net', view_func=vm_net, endpoint='vm_net',
                     methods=['GET', 'POST'])
    app.add_url_rule('/virsh/checkconfig', view_func=vm_checkconfig,
                     endpoint='vm_checkconfig')
