# VM start/stop/pause, undefine, snapshots, define-from-XML, clone.

import subprocess
import time

import libvirtlite as virt
from lwp.util import ok_vm_name
from flask import (abort, flash, jsonify, redirect, render_template, request,
                   session, url_for)


def action():
    '''GET /virsh/action: start, stop, pause, resume, undefine, snapshot restore/delete.'''

    if 'logged_in' not in session:
        return render_template('login.html')
    name = request.args.get('name', '')
    if request.args.get('token') == session.get('token'):
        action_name = request.args['action']
        if action_name == 'start':
            try:
                virt.start(name)
                time.sleep(1)
                flash(u'VM %s started successfully!' % name, 'success')
            except virt.ContainerAlreadyRunning:
                flash(u'VM %s is already running!' % name, 'error')
            except subprocess.CalledProcessError as e:
                flash(u'Unable to start %s: %s' %
                      (name, virt.virsh_error_message(e.output)), 'error')
        elif action_name == 'stop':
            try:
                virt.stop(name)
                flash(u'VM %s is shutting down.' % name, 'success')
            except virt.ContainerNotRunning:
                flash(u'VM %s is already shut off!' % name, 'error')
            except subprocess.CalledProcessError as e:
                flash(u'Unable to shut down %s: %s' %
                      (name, virt.virsh_error_message(e.output)), 'error')
        elif action_name == 'force_stop':
            try:
                virt.force_stop(name)
                flash(u'VM %s powered off.' % name, 'success')
            except virt.ContainerNotRunning:
                flash(u'VM %s is already shut off!' % name, 'error')
            except subprocess.CalledProcessError as e:
                flash(u'Unable to power off %s: %s' %
                      (name, virt.virsh_error_message(e.output)), 'error')
        elif action_name == 'freeze':
            try:
                virt.freeze(name)
                flash(u'VM %s paused.' % name, 'success')
            except virt.ContainerNotRunning:
                flash(u'VM %s is not running!' % name, 'error')
            except subprocess.CalledProcessError as e:
                flash(u'Unable to pause %s: %s' %
                      (name, virt.virsh_error_message(e.output)), 'error')
        elif action_name == 'unfreeze':
            try:
                virt.unfreeze(name)
                flash(u'VM %s resumed.' % name, 'success')
            except virt.ContainerNotRunning:
                flash(u'VM %s is not paused!' % name, 'error')
            except subprocess.CalledProcessError as e:
                flash(u'Unable to resume %s: %s' %
                      (name, virt.virsh_error_message(e.output)), 'error')
        elif action_name == 'destroy':
            if session['su'] != 'Yes':
                return abort(403)
            wipe = request.args.get('wipe') == '1'
            try:
                virt.destroy(name, remove_storage=wipe)
                if wipe:
                    flash(u'VM %s undefined and disks removed.' % name,
                          'success')
                else:
                    flash(u'VM %s undefined (disks kept).' % name, 'success')
            except virt.ContainerDoesntExists:
                flash(u'VM %s does not exist!' % name, 'error')
            except subprocess.CalledProcessError as e:
                flash(u'Unable to undefine %s: %s' %
                      (name, virt.virsh_error_message(e.output)), 'error')
        elif action_name == 'destroy_snapshot':
            if session['su'] != 'Yes':
                return abort(403)
            snap = request.args.get('snap', '')
            try:
                virt.snapshot_destroy(name, snap)
                flash(u'Snapshot %s of %s deleted.' % (snap, name), 'success')
            except (virt.InvalidSnapshot, virt.SnapshotDoesntExists,
                    virt.ContainerDoesntExists) as e:
                flash(u'%s' % e, 'error')
            except subprocess.CalledProcessError as e:
                flash(u'Unable to delete snapshot: %s' %
                      virt.virsh_error_message(e.output), 'error')
        elif action_name == 'restore_snapshot':
            if session['su'] != 'Yes':
                return abort(403)
            snap = request.args.get('snap', '')
            newname = (request.args.get('newname') or '').strip()
            try:
                virt.snapshot_restore(name, snap, newname or None)
                flash(u'Snapshot %s restored into %s.' % (snap, name),
                      'success')
            except (virt.InvalidSnapshot, virt.SnapshotDoesntExists,
                    virt.ContainerDoesntExists, virt.ContainerAlreadyRunning,
                    virt.SnapshotNotPossible) as e:
                flash(u'%s' % e, 'error')
            except subprocess.CalledProcessError as e:
                flash(u'Unable to restore snapshot: %s' %
                      virt.virsh_error_message(e.output), 'error')
        elif action_name == 'reboot' and name == 'host':
            if session['su'] != 'Yes':
                return abort(403)
            try:
                subprocess.check_output(
                    ['/sbin/shutdown', '-r', 'now',
                     'Reboot from NoDeck panel'],
                    stderr=subprocess.STDOUT, universal_newlines=True)
                flash(u'System will now restart!', 'success')
            except subprocess.CalledProcessError as e:
                flash(u'System error: %s' % e.output, 'error')
    if request.args.get('from') == 'edit' and name:
        return redirect(url_for('vm_edit', container=name))
    return redirect(url_for('vm_home'))


