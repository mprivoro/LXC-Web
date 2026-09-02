# Start/stop/freeze/destroy, bulk start/stop, snapshots, create, clone. Query/form → lxclite.

import subprocess
import time

import lxclite as lxc
import lwp
from lwp.util import (
    RE_ABS_DIR, RE_CT_CREATE, RE_CT_NAME, RE_FSSIZE, RE_FSTYPE, RE_IFACE,
    RE_ZFS, matches)
from flask import (abort, flash, jsonify, redirect, render_template, request,
                   session, url_for)


def action():
    '''GET /action: start, stop, freeze, unfreeze, destroy, snapshot restore/delete.'''

    if 'logged_in' in session:
        name = request.args.get('name', '')
        if request.args.get('token') == session.get('token'):
            action = request.args['action']

            if action == 'start':
                try:
                    lxc.start(name)
                    # Fix bug : "the container is randomly not
                    #            displayed in overview list after a boot"
                    time.sleep(1)
                    flash(u'Container %s started successfully!' % name, 'success')
                except lxc.ContainerAlreadyRunning:
                    flash(u'Container %s is already running!' % name, 'error')
                except subprocess.CalledProcessError as e:
                    flash(u'Unable to start %s: %s' % (name, e.output), 'error')
            elif action == 'stop':
                try:
                    lxc.stop(name)
                    flash(u'Container %s stopped successfully!' % name, 'success')
                except lxc.ContainerNotRunning:
                    flash(u'Container %s is already stopped!' % name, 'error')
                except subprocess.CalledProcessError as e:
                    flash(u'Unable to stop %s: %s' % (name, e.output), 'error')
            elif action == 'freeze':
                try:
                    lxc.freeze(name)
                    flash(u'Container %s frozen successfully!' % name, 'success')
                except lxc.ContainerNotRunning:
                    flash(u'Container %s not running!' % name, 'error')
                except subprocess.CalledProcessError as e:
                    flash(u'Unable to freeze %s: %s' % (name, e.output), 'error')
            elif action == 'unfreeze':
                try:
                    lxc.unfreeze(name)
                    flash(u'Container %s unfrozen successfully!' % name, 'success')
                except lxc.ContainerNotRunning:
                    flash(u'Container %s not frozen!' % name, 'error')
                except subprocess.CalledProcessError as e:
                    flash(u'Unable to unfeeze %s: %s' % (name, e.output), 'error')

            elif action == 'destroy':
                if session['su'] != 'Yes':
                    return abort(403)
                try:
                    lxc.destroy(name)
                    flash(u'Container %s destroyed successfully!' % name, 'success')
                except lxc.ContainerDoesntExists:
                    flash(u'The Container %s does not exists!' % name, 'error')
                except subprocess.CalledProcessError as e:
                    flash(u'Unable to destroy %s: %s' % (name, e.output), 'error')
            elif action == 'destroy_snapshot':
                if session['su'] != 'Yes':
                    return abort(403)
                snap = request.args.get('snap', '')
                try:
                    lxc.snapshot_destroy(name, snap)
                    flash(u'Snapshot %s of %s destroyed successfully!' % (snap, name),
                          'success')
                except lxc.InvalidSnapshot:
                    flash(u'Invalid snapshot name!', 'error')
                except lxc.SnapshotDoesntExists:
                    flash(u'Snapshot %s does not exist for %s!' % (snap, name),
                          'error')
                except lxc.ContainerDoesntExists:
                    flash(u'The Container %s does not exists!' % name, 'error')
                except subprocess.CalledProcessError as e:
                    flash(u'Unable to destroy snapshot %s of %s: %s' %
                          (snap, name, lxc.lxc_error_message(e.output)), 'error')
            elif action == 'restore_snapshot':
                if session['su'] != 'Yes':
                    return abort(403)
                snap = request.args.get('snap', '')
                newname = (request.args.get('newname') or '').strip()
                try:
                    lxc.snapshot_restore(name, snap, newname or None)
                    if newname and newname != name:
                        flash(u'Snapshot %s of %s restored as %s!' %
                              (snap, name, newname), 'success')
                    else:
                        flash(u'Snapshot %s restored into %s!' % (snap, name),
                              'success')
                except lxc.InvalidSnapshot:
                    flash(u'Invalid snapshot or container name!', 'error')
                except lxc.SnapshotDoesntExists:
                    flash(u'Snapshot %s does not exist for %s!' % (snap, name),
                          'error')
                except lxc.ContainerDoesntExists:
                    flash(u'The Container %s does not exists!' % name, 'error')
                except lxc.ContainerAlreadyExists:
                    flash(u'The Container %s already exists!' % newname, 'error')
                except lxc.ContainerAlreadyRunning:
                    flash(u'Stop %s before restoring a snapshot in place!' % name,
                          'error')
                except subprocess.CalledProcessError as e:
                    flash(u'Unable to restore snapshot %s of %s: %s' %
                          (snap, name, lxc.lxc_error_message(e.output)), 'error')
            elif action == 'reboot' and name == 'host':
                if session['su'] != 'Yes':
                    return abort(403)
                msg = '\v*** NoDeck *** \
                        \nReboot from web panel'
                try:
                    lxc._run('/sbin/shutdown -r now \'%s\'' % msg)
                    flash(u'System will now restart!', 'success')
                except subprocess.CalledProcessError as e:
                    flash(u'System error: %s' % e.output, 'error')
        if request.args.get('from') == 'edit' and name:
            return redirect(url_for('edit', container=name))
        return redirect(url_for('home'))
    return render_template('login.html')


