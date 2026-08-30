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

# Flask entry point: python3 lwp.py
# Loads lwp.conf, builds the app, registers HTTP routes (lwp/web/) and the
# optional WebSocket console, then app.run(). Views are not in this file.

import argparse
import sys

import lxclite as lxc
import lwp
import lwp.ctlog as ctlog
from lwp.auth import check_session_limit, connect_db
from lwp.util import format_qty
from lwp.web import register as register_routes
from lwp.web.session_views import logout

from flask import Flask, g

try:
    import configparser
except ImportError:
    import ConfigParser as configparser

# configuration
config = configparser.ConfigParser()
with open('lwp.conf') as fh:
    config.read_file(fh)

SECRET_KEY = config.get('session', 'secret_key', raw=True)
DEBUG = config.getboolean('global', 'debug')
DATABASE = config.get('database', 'file')
ADDRESS = config.get('global', 'address')
PORT = int(config.get('global', 'port'))
try:
    CONTAINER_LOG = config.get('logging', 'file', raw=True).strip()
except (configparser.NoSectionError, configparser.NoOptionError):
    CONTAINER_LOG = 'lwp-containers.log'
ctlog.init(CONTAINER_LOG)
try:
    OVERVIEW_PARTITION = config.get('overview', 'partition')
except (configparser.NoSectionError, configparser.NoOptionError):
    OVERVIEW_PARTITION = '/'


# Flask app
app = Flask(__name__)
app.config.from_object(sys.modules[__name__])
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
app.add_template_filter(format_qty, 'qty')

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
app.config['CONSOLE_AVAILABLE'] = CONSOLE_AVAILABLE
app.config['OVERVIEW_PARTITION'] = OVERVIEW_PARTITION

register_routes(app)


@app.before_request
def before_request():
    '''Expire idle sessions, then open SQLite for this request.'''

    check_session_limit(int(config.get('session', 'time')), logout)
    g.db = connect_db(app.config['DATABASE'])


@app.teardown_request
def teardown_request(exception):
    '''Close the request-scoped database connection.'''

    if hasattr(g, 'db'):
        g.db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='LXC-Web panel')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='run with Flask debug mode (reloader and debugger)')
    args = parser.parse_args()
    debug = args.debug or app.config.get('DEBUG', False)
    app.run(host=app.config['ADDRESS'], port=app.config['PORT'], debug=debug)