def bulk_action():
    '''POST: start or stop the selected VMs (skips broken / already there).'''

    if 'logged_in' not in session:
        return render_template('login.html')
    if request.form.get('token') != session.get('token'):
        return abort(403)
    action_name = request.form.get('action', '')
    if action_name not in ('start', 'stop'):
        flash(u'Invalid bulk action!', 'error')
        return redirect(url_for('vm_home'))
    names = request.form.getlist('name')
    ok = []
    failed = []
    for name in names:
        if not ok_vm_name(name):
            failed.append('%s (invalid name)' % name)
            continue
        try:
            state = virt.info(name).get('state', '')
        except virt.ContainerDoesntExists:
            failed.append('%s (does not exist)' % name)
            continue
        except Exception as e:
            failed.append('%s (%s)' % (name, e))
            continue
        if state == 'BROKEN':
            failed.append('%s (broken)' % name)
            continue
        try:
            if action_name == 'start':
                if state == 'FROZEN':
                    virt.unfreeze(name)
                elif state != 'RUNNING':
                    virt.start(name)
            else:
                virt.stop(name)
            ok.append(name)
        except (virt.ContainerAlreadyRunning, virt.ContainerNotRunning):
            pass
        except subprocess.CalledProcessError as e:
            failed.append('%s (%s)' % (name, virt.virsh_error_message(e.output)))
        except Exception as e:
            failed.append('%s (%s)' % (name, e))
    if ok:
        flash(u'%s: %s' % (action_name.capitalize(), ', '.join(ok)), 'success')
    if failed:
        flash(u'Failed: %s' % '; '.join(failed), 'error')
    return redirect(url_for('vm_home'))


def take_snapshot():
    '''Create a libvirt snapshot of a domain.'''

    if 'logged_in' not in session:
        return render_template('login.html')
    if session['su'] != 'Yes':
        return abort(403)
    if request.form.get('token') != session.get('token'):
        return abort(403)
    name = request.form.get('name', '')
    comment = (request.form.get('comment') or '')[:2000]
    allow_running = request.form.get('allow_running') == '1'
    try:
        snap = virt.snapshot_create(name, comment, allow_running=allow_running)
        snap_name = snap.get('name') if isinstance(snap, dict) else snap
        flash(u'Snapshot %s of %s created.' % (snap_name, name), 'success')
    except virt.ContainerDoesntExists:
        flash(u'VM %s does not exist!' % name, 'error')
    except (virt.SnapshotNotPossible, virt.SnapshotNeedsConfirm) as e:
        flash(u'%s' % e, 'warning')
    except subprocess.CalledProcessError as e:
        flash(u'Unable to snapshot %s: %s' %
              (name, virt.virsh_error_message(e.output)), 'error')
    if request.form.get('from') == 'edit':
        return redirect(url_for('vm_edit', container=name))
    return redirect(url_for('vm_home'))