def bulk_action():
    '''POST: start or stop the selected containers (skips broken / already there).'''

    if 'logged_in' not in session:
        return render_template('login.html')
    if request.form.get('token') != session.get('token'):
        return abort(403)
    action_name = request.form.get('action', '')
    if action_name not in ('start', 'stop'):
        flash(u'Invalid bulk action!', 'error')
        return redirect(url_for('home'))

    names = []
    seen = set()
    for name in request.form.getlist('name'):
        name = (name or '').strip()
        if not name or name in seen or not matches(RE_CT_NAME, name):
            continue
        seen.add(name)
        names.append(name)
    if not names:
        flash(u'No containers selected!', 'error')
        return redirect(url_for('home'))

    ok = []
    failed = []
    did_start = False
    for name in names:
        try:
            state = lxc.info(name).get('state', '')
        except lxc.ContainerDoesntExists:
            failed.append('%s (does not exist)' % name)
            continue
        except Exception as e:
            failed.append('%s (%s)' % (name, e))
            continue
        if state == 'BROKEN':
            failed.append('%s (broken config)' % name)
            continue
        try:
            if action_name == 'start':
                if state == 'RUNNING':
                    continue
                if state == 'FROZEN':
                    lxc.unfreeze(name)
                else:
                    lxc.start(name)
                    did_start = True
            else:
                if state == 'STOPPED':
                    continue
                lxc.stop(name)
            ok.append(name)
        except (lxc.ContainerAlreadyRunning, lxc.ContainerNotRunning):
            continue
        except subprocess.CalledProcessError as e:
            failed.append('%s (%s)' % (name, lxc.lxc_error_message(e.output)))
        except Exception as e:
            failed.append('%s (%s)' % (name, e))
    if did_start:
        time.sleep(1)
    if ok:
        verb = 'started' if action_name == 'start' else 'stopped'
        flash(u'%d container(s) %s: %s' % (len(ok), verb, ', '.join(ok)),
              'success')
    elif not failed:
        flash(u'Nothing to %s (already in that state).' % action_name, 'info')
    if failed:
        flash(u'Failed: %s' % '; '.join(failed), 'error')
    return redirect(url_for('home'))


def take_snapshot():
    '''
    Create an LXC snapshot of a container.
    '''
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
        snap = lxc.snapshot_create(name, comment, allow_running=allow_running)
        if snap:
            flash(u'Snapshot %s of %s created successfully!' % (snap, name),
                  'success')
        else:
            flash(u'Snapshot of %s created successfully!' % name, 'success')
    except lxc.ContainerDoesntExists:
        flash(u'The Container %s does not exists!' % name, 'error')
    except lxc.SnapshotNotPossible as e:
        flash(u'%s' % e, 'error')
    except lxc.SnapshotNeedsConfirm as e:
        flash(u'%s' % e, 'warning')
    except subprocess.CalledProcessError as e:
        flash(u'Unable to snapshot %s: %s' %
              (name, lxc.lxc_error_message(e.output)), 'error')

    if request.form.get('from') == 'edit':
        return redirect(url_for('edit', container=name))
    return redirect(url_for('home'))


