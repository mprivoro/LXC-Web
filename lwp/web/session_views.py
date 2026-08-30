# Login and logout views. Sets session keys used by every other page.

import time

from lwp.auth import get_token, hash_passwd, query_db
from flask import flash, redirect, render_template, request, session, url_for


def login():
    '''Show the form, or authenticate and start a session.'''

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


def logout():
    '''Clear the session and send the browser to /login.'''

    session.pop('logged_in', None)
    session.pop('token', None)
    session.pop('last_activity', None)
    session.pop('username', None)
    session.pop('name', None)
    session.pop('su', None)
    flash(u'You are logged out!', 'success')
    return redirect(url_for('login'))



def register(app):
    '''Bind /login and /logout.'''

    app.add_url_rule('/login', view_func=login, endpoint='login',
                     methods=['GET', 'POST'])
    app.add_url_rule('/logout', view_func=logout, endpoint='logout')
