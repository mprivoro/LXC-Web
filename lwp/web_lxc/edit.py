# Edit one container (config form + raw file) and the HTML console page.

import os

import lxclite as lxc
import lwp
from lwp.util import (
    IPV4_CIDR, RE_CPUS, RE_CT_NAME, RE_FLAGS, RE_HOSTNAME, RE_HWADDR,
    RE_IFACE, RE_ROOTFS, RE_SHARES, RE_WORD, matches)
from lwp.web.helpers import console_ok
from flask import (abort, flash, redirect, render_template, request, session,
                   url_for)


def container_console(container=None):
    '''
    Full-page LXC attach console (opened in a separate window/tab).
    '''

    if 'logged_in' not in session:
        return render_template('login.html')
    if session.get('su') != 'Yes' or not console_ok():
        return abort(403)
    if not matches(RE_CT_NAME, container):
        return abort(404)
    if not lxc.exists(container):
        flash(u'Container %s does not exist!' % container, 'error')
        return redirect(url_for('home'))
    return render_template('console.html', container=container)


def edit(container=None):
    '''GET: edit form. POST: save fields or the raw config file.'''

    if 'logged_in' in session:
        host_memory = lwp.host_memory_usage()
        cfg = None
        if request.method == 'POST' and request.form.get('save_config'):
            if session.get('su') != 'Yes':
                return abort(403)
            if request.form.get('token') != session.get('token'):
                flash(u'Invalid token!', 'error')
            elif request.form.get('restore_bak'):
                ok, err = lwp.restore_container_config_backup(container)
                if ok:
                    flash(u'Config backup restored for %s.' % container,
                          'success')
                else:
                    flash(u'Unable to restore backup: %s' % err, 'error')
            else:
                ok, err = lwp.write_container_config(
                    container, request.form.get('raw_config', ''))
                if not ok:
                    flash(u'Unable to save config: %s' % err, 'error')
                else:
                    try:
                        inf = lxc.info(container)
                    except lxc.ContainerDoesntExists:
                        inf = {'state': 'BROKEN',
                               'error': 'Container does not exist.'}
                    except Exception as e:
                        inf = {'state': 'BROKEN', 'error': str(e)}
                    if inf.get('state') == 'BROKEN':
                        flash(u'Config saved, but LXC cannot load %s: %s' % (
                            container,
                            inf.get('error') or 'broken config'), 'error')
                    else:
                        flash(u'Config file saved for %s.' % container,
                              'success')
        elif request.method == 'POST':
            cfg = lwp.get_container_settings(container)
            if not cfg:
                cfg = lwp.empty_container_settings('Config file is missing.')
            if cfg.get('config_error'):
                flash(u'Cannot update %s: %s' % (container, cfg['config_error']),
                      'error')
                cfg = None
        if request.method == 'POST' and cfg:
            try:
                info = lxc.info(container)
            except lxc.ContainerDoesntExists:
                flash(u'Container %s does not exist!' % container, 'error')
                return redirect(url_for('home'))
            except Exception as e:
                info = {'state': 'BROKEN', 'pid': '0', 'error': str(e)}

            form = {}
            form['type'] = request.form['type']
            form['link'] = request.form['link']
            try:
                form['flags'] = request.form['flags']
            except KeyError:
                form['flags'] = 'down'
            form['hwaddr'] = request.form['hwaddress']
            form['rootfs'] = request.form['rootfs']
            form['utsname'] = request.form['hostname']
            form['ipv4'] = request.form['ipaddress']
            form['memlimit'] = request.form['memlimit']
            form['swlimit'] = request.form['swlimit']
            form['cpus'] = request.form['cpus']
            form['shares'] = request.form['cpushares']
            try:
                form['autostart'] = request.form['autostart']
            except KeyError:
                form['autostart'] = False

            if form['utsname'] != cfg['utsname'] and \
                    matches(RE_HOSTNAME, form['utsname']):
                lwp.push_config_value('lxc.utsname', form['utsname'],
                                      container=container)
                flash(u'Hostname updated for %s!' % container, 'success')

            if form['flags'] != cfg['flags'] and \
                    matches(RE_FLAGS, form['flags']):
                lwp.push_config_value('lxc.network.flags', form['flags'],
                                      container=container)
                flash(u'Network flag updated for %s!' % container, 'success')

            if form['type'] != cfg['type'] and \
                    matches(RE_WORD, form['type']):
                lwp.push_config_value('lxc.network.type', form['type'],
                                      container=container)
                flash(u'Link type updated for %s!' % container, 'success')

            if form['link'] != cfg['link'] and \
                    matches(RE_IFACE, form['link']):
                lwp.push_config_value('lxc.network.link', form['link'],
                                      container=container)
                flash(u'Link name updated for %s!' % container, 'success')

            if form['hwaddr'] != cfg['hwaddr'] and \
                    matches(RE_HWADDR, form['hwaddr']):
                lwp.push_config_value('lxc.network.hwaddr', form['hwaddr'],
                                      container=container)
                flash(u'Hardware address updated for %s!' % container,
                      'success')

            if (not form['ipv4'] and form['ipv4'] != cfg['ipv4']) or \
                    (form['ipv4'] != cfg['ipv4'] and
                     matches('^%s$' % IPV4_CIDR, form['ipv4'])):
                lwp.push_config_value('lxc.network.ipv4', form['ipv4'],
                                      container=container)
                flash(u'IP address updated for %s!' % container, 'success')

            if form['memlimit'] != cfg['memlimit'] and \
                    form['memlimit'].isdigit() and \
                    int(form['memlimit']) <= int(host_memory['total']):
                if int(form['memlimit']) == int(host_memory['total']):
                    form['memlimit'] = ''

                if form['memlimit'] != cfg['memlimit']:
                    lwp.push_config_value('lxc.cgroup.memory.limit_in_bytes',
                                          form['memlimit'],
                                          container=container)
                    if info["state"].lower() not in ('stopped', 'broken'):
                        lxc.cgroup(container,
                                   'lxc.cgroup.memory.limit_in_bytes',
                                   form['memlimit'])
                    flash(u'Memory limit updated for %s!' % container,
                          'success')

            if form['swlimit'] != cfg['swlimit'] and \
                    form['swlimit'].isdigit() and \
                    int(form['swlimit']) <= int(host_memory['total'] * 2):
                if int(form['swlimit']) == int(host_memory['total'] * 2):
                    form['swlimit'] = ''

                if form['swlimit'].isdigit():
                    form['swlimit'] = int(form['swlimit'])

                if form['memlimit'].isdigit():
                    form['memlimit'] = int(form['memlimit'])

                if (form['memlimit'] == '' and form['swlimit'] != '') or \
                        (form['memlimit'] > form['swlimit'] and
                         form['swlimit'] != ''):
                    flash(u'Can\'t assign swap memory lower than'
                          ' the memory limit', 'warning')

                elif form['swlimit'] != cfg['swlimit'] and \
                        form['memlimit'] <= form['swlimit']:
                    lwp.push_config_value(
                        'lxc.cgroup.memory.memsw.limit_in_bytes',
                        form['swlimit'], container=container)

                    if info["state"].lower() not in ('stopped', 'broken'):
                        lxc.cgroup(container,
                                   'lxc.cgroup.memory.memsw.limit_in_bytes',
                                   form['swlimit'])
                    flash(u'Swap limit updated for %s!' % container, 'success')

            if (not form['cpus'] and form['cpus'] != cfg['cpus']) or \
                    (form['cpus'] != cfg['cpus'] and
                     matches(RE_CPUS, form['cpus'])):
                lwp.push_config_value('lxc.cgroup.cpuset.cpus', form['cpus'],
                                      container=container)

                if info["state"].lower() not in ('stopped', 'broken'):
                        lxc.cgroup(container, 'lxc.cgroup.cpuset.cpus',
                                   form['cpus'])
                flash(u'CPUs updated for %s!' % container, 'success')

            if (not form['shares'] and form['shares'] != cfg['shares']) or \
                    (form['shares'] != cfg['shares'] and
                     matches(RE_SHARES, form['shares'])):
                lwp.push_config_value('lxc.cgroup.cpu.shares', form['shares'],
                                      container=container)
                if info["state"].lower() not in ('stopped', 'broken'):
                        lxc.cgroup(container, 'lxc.cgroup.cpu.shares',
                                   form['shares'])
                flash(u'CPU shares updated for %s!' % container, 'success')

            if form['rootfs'] != cfg['rootfs'] and \
                    matches(RE_ROOTFS, form['rootfs']):
                lwp.push_config_value('lxc.rootfs', form['rootfs'],
                                      container=container)
                flash(u'Rootfs updated!' % container, 'success')

            auto = lwp.ls_auto()
            if form['autostart'] == 'True' and \
                    not ('%s.conf' % container) in auto:
                try:
                    os.symlink(os.path.join(lxc.lxcpath(), container, 'config'),
                               '/etc/lxc/auto/%s.conf' % container)
                    flash(u'Autostart enabled for %s' % container, 'success')
                except OSError:
                    flash(u'Unable to create symlink \'/etc/lxc/auto/%s.conf\''
                          % container, 'error')
            elif not form['autostart'] and ('%s.conf' % container) in auto:
                try:
                    os.remove('/etc/lxc/auto/%s.conf' % container)
                    flash(u'Autostart disabled for %s' % container, 'success')
                except OSError:
                    flash(u'Unable to remove symlink', 'error')

        try:
            info = lxc.info(container)
        except lxc.ContainerDoesntExists:
            flash(u'Container %s does not exist!' % container, 'error')
            return redirect(url_for('home'))
        except Exception as e:
            info = {'state': 'BROKEN', 'pid': '0', 'error': str(e)}
        status = info['state']
        pid = info['pid']
        try:
            memusg = 0 if status == 'BROKEN' else lwp.memory_usage(container)
        except Exception:
            memusg = 0

        infos = {'status': status,
                 'pid': pid,
                 'memusg': memusg,
                 'error': info.get('error') or ''}
        try:
            snapshots = lxc.snapshots(container)
        except Exception:
            snapshots = []
        try:
            snap_plan = lxc.snapshot_plan(container)
        except Exception:
            snap_plan = {
                'state': infos['status'],
                'storage': 'unknown',
                'can': False,
                'need_confirm': False,
                'method': 'snapshot',
                'reason': infos['error'] or 'Container is not available.',
            }
        try:
            settings = lwp.get_container_settings(container)
        except Exception as e:
            settings = lwp.empty_container_settings(str(e))
        if not settings:
            settings = lwp.empty_container_settings(
                'Config file is missing or unreadable.')
        if settings.get('config_error') and not infos['error']:
            infos['error'] = settings['config_error']
        raw_config, config_read_error = lwp.read_container_config(container)
        config_path = lwp.container_config_path(container)
        config_missing = (raw_config is None and
                          config_read_error == 'missing')
        if raw_config is None:
            raw_config = ''
        config_has_bak = bool(config_path and
                              os.path.isfile(config_path + '.bak'))
        return render_template('lxc/edit.html', containers=lxc.ls(),
                               container=container, infos=infos,
                               settings=settings,
                               host_memory=host_memory,
                               snapshots=snapshots,
                               snap_plan=snap_plan,
                               raw_config=raw_config,
                               config_path=config_path,
                               config_missing=config_missing,
                               config_read_error=config_read_error,
                               config_has_bak=config_has_bak,
                               console_available=console_ok(),
                               lxcpath=lxc.lxcpath())
    return render_template('login.html')



def register(app):
    '''Bind /<container>/edit and /<container>/console.'''

    app.add_url_rule('/<container>/console', view_func=container_console,
                     endpoint='container_console')
    app.add_url_rule('/<container>/edit', view_func=edit, endpoint='edit',
                     methods=['GET', 'POST'])