def create_container():
    '''POST: lxc-create from a template or a cached download image.'''

    if 'logged_in' in session:
        if session['su'] != 'Yes':
            return abort(403)
        if request.method == 'POST':
            name = request.form['name']
            command = request.form.get('command', '')
            template, image_xargs = lwp.parse_create_source(
                request.form.get('source', ''))
            if not template:
                flash(u'Select a cached image or a template!', 'error')
                return redirect(url_for('home'))
            if image_xargs:
                command = image_xargs
            create_env = lwp.create_env()

            if matches(RE_CT_CREATE, name):
                storage_method = request.form['backingstore']

                if storage_method == 'default':
                    try:
                        lxc.create(name, template=template, xargs=command,
                                   env=create_env)
                        flash(u'Container %s created successfully!' % name, 'success')
                    except lxc.ContainerAlreadyExists:
                        flash(u'The Container %s is already created!' % name,
                              'error')
                    except subprocess.CalledProcessError as e:
                        flash(u'Error creating container %s: %s' % (name, e.output), 'error')

                elif storage_method == 'directory':
                    directory = request.form['dir']

                    if matches(RE_ABS_DIR, directory) and directory != '':
                        try:
                            lxc.create(name, template=template,
                                       storage='dir --dir %s' % directory,
                                       xargs=command, env=create_env)
                            flash(u'Container %s created successfully!'
                                  % name, 'success')
                        except lxc.ContainerAlreadyExists:
                            flash(u'The Container %s is already created!'
                                  % name, 'error')
                        except subprocess.CalledProcessError as e:
                            flash(u'Error creating container %s: %s' % (name, e.output), 'error')

                elif storage_method == 'zfs':
                    zfs = request.form['zpoolname']

                    if matches(RE_ZFS, zfs) and zfs != '':
                        try:
                            lxc.create(name, template=template,
                                       storage='zfs --zfsroot %s' % zfs,
                                       xargs=command, env=create_env)
                            flash(u'Container %s created successfully!' % name, 'success')
                        except lxc.ContainerAlreadyExists:
                            flash(u'The Container %s is already created!' % name, 'error')
                        except subprocess.CalledProcessError as e:
                            flash(u'Error creating container %s: %s' % (name, e.output), 'error')

                elif storage_method == 'lvm':
                    lvname = request.form['lvname']
                    vgname = request.form['vgname']
                    fstype = request.form['fstype']
                    fssize = request.form['fssize']
                    storage_options = 'lvm'

                    if matches(RE_IFACE, lvname) and lvname != '':
                        storage_options += ' --lvname %s' % lvname
                    if matches(RE_IFACE, vgname) and vgname != '':
                        storage_options += ' --vgname %s' % vgname
                    if matches(RE_FSTYPE, fstype) and fstype != '':
                        storage_options += ' --fstype %s' % fstype
                    if matches(RE_FSSIZE, fssize) and fssize != '':
                        storage_options += ' --fssize %s' % fssize

                    try:
                        lxc.create(name, template=template,
                                   storage=storage_options, xargs=command,
                                   env=create_env)
                        flash(u'Container %s created successfully!' % name, 'success')
                    except lxc.ContainerAlreadyExists:
                        flash(u'The container/logical volume %s is '
                              'already created!' % name, 'error')
                    except subprocess.CalledProcessError as e:
                        flash(u'Error creating container %s: %s' % (name, e.output), 'error')

                else:
                    flash(u'Missing parameters to create container!', 'error')

            else:
                if name == '':
                    flash(u'Please enter a container name!', 'error')
                else:
                    flash(u'Invalid name for \"%s\"!' % name, 'error')

        return redirect(url_for('home'))
    return render_template('login.html')


def clone_container():
    '''POST: lxc-clone, optional snapshot clone.'''

    if 'logged_in' in session:
        if session['su'] != 'Yes':
            return abort(403)
        if request.method == 'POST':
            orig = request.form['orig']
            name = request.form['name']

            try:
                snapshot = request.form['snapshot']
                if snapshot == 'True':
                    snapshot = True
            except KeyError:
                snapshot = False

            if matches(RE_CT_CREATE, name):
                out = None

                try:
                    lxc.clone(orig=orig, new=name, snapshot=snapshot)
                    flash(u'Container %s cloned into %s successfully!' % (orig, name), 'success')
                except lxc.ContainerAlreadyExists:
                    flash(u'The Container %s already exists!' % name, 'error')
                except subprocess.CalledProcessError as e:
                    flash(u'Failed to clone %s into %s: %s' % (orig, name, e.output), 'error')

            else:
                if name == '':
                    flash(u'Please enter a container name!', 'error')
                else:
                    flash(u'Invalid name for \"%s\"!' % name, 'error')

        return redirect(url_for('home'))
    return render_template('login.html')


def snapshot_info():
    '''JSON details for one snapshot (modal on Overview/Edit).'''

    if 'logged_in' in session:
        name = request.args.get('name', '')
        snap = request.args.get('snap', '')
        try:
            return jsonify(lxc.snapshot_info(name, snap))
        except (lxc.ContainerDoesntExists, lxc.SnapshotDoesntExists,
                lxc.InvalidSnapshot):
            return jsonify({'error': 'not found'}), 404
        except Exception as e:
            return jsonify({'error': str(e)}), 400
    return abort(403)


def register(app):
    '''Bind /action, create/clone, take-snapshot, and snapshot AJAX.'''

    app.add_url_rule('/action', view_func=action, endpoint='action', methods=['GET'])
    app.add_url_rule('/action/bulk', view_func=bulk_action, endpoint='bulk_action',
                     methods=['POST'])
    app.add_url_rule('/action/take-snapshot', view_func=take_snapshot,
                     endpoint='take_snapshot', methods=['POST'])
    app.add_url_rule('/action/create-container', view_func=create_container,
                     endpoint='create_container', methods=['GET', 'POST'])
    app.add_url_rule('/action/clone-container', view_func=clone_container,
                     endpoint='clone_container', methods=['GET', 'POST'])
    app.add_url_rule('/_snapshot_info', view_func=snapshot_info,
                     endpoint='snapshot_info')
