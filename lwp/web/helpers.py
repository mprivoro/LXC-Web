# Tiny helpers shared by Flask views. Needs an active request/app context.

from flask import current_app


def console_ok():
    '''True when flask-sock loaded and the attach console is wired up.'''

    return bool(current_app.config.get('CONSOLE_AVAILABLE'))
