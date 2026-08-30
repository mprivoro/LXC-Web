# HTTP route table for the panel.
# Not a Flask Blueprint: endpoint names stay unprefixed
# (url_for('home'), not url_for('web.home')) so templates do not change.


def register(app):
    '''Attach Overview, Edit, panel, actions, and login URLs to the app.'''

    from lwp.web import actions, edit, overview, panel, session_views
    overview.register(app)
    edit.register(app)
    panel.register(app)
    actions.register(app)
    session_views.register(app)
