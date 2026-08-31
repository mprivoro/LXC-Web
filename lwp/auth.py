# Session, passwords, SQLite helpers.
# Used by login and before_request. query_db needs Flask g.db already open.

import hashlib
import secrets
import sqlite3
import time

from flask import g, session, flash


def connect_db(path):
    '''Open the panel SQLite file (users table).'''

    conn = sqlite3.connect(path)
    ensure_users_schema(conn)
    return conn


def hash_mcp_token(token):
    '''SHA-256 hex of an MCP token. That is what SQLite stores.'''

    if not token:
        return ''
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _mcp_token_is_hashed(value):
    '''True if value looks like SHA-256 hex (not a live token).'''

    if not value or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def new_mcp_token(conn=None):
    '''Fresh plaintext MCP token. Store hash_mcp_token(token), not this string.'''

    for _ in range(24):
        token = secrets.token_urlsafe(32)
        if conn is None:
            return token
        digest = hash_mcp_token(token)
        cur = conn.execute(
            'SELECT COUNT(*) FROM users WHERE mcp_token=?', (digest,))
        if cur.fetchone()[0] == 0:
            return token
    raise RuntimeError('Could not allocate a unique MCP token.')


def ensure_users_schema(conn):
    '''Add users.mcp_token, hash any leftover plaintext, fill blanks.'''

    cols = [row[1] for row in conn.execute('PRAGMA table_info(users)')]
    if 'mcp_token' not in cols:
        conn.execute('ALTER TABLE users ADD COLUMN mcp_token TEXT')
        conn.commit()
    rows = conn.execute('SELECT id, mcp_token FROM users').fetchall()
    changed = False
    for user_id, token in rows:
        raw = (token or '').strip()
        if not raw:
            digest = hash_mcp_token(new_mcp_token(conn))
            conn.execute(
                'UPDATE users SET mcp_token=? WHERE id=?',
                (digest, user_id))
            changed = True
        elif not _mcp_token_is_hashed(raw):
            conn.execute(
                'UPDATE users SET mcp_token=? WHERE id=?',
                (hash_mcp_token(raw), user_id))
            changed = True
    if changed:
        conn.commit()


def lookup_mcp_user(conn, token):
    '''User for this plaintext MCP token, or None. Matches against the hash.'''

    if not token:
        return None
    digest = hash_mcp_token(token)
    cur = conn.execute(
        'SELECT id, username, su FROM users WHERE mcp_token=?',
        (digest,))
    row = cur.fetchone()
    if not row:
        return None
    return {
        'id': row[0],
        'username': row[1],
        'su': row[2] or 'No',
    }


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
