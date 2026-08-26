# LXC Python Library
# for compatibility with LXC 0.8 and 0.9
# on Ubuntu 12.04/12.10/13.04

# Author: Michael Privorotsky
# https://github.com/mprivoro/LXC-Web

# The MIT License (MIT)
# Copyright (c) 2013 Antoine TANZILLI, Élie DELOUMEAU
# Copyright (c) 2026 Michael Privorotsky

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import lxclite as lxc
import lwp
import argparse
import subprocess
import time
import re
import hashlib
import sqlite3
import os

from flask import Flask, request, session, g, redirect, url_for, abort, \
    render_template, flash, jsonify

try:
    import configparser
except ImportError:
    import ConfigParser as configparser

# configuration
config = configparser.SafeConfigParser()
config.readfp(open('lwp.conf'))

SECRET_KEY = config.get('session', 'secret_key', raw=True)
DEBUG = config.getboolean('global', 'debug')
DATABASE = config.get('database', 'file')
ADDRESS = config.get('global', 'address')
PORT = int(config.get('global', 'port'))


# Flask app
app = Flask(__name__)
app.config.from_object(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True

# Optional LXC attach console (flask-sock). If the extra is missing, the
# panel runs exactly as before — no Console button, no WebSocket route.
CONSOLE_AVAILABLE = False
try:
    from flask_sock import Sock
    app.config['SOCK_SERVER_OPTIONS'] = {'ping_interval': 20}
    sock = Sock(app)
    from lwp.console import register_console
    register_console(sock, lxc)
    CONSOLE_AVAILABLE = True
except ImportError:
    sock = None


def connect_db():
    '''
    SQLite3 connect function
    '''

    return sqlite3.connect(app.config['DATABASE'])


@app.before_request
def before_request():
    '''
    executes functions before all requests
    '''

    check_session_limit()
    g.db = connect_db()


@app.teardown_request
def teardown_request(exception):
    '''
    executes functions after all requests
    '''

    if hasattr(g, 'db'):
        g.db.close()


def _overview_row(container, running=False):
    '''
    One Overview row. A killed/invalid config must not raise.
    '''

    error = ''
    try:
        inf = lxc.info(container)
    except Exception as e:
        inf = {'state': 'BROKEN', 'pid': '0', 'error': str(e)}
    error = inf.get('error') or ''

    try:
        settings = lwp.get_container_settings(container)
    except Exception as e:
        settings = lwp.empty_container_settings(str(e))
        error = error or str(e)
    if not settings:
        settings = lwp.empty_container_settings(
            'Config file is missing or unreadable.')
        error = error or settings['config_error']
    else:
        error = error or settings.get('config_error') or ''

    memusg = 0
    snaps = []
    if inf.get('state') != 'BROKEN':
        try:
            memusg = lwp.memory_usage(container)
        except Exception:
            memusg = 0
        try:
            snaps = lxc.snapshots(container)
        except Exception:
            snaps = []
        if running:
            try:
                settings['ipv4'] = lxc.ip_address(container, True)
            except Exception:
                pass
    elif not error:
        error = 'LXC cannot load this container config.'

    return {
        'name': container,
        'memusg': memusg,
        'settings': settings,
        'snapshots': snaps,
        'error': error,
    }


@app.route('/')
@app.route('/home')
def home():
    '''
    home page function
    '''

    if 'logged_in' in session:
        try:
            listx = lxc.listx()
        except Exception as e:
            flash(u'Unable to list containers: %s' % e, 'error')
            listx = {'RUNNING': [], 'FROZEN': [], 'STOPPED': [], 'BROKEN': []}

        containers_all = []
        for status in ['RUNNING', 'FROZEN', 'STOPPED', 'BROKEN']:
            containers_by_status = []
            running = (status == 'RUNNING')
            for container in listx.get(status, []):
                try:
                    containers_by_status.append(
                        _overview_row(container, running))
                except Exception as e:
                    containers_by_status.append({
                        'name': container,
                        'memusg': 0,
                        'settings': lwp.empty_container_settings(str(e)),
                        'snapshots': [],
                        'error': str(e),
                    })
            containers_all.append({
                'status': status.lower(),
                'containers': containers_by_status
            })

        try:
            names = lxc.ls()
        except Exception:
            names = []

        return render_template('index.html', containers=names,
                               containers_all=containers_all,
                               dist=lwp.check_ubuntu(),
                               templates=lwp.get_templates_list(),
                               images=lwp.get_cached_images(),
                               console_available=CONSOLE_AVAILABLE)
    return render_template('login.html')


@app.route('/about')
def about():
    '''
    about page
    '''

    if 'logged_in' in session:
        return render_template('about.html', containers=lxc.ls(),
                               version=lwp.check_version())
    return render_template('login.html')


@app.route('/<container>/console')
def container_console(container=None):
    '''
    Full-page LXC attach console (opened in a separate window/tab).
    '''

    if 'logged_in' not in session:
        return render_template('login.html')
    if session.get('su') != 'Yes' or not CONSOLE_AVAILABLE:
        return abort(403)
    if not container or not re.match(r'^[A-Za-z0-9_-]+$', container):
        return abort(404)
    if not lxc.exists(container):
        flash(u'Container %s does not exist!' % container, 'error')
        return redirect(url_for('home'))
    return render_template('console.html', container=container)


@app.route('/<container>/edit', methods=['POST', 'GET'])
def edit(container=None):
    '''
    edit containers page and actions if form post request
    '''

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
            ip_regex = '(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?).(25[0-5]' \
                       '|2[0-4][0-9]|[01]?[0-9][0-9]?).(25[0-5]|2[0-4]' \
                       '[0-9]|[01]?[0-9][0-9]?).(25[0-5]|2[0-4][0-9]|[01]' \
                       '?[0-9][0-9]?)(/(3[0-2]|[12]?[0-9]))?'
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
                    re.match('(?!^containers$)|^(([a-zA-Z0-9]|[a-zA-Z0-9]'
                             '[a-zA-Z0-9\-]*[a-zA-Z0-9])\.)*([A-Za-z0-9]|'
                             '[A-Za-z0-9][A-Za-z0-9\-]*[A-Za-z0-9])$',
                             form['utsname']):
                lwp.push_config_value('lxc.utsname', form['utsname'],
                                      container=container)
                flash(u'Hostname updated for %s!' % container, 'success')

            if form['flags'] != cfg['flags'] and \
                    re.match('^(up|down)$', form['flags']):
                lwp.push_config_value('lxc.network.flags', form['flags'],
                                      container=container)
                flash(u'Network flag updated for %s!' % container, 'success')

            if form['type'] != cfg['type'] and \
                    re.match('^\w+$', form['type']):
                lwp.push_config_value('lxc.network.type', form['type'],
                                      container=container)
                flash(u'Link type updated for %s!' % container, 'success')

            if form['link'] != cfg['link'] and \
                    re.match('^[a-zA-Z0-9_-]+$', form['link']):
                lwp.push_config_value('lxc.network.link', form['link'],
                                      container=container)
                flash(u'Link name updated for %s!' % container, 'success')

            if form['hwaddr'] != cfg['hwaddr'] and \
                    re.match('^([a-fA-F0-9]{2}[:|\-]?){6}$', form['hwaddr']):
                lwp.push_config_value('lxc.network.hwaddr', form['hwaddr'],
                                      container=container)
                flash(u'Hardware address updated for %s!' % container,
                      'success')

            if (not form['ipv4'] and form['ipv4'] != cfg['ipv4']) or \
                    (form['ipv4'] != cfg['ipv4'] and
                     re.match('^%s$' % ip_regex, form['ipv4'])):
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
                     re.match('^[0-9,-]+$', form['cpus'])):
                lwp.push_config_value('lxc.cgroup.cpuset.cpus', form['cpus'],
                                      container=container)

                if info["state"].lower() not in ('stopped', 'broken'):
                        lxc.cgroup(container, 'lxc.cgroup.cpuset.cpus',
                                   form['cpus'])
                flash(u'CPUs updated for %s!' % container, 'success')

            if (not form['shares'] and form['shares'] != cfg['shares']) or \
                    (form['shares'] != cfg['shares'] and
                     re.match('^[0-9]+$', form['shares'])):
                lwp.push_config_value('lxc.cgroup.cpu.shares', form['shares'],
                                      container=container)
                if info["state"].lower() not in ('stopped', 'broken'):
                        lxc.cgroup(container, 'lxc.cgroup.cpu.shares',
                                   form['shares'])
                flash(u'CPU shares updated for %s!' % container, 'success')

            if form['rootfs'] != cfg['rootfs'] and \
                    re.match('^[a-zA-Z0-9_/\-\.]+', form['rootfs']):
                lwp.push_config_value('lxc.rootfs', form['rootfs'],
                                      container=container)
                flash(u'Rootfs updated!' % container, 'success')

            auto = lwp.ls_auto()
            if form['autostart'] == 'True' and \
                    not ('%s.conf' % container) in auto:
                try:
                    os.symlink('/var/lib/lxc/%s/config' % container,
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
        return render_template('edit.html', containers=lxc.ls(),
                               container=container, infos=infos,
                               settings=settings,
                               host_memory=host_memory,
                               snapshots=snapshots,
                               snap_plan=snap_plan,
                               raw_config=raw_config,
                               config_path=config_path,
                               config_missing=config_missing,
                               config_read_error=config_read_error,
                               config_has_bak=config_has_bak)
    return render_template('login.html')


@app.route('/settings/lxc-net', methods=['POST', 'GET'])
def lxc_net():
    '''
    lxc-net (/etc/default/lxc) settings page and actions if form post request
    '''
    if 'logged_in' in session:
        if session['su'] != 'Yes':
            return abort(403)

        if request.method == 'POST':
            if lxc.running() == []:
                cfg = lwp.get_net_settings()
                ip_regex = '(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?).(25[0-5]' \
                           '|2[0-4][0-9]|[01]?[0-9][0-9]?).(25[0-5]|2[0-4]' \
                           '[0-9]|[01]?[0-9][0-9]?).(25[0-5]|2[0-4][0-9]|' \
                           '[01]?[0-9][0-9]?)'

                form = {}
                try:
                    form['use'] = request.form['use']
                except KeyError:
                    form['use'] = 'false'

                try:
                    form['bridge'] = request.form['bridge']
                except KeyError:
                    form['bridge'] = None

                try:
                    form['address'] = request.form['address']
                except KeyError:
                    form['address'] = None

                try:
                    form['netmask'] = request.form['netmask']
                except KeyError:
                    form['netmask'] = None

                try:
                    form['network'] = request.form['network']
                except KeyError:
                    form['network'] = None

                try:
                    form['range'] = request.form['range']
                except KeyError:
                    form['range'] = None

                try:
                    form['max'] = request.form['max']
                except KeyError:
                    form['max'] = None

                if form['use'] == 'true' and form['use'] != cfg['use']:
                    lwp.push_net_value('USE_LXC_BRIDGE', 'true')

                elif form['use'] == 'false' and form['use'] != cfg['use']:
                    lwp.push_net_value('USE_LXC_BRIDGE', 'false')

                if form['bridge'] and form['bridge'] != cfg['bridge'] \
                        and re.match('^[a-zA-Z0-9_-]+$', form['bridge']):
                    lwp.push_net_value('LXC_BRIDGE', form['bridge'])

                if form['address'] and form['address'] != cfg['address'] \
                        and re.match('^%s$' % ip_regex, form['address']):
                    lwp.push_net_value('LXC_ADDR', form['address'])

                if form['netmask'] and form['netmask'] != cfg['netmask'] \
                        and re.match('^%s$' % ip_regex, form['netmask']):
                    lwp.push_net_value('LXC_NETMASK', form['netmask'])

                if form['network'] and form['network'] != cfg['network'] and \
                        re.match('^%s(?:/\d{1,2}|)$' % ip_regex,
                                 form['network']):
                    lwp.push_net_value('LXC_NETWORK', form['network'])

                if form['range'] and form['range'] != cfg['range'] and \
                        re.match('^%s,%s$' % (ip_regex, ip_regex),
                                 form['range']):
                    lwp.push_net_value('LXC_DHCP_RANGE', form['range'])

                if form['max'] and form['max'] != cfg['max'] and \
                        re.match('^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$',
                                 form['max']):
                    lwp.push_net_value('LXC_DHCP_MAX', form['max'])

                if lwp.net_restart() == 0:
                    flash(u'LXC Network settings applied successfully!',
                          'success')
                else:
                    flash(u'Failed to restart LXC networking.', 'error')
            else:
                flash(u'Stop all containers before restart lxc-net.',
                      'warning')
        return render_template('lxc-net.html', containers=lxc.ls(),
                               cfg=lwp.get_net_settings(),
                               running=lxc.running())
    return render_template('login.html')


@app.route('/lwp/users', methods=['POST', 'GET'])
def lwp_users():
    '''
    returns users and get posts request : can edit or add user in page.
    this funtction uses sqlite3
    '''
    if 'logged_in' in session:
        if session['su'] != 'Yes':
            return abort(403)

        try:
            trash = request.args.get('trash')
        except KeyError:
            trash = 0

        su_users = query_db("SELECT COUNT(id) as num FROM users "
                            "WHERE su='Yes'", [], one=True)

        if request.args.get('token') == session.get('token') and \
                int(trash) == 1 and request.args.get('userid') and \
                request.args.get('username'):
            nb_users = query_db("SELECT COUNT(id) as num FROM users", [],
                                one=True)

            if nb_users['num'] > 1:
                if su_users['num'] <= 1:
                    su_user = query_db("SELECT username FROM users "
                                       "WHERE su='Yes'", [], one=True)

                    if su_user['username'] == request.args.get('username'):
                        flash(u'Can\'t delete the last admin user : %s' %
                              request.args.get('username'), 'error')
                        return redirect(url_for('lwp_users'))

                g.db.execute("DELETE FROM users WHERE id=? AND username=?",
                             [request.args.get('userid'),
                              request.args.get('username')])
                g.db.commit()
                flash(u'Deleted %s' % request.args.get('username'), 'success')
                return redirect(url_for('lwp_users'))

            flash(u'Can\'t delete the last user!', 'error')
            return redirect(url_for('lwp_users'))

        if request.method == 'POST':
            users = query_db('SELECT id, name, username, su FROM users '
                             'ORDER BY id ASC')

            if request.form['newUser'] == 'True':
                if not request.form['username'] in \
                        [user['username'] for user in users]:
                    if re.match('^\w+$', request.form['username']) and \
                            request.form['password1']:
                        if request.form['password1'] == \
                                request.form['password2']:
                            if request.form['name']:
                                if re.match('[a-z A-Z0-9]{3,32}',
                                            request.form['name']):
                                    g.db.execute(
                                        "INSERT INTO users "
                                        "(name, username, password) "
                                        "VALUES (?, ?, ?)",
                                        [request.form['name'],
                                         request.form['username'],
                                         hash_passwd(
                                             request.form['password1'])])
                                    g.db.commit()
                                else:
                                    flash(u'Invalid name!', 'error')
                            else:
                                g.db.execute("INSERT INTO users "
                                             "(username, password) VALUES "
                                             "(?, ?)",
                                             [request.form['username'],
                                              hash_passwd(
                                                  request.form['password1'])])
                                g.db.commit()

                            flash(u'Created %s' % request.form['username'],
                                  'success')
                        else:
                            flash(u'No password match', 'error')
                    else:
                        flash(u'Invalid username or password!', 'error')
                else:
                    flash(u'Username already exist!', 'error')

            elif request.form['newUser'] == 'False':
                if request.form['password1'] == request.form['password2']:
                    if re.match('[a-z A-Z0-9]{3,32}', request.form['name']):
                        if su_users['num'] <= 1:
                            su = 'Yes'
                        else:
                            try:
                                su = request.form['su']
                            except KeyError:
                                su = 'No'

                        if not request.form['name']:
                            g.db.execute("UPDATE users SET name='', su=? "
                                         "WHERE username=?",
                                         [su, request.form['username']])
                            g.db.commit()
                        elif request.form['name'] and \
                                not request.form['password1'] and \
                                not request.form['password2']:
                            g.db.execute("UPDATE users SET name=?, su=? "
                                         "WHERE username=?",
                                         [request.form['name'], su,
                                          request.form['username']])
                            g.db.commit()
                        elif request.form['name'] and \
                                request.form['password1'] and \
                                request.form['password2']:
                            g.db.execute("UPDATE users SET "
                                         "name=?, password=?, su=? WHERE "
                                         "username=?",
                                         [request.form['name'],
                                          hash_passwd(
                                              request.form['password1']),
                                          su, request.form['username']])
                            g.db.commit()
                        elif request.form['password1'] and \
                                request.form['password2']:
                            g.db.execute("UPDATE users SET password=?, su=? "
                                         "WHERE username=?",
                                         [hash_passwd(
                                             request.form['password1']),
                                          su, request.form['username']])
                            g.db.commit()

                        flash(u'Updated', 'success')
                    else:
                        flash(u'Invalid name!', 'error')
                else:
                    flash(u'No password match', 'error')
            else:
                flash(u'Unknown error!', 'error')

        users = query_db("SELECT id, name, username, su FROM users "
                         "ORDER BY id ASC")
        nb_users = query_db("SELECT COUNT(id) as num FROM users", [], one=True)
        su_users = query_db("SELECT COUNT(id) as num FROM users "
                            "WHERE su='Yes'", [], one=True)

        return render_template('users.html', containers=lxc.ls(), users=users,
                               nb_users=nb_users, su_users=su_users)
    return render_template('login.html')


@app.route('/checkconfig')
def checkconfig():
    '''
    returns the display of lxc-checkconfig command
    '''
    if 'logged_in' in session:
        if session['su'] != 'Yes':
            return abort(403)

        return render_template('checkconfig.html', containers=lxc.ls(),
                               cfg=lxc.checkconfig())
    return render_template('login.html')


@app.route('/action', methods=['GET'])
def action():
    '''
    manage all actions related to containers
    lxc-start, lxc-stop, etc...
    '''
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
                msg = '\v*** LXC Web Panel *** \
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


@app.route('/action/take-snapshot', methods=['POST'])
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


@app.route('/action/create-container', methods=['GET', 'POST'])
def create_container():
    '''
    verify all forms to create a container
    '''
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

            if re.match('^(?!^containers$)|[a-zA-Z0-9_-]+$', name):
                storage_method = request.form['backingstore']

                if storage_method == 'default':
                    try:
                        lxc.create(name, template=template, xargs=command)
                        flash(u'Container %s created successfully!' % name, 'success')
                    except lxc.ContainerAlreadyExists:
                        flash(u'The Container %s is already created!' % name,
                              'error')
                    except subprocess.CalledProcessError as e:
                        flash(u'Error creating container %s: %s' % (name, e.output), 'error')

                elif storage_method == 'directory':
                    directory = request.form['dir']

                    if re.match('^/[a-zA-Z0-9_/-]+$', directory) and \
                            directory != '':
                        try:
                            lxc.create(name, template=template,
                                       storage='dir --dir %s' % directory,
                                       xargs=command)
                            flash(u'Container %s created successfully!'
                                  % name, 'success')
                        except lxc.ContainerAlreadyExists:
                            flash(u'The Container %s is already created!'
                                  % name, 'error')
                        except subprocess.CalledProcessError as e:
                            flash(u'Error creating container %s: %s' % (name, e.output), 'error')

                elif storage_method == 'zfs':
                    zfs = request.form['zpoolname']

                    if re.match('^[a-zA-Z0-9_/-]+$', zfs) and zfs != '':
                        try:
                            lxc.create(name, template=template, storage='zfs --zfsroot %s' % zfs, xargs=command)
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

                    if re.match('^[a-zA-Z0-9_-]+$', lvname) and lvname != '':
                        storage_options += ' --lvname %s' % lvname
                    if re.match('^[a-zA-Z0-9_-]+$', vgname) and vgname != '':
                        storage_options += ' --vgname %s' % vgname
                    if re.match('^[a-z0-9]+$', fstype) and fstype != '':
                        storage_options += ' --fstype %s' % fstype
                    if re.match('^[1-9][0-9]*[G|M]$', fssize) and fssize != '':
                        storage_options += ' --fssize %s' % fssize

                    try:
                        lxc.create(name, template=template, storage=storage_options, xargs=command)
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


@app.route('/action/clone-container', methods=['GET', 'POST'])
def clone_container():
    '''
    verify all forms to clone a container
    '''
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

            if re.match('^(?!^containers$)|[a-zA-Z0-9_-]+$', name):
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


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        request_username = request.form['username']
        request_passwd = hash_passwd(request.form['password'])

        current_url = request.form['url']

        user = query_db('select name, username, su from users where username=?'
                        'and password=?', [request_username, request_passwd],
                        one=True)

        if user:
            session['logged_in'] = True
            session['token'] = get_token()
            session['last_activity'] = int(time.time())
            session['username'] = user['username']
            session['name'] = user['name']
            session['su'] = user['su']
            flash(u'You are logged in!', 'success')

            if current_url == url_for('login'):
                return redirect(url_for('home'))
            return redirect(current_url)

        flash(u'Invalid username or password!', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('token', None)
    session.pop('last_activity', None)
    session.pop('username', None)
    session.pop('name', None)
    session.pop('su', None)
    flash(u'You are logged out!', 'success')
    return redirect(url_for('login'))


@app.route('/_refresh_cpu_host')
def refresh_cpu_host():
    if 'logged_in' in session:
        return lwp.host_cpu_percent()


@app.route('/_refresh_uptime_host')
def refresh_uptime_host():
    if 'logged_in' in session:
        return jsonify(lwp.host_uptime())


@app.route('/_snapshot_info')
def snapshot_info():
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


@app.route('/_refresh_disk_host')
def refresh_disk_host():
    if 'logged_in' in session:
        return jsonify(lwp.host_disk_usage(partition=config.get('overview',
                                                                'partition')))


@app.route('/_refresh_memory_<name>')
def refresh_memory_containers(name=None):
    if 'logged_in' in session:
        if name == 'containers':
            try:
                containers_running = lxc.running()
            except Exception:
                containers_running = []
            containers = []
            for container in containers_running:
                container = container.replace(' (auto)', '')
                try:
                    memusg = lwp.memory_usage(container)
                except Exception:
                    memusg = 0
                containers.append({'name': container, 'memusg': memusg})
            return jsonify(data=containers)
        elif name == 'host':
            return jsonify(lwp.host_memory_usage())
        try:
            return jsonify({'memusg': lwp.memory_usage(name)})
        except Exception:
            return jsonify({'memusg': 0})


def hash_passwd(passwd):
    return hashlib.sha512(passwd.encode()).hexdigest()


def get_token():
    return hashlib.md5(str(time.time()).encode()).hexdigest()


def query_db(query, args=(), one=False):
    cur = g.db.execute(query, args)
    rv = [dict((cur.description[idx][0], value)
          for idx, value in enumerate(row)) for row in cur.fetchall()]
    return (rv[0] if rv else None) if one else rv


def check_session_limit():
    if 'logged_in' in session and session.get('last_activity') is not None:
        now = int(time.time())
        limit = now - 60 * int(config.get('session', 'time'))
        last_activity = session.get('last_activity')
        if last_activity < limit:
            flash(u'Session timed out !', 'info')
            logout()
        else:
            session['last_activity'] = now

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='LXC-Web panel')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='run with Flask debug mode (reloader and debugger)')
    args = parser.parse_args()
    debug = args.debug or app.config.get('DEBUG', False)
    app.run(host=app.config['ADDRESS'], port=app.config['PORT'], debug=debug)
