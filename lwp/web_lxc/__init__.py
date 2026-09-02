# HTTP routes for classic LXC containers (/, /<name>/edit, /action, …).
# Endpoint names stay unprefixed (url_for('home'), url_for('edit')).


def register(app):
    '''Overview, edit, actions, lxc-net, checkconfig.'''

    from lwp.web_lxc import actions, edit, overview, settings
    overview.register(app)
    edit.register(app)
    actions.register(app)
    settings.register(app)
