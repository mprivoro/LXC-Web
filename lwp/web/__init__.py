# Shared Flask HTTP: login, users, log, about, host stats.
# LXC routes live in lwp.web_lxc; VM routes in lwp.web_vm.
# Not a Blueprint: endpoint names stay unprefixed so templates do not change.


def register(app):
    '''Attach shared pages, then LXC and VM panels.'''

    from lwp.web import host, panel, session_views
    from lwp.web_lxc import register as register_lxc
    from lwp.web_vm import register as register_vm
    session_views.register(app)
    panel.register(app)
    host.register(app)
    register_lxc(app)
    register_vm(app)
