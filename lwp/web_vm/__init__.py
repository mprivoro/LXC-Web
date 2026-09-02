# VM panel HTTP routes under /virsh. Endpoint names are prefixed (vm_home, vm_edit).


def register(app):
    '''Overview, VM edit, actions, networks, checkconfig.'''

    from lwp.web_vm import actions, edit, overview, settings
    overview.register(app)
    edit.register(app)
    actions.register(app)
    settings.register(app)