def create_container():
    '''POST: define a VM from form presets or pasted domain XML.'''

    if 'logged_in' not in session:
        return render_template('login.html')
    if session['su'] != 'Yes':
        return abort(403)
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        xml_text = (request.form.get('xml') or '').strip()
        if not ok_vm_name(name):
            flash(u'Invalid VM name.', 'error')
        elif xml_text:
            try:
                virt.create_from_xml(name, xml_text)
                flash(u'VM %s defined from XML.' % name, 'success')
            except virt.ContainerAlreadyExists:
                flash(u'VM %s already exists!' % name, 'error')
            except Exception as e:
                flash(u'Unable to define %s: %s' % (name, e), 'error')
        else:
            mem = (request.form.get('memlimit') or '2048').strip()
            vcpus = (request.form.get('vcpus') or '2').strip()
            disk_gb = (request.form.get('disk_gb') or '20').strip()
            disk_path = (request.form.get('disk_path') or '').strip()
            net_type = (request.form.get('type') or 'network').strip()
            net_link = (request.form.get('link') or 'default').strip()
            autostart = bool(request.form.get('autostart'))
            virt_type = (request.form.get('virt_type') or 'kvm').strip()
            boot = request.form.getlist('boot')
            iso = (request.form.get('iso') or '').strip()
            cpu_style = (request.form.get('cpu_style') or 'host').strip()
            cpu_model = (request.form.get('cpu_model') or '').strip()
            ipv4 = (request.form.get('ipaddress') or '').strip() if 'ipaddress' in request.form else ''
            try:
                virt.create_from_preset(
                    name,
                    memory_mb=int(mem) if mem.isdigit() else 2048,
                    vcpus=int(vcpus) if vcpus.isdigit() else 2,
                    disk_gb=int(disk_gb) if disk_gb.isdigit() else 20,
                    disk_path=disk_path,
                    net_type=net_type,
                    net_link=net_link,
                    autostart=autostart,
                    virt_type=virt_type,
                    boot=boot,
                    iso=iso,
                    cpu_style=cpu_style,
                    cpu_model=cpu_model,
                    ipv4=ipv4)
                flash(u'VM %s created.' % name, 'success')
            except virt.ContainerAlreadyExists as e:
                flash(u'%s' % e, 'error')
            except subprocess.CalledProcessError as e:
                flash(u'Unable to create %s: %s' %
                      (name, virt.virsh_error_message(e.output)), 'error')
            except Exception as e:
                flash(u'Unable to create %s: %s' % (name, e), 'error')
    return redirect(url_for('vm_home'))


def clone_container():
    '''POST: virt-clone into a new name.'''

    if 'logged_in' not in session:
        return render_template('login.html')
    if session['su'] != 'Yes':
        return abort(403)
    if request.method == 'POST':
        orig = request.form.get('orig', '')
        name = (request.form.get('name') or '').strip()
        if not ok_vm_name(name):
            flash(u'Invalid clone name.', 'error')
        else:
            try:
                virt.clone(orig=orig, new=name)
                flash(u'VM %s cloned into %s.' % (orig, name), 'success')
            except virt.ContainerAlreadyExists:
                flash(u'VM %s already exists!' % name, 'error')
            except virt.ContainerDoesntExists:
                flash(u'VM %s does not exist!' % orig, 'error')
            except subprocess.CalledProcessError as e:
                flash(u'Failed to clone: %s' %
                      virt.virsh_error_message(e.output), 'error')
    return redirect(url_for('vm_home'))


def snapshot_info():
    '''JSON details for one VM snapshot (modal on Overview/Edit).'''

    if 'logged_in' in session:
        name = request.args.get('name', '')
        snap = request.args.get('snap', '')
        try:
            return jsonify(virt.snapshot_info(name, snap))
        except (virt.ContainerDoesntExists, virt.SnapshotDoesntExists,
                virt.InvalidSnapshot):
            return jsonify({'error': 'not found'}), 404
        except Exception as e:
            return jsonify({'error': str(e)}), 400
    return abort(403)


def register(app):
    '''Bind /virsh/action, create/clone, take-snapshot, and snapshot AJAX.'''

    app.add_url_rule('/virsh/action', view_func=action, endpoint='vm_action',
                     methods=['GET'])
    app.add_url_rule('/virsh/action/bulk', view_func=bulk_action,
                     endpoint='vm_bulk_action', methods=['POST'])
    app.add_url_rule('/virsh/action/take-snapshot', view_func=take_snapshot,
                     endpoint='vm_take_snapshot', methods=['POST'])
    app.add_url_rule('/virsh/action/create', view_func=create_container,
                     endpoint='vm_create', methods=['GET', 'POST'])
    app.add_url_rule('/virsh/action/clone', view_func=clone_container,
                     endpoint='vm_clone', methods=['GET', 'POST'])
    app.add_url_rule('/virsh/_snapshot_info', view_func=snapshot_info,
                     endpoint='vm_snapshot_info')
