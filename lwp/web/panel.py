# Su-only panel pages: command log, users, lxc-net, lxc-checkconfig.

import lxclite as lxc
import lwp
import lwp.ctlog as ctlog
from lwp.auth import hash_passwd, query_db
from lwp.util import (IPV4, RE_BYTE, RE_DISPLAY_NAME, RE_IFACE, RE_USERNAME,
                      matches)
from flask import abort, flash, redirect, render_template, request, session, url_for


def container_log():
    '''Show the container command log (su).'''

    if 'logged_in' not in session:
        return render_template('login.html')
    if session.get('su') != 'Yes':
        return abort(403)
    try:
        names = lxc.ls()
    except Exception:
        names = []
    return render_template('log.html', containers=names, log=ctlog.read_log())


def lxc_net():
    '''GET/POST /etc/default/lxc-net (bridge DHCP). Only if no CT is running.'''

    if 'logged_in' in session:
        if session['su'] != 'Yes':
            return abort(403)

        if request.method == 'POST':
            if lxc.running() == []:
                cfg = lwp.get_net_settings()
                form = {}
                form['use'] = request.form.get('use', 'false')
                form['bridge'] = request.form.get('bridge')
                form['address'] = request.form.get('address')
                form['netmask'] = request.form.get('netmask')
                form['network'] = request.form.get('network')
                form['range'] = request.form.get('range')
                form['max'] = request.form.get('max')

                if form['use'] == 'true' and form['use'] != cfg['use']:
                    lwp.push_net_value('USE_LXC_BRIDGE', 'true')

                elif form['use'] == 'false' and form['use'] != cfg['use']:
                    lwp.push_net_value('USE_LXC_BRIDGE', 'false')

                if form['bridge'] and form['bridge'] != cfg['bridge'] \
                        and matches(RE_IFACE, form['bridge']):
                    lwp.push_net_value('LXC_BRIDGE', form['bridge'])

                if form['address'] and form['address'] != cfg['address'] \
                        and matches('^%s$' % IPV4, form['address']):
                    lwp.push_net_value('LXC_ADDR', form['address'])

                if form['netmask'] and form['netmask'] != cfg['netmask'] \
                        and matches('^%s$' % IPV4, form['netmask']):
                    lwp.push_net_value('LXC_NETMASK', form['netmask'])

                if form['network'] and form['network'] != cfg['network'] and \
                        matches('^%s(?:/\\d{1,2}|)$' % IPV4, form['network']):
                    lwp.push_net_value('LXC_NETWORK', form['network'])

                if form['range'] and form['range'] != cfg['range'] and \
                        matches('^%s,%s$' % (IPV4, IPV4), form['range']):
                    lwp.push_net_value('LXC_DHCP_RANGE', form['range'])

                if form['max'] and form['max'] != cfg['max'] and \
                        matches(RE_BYTE, form['max']):
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


def lwp_users():
    '''List, add, edit, delete panel users in SQLite (su only).'''

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
                    if matches(RE_USERNAME, request.form['username']) and \
                            request.form['password1']:
                        if request.form['password1'] == \
                                request.form['password2']:
                            if request.form['name']:
                                if matches(RE_DISPLAY_NAME, request.form['name']):
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
                    if matches(RE_DISPLAY_NAME, request.form['name']):
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


def checkconfig():
    '''Show output of lxc-checkconfig (kernel features).'''

    if 'logged_in' in session:
        if session['su'] != 'Yes':
            return abort(403)

        return render_template('checkconfig.html', containers=lxc.ls(),
                               cfg=lxc.checkconfig())
    return render_template('login.html')



def register(app):
    '''Bind /lwp/log, /lwp/users, /settings/lxc-net, /checkconfig.'''

    app.add_url_rule('/lwp/log', view_func=container_log, endpoint='container_log')
    app.add_url_rule('/settings/lxc-net', view_func=lxc_net, endpoint='lxc_net',
                     methods=['GET', 'POST'])
    app.add_url_rule('/lwp/users', view_func=lwp_users, endpoint='lwp_users',
                     methods=['GET', 'POST'])
    app.add_url_rule('/checkconfig', view_func=checkconfig, endpoint='checkconfig')
