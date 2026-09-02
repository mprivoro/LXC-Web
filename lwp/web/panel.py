# Shared su-only pages: command log, users, about.
# Sidebar names come from lwp.py inject_panel (lxc_names / vm_names).

import lwp
import lwp.ctlog as ctlog
from lwp.auth import hash_mcp_token, hash_passwd, new_mcp_token, query_db
from lwp.util import (RE_DISPLAY_NAME, RE_USERNAME, matches)
from flask import abort, flash, g, redirect, render_template, request, session, url_for


def container_log():
    '''Show the container command log or the MCP request log (su).'''

    if 'logged_in' not in session:
        return render_template('login.html')
    if session.get('su') != 'Yes':
        return abort(403)
    view = request.args.get('view', '')
    if view == 'mcp':
        log = ctlog.read_mcp_log()
    else:
        view = 'containers'
        log = ctlog.read_log()
    return render_template('log.html', log=log, view=view)


def about():
    '''About page with the local NoDeck version.'''

    if 'logged_in' in session:
        return render_template('about.html', version=lwp.check_version())
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

            if request.form.get('mcp_action') == 'regenerate':
                username = request.form.get('username', '')
                if username in [user['username'] for user in users]:
                    token = new_mcp_token(g.db)
                    g.db.execute(
                        'UPDATE users SET mcp_token=? WHERE username=?',
                        [hash_mcp_token(token), username])
                    g.db.commit()
                    flash(u'New MCP token for %s (copy now — it cannot be '
                          u'shown again): %s' % (username, token),
                          'success dont-hide')
                else:
                    flash(u'Unknown user.', 'error')
                return redirect(url_for('lwp_users'))

            if request.form['newUser'] == 'True':
                if not request.form['username'] in \
                        [user['username'] for user in users]:
                    if matches(RE_USERNAME, request.form['username']) and \
                            request.form['password1']:
                        if request.form['password1'] == \
                                request.form['password2']:
                            created = False
                            raw_token = None
                            if request.form['name']:
                                if matches(RE_DISPLAY_NAME, request.form['name']):
                                    raw_token = new_mcp_token(g.db)
                                    g.db.execute(
                                        "INSERT INTO users "
                                        "(name, username, password, mcp_token) "
                                        "VALUES (?, ?, ?, ?)",
                                        [request.form['name'],
                                         request.form['username'],
                                         hash_passwd(
                                             request.form['password1']),
                                         hash_mcp_token(raw_token)])
                                    g.db.commit()
                                    created = True
                                else:
                                    flash(u'Invalid name!', 'error')
                            else:
                                raw_token = new_mcp_token(g.db)
                                g.db.execute("INSERT INTO users "
                                             "(username, password, mcp_token) "
                                             "VALUES (?, ?, ?)",
                                             [request.form['username'],
                                              hash_passwd(
                                                  request.form['password1']),
                                              hash_mcp_token(raw_token)])
                                g.db.commit()
                                created = True

                            if created:
                                flash(u'Created %s. MCP token (copy now — '
                                      u'it cannot be shown again): %s' %
                                      (request.form['username'], raw_token),
                                      'success dont-hide')
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

        return render_template('users.html', users=users,
                               nb_users=nb_users, su_users=su_users)
    return render_template('login.html')


def register(app):
    '''Bind /lwp/log, /lwp/users, /about.'''

    app.add_url_rule('/lwp/log', view_func=container_log, endpoint='container_log')
    app.add_url_rule('/lwp/users', view_func=lwp_users, endpoint='lwp_users',
                     methods=['GET', 'POST'])
    app.add_url_rule('/about', view_func=about, endpoint='about')
