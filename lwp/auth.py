# Session, passwords, SQLite helpers.
# Used by login and before_request. query_db needs Flask g.db already open.

import hashlib
import sqlite3
import time

from flask import g, session, flash


def connect_db(path):
    '''Open the panel SQLite file (users table).'''

    return sqlite3.connect(path)


def hash_passwd(passwd):
    '''SHA-512 hex of the password (how rows in users are stored).'''

    return hashlib.sha512(passwd.encode()).hexdigest()


def get_token():
    '''CSRF-ish token stored in the session after login.'''

    return hashlib.md5(str(time.time()).encode()).hexdigest()


def query_db(query, args=(), one=False):
    '''Run SQL on g.db; rows as dicts. one=True returns a single row or None.'''

    cur = g.db.execute(query, args)
    rv = [dict((cur.description[idx][0], value)
          for idx, value in enumerate(row)) for row in cur.fetchall()]
    return (rv[0] if rv else None) if one else rv


def check_session_limit(minutes, logout_func):
    '''Log out if idle longer than minutes; otherwise refresh last_activity.'''

    if 'logged_in' not in session or session.get('last_activity') is None:
        return
    now = int(time.time())
    limit = now - 60 * int(minutes)
    if session.get('last_activity') < limit:
        flash(u'Session timed out !', 'info')
        logout_func()
    else:
        session['last_activity'] = now
