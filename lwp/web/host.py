# Host dashboard AJAX (CPU, uptime, disk). Shared by Overview LXC and Overview VMs.

import lwp
from flask import current_app, jsonify, session


def refresh_cpu_host():
    '''JSON: host CPU %, load 1/5/15, CPU count.'''

    if 'logged_in' in session:
        return jsonify(lwp.host_cpu_usage())


def refresh_uptime_host():
    '''JSON: host uptime days + HH:MM.'''

    if 'logged_in' in session:
        return jsonify(lwp.host_uptime())


def refresh_disk_host():
    '''JSON: df of the partition from lwp.conf [overview].'''

    if 'logged_in' in session:
        partition = current_app.config.get('OVERVIEW_PARTITION', '/')
        return jsonify(lwp.host_disk_usage(partition=partition))


def register(app):
    '''Bind /_refresh_cpu_host, /_refresh_uptime_host, /_refresh_disk_host.'''

    app.add_url_rule('/_refresh_cpu_host', view_func=refresh_cpu_host,
                     endpoint='refresh_cpu_host')
    app.add_url_rule('/_refresh_uptime_host', view_func=refresh_uptime_host,
                     endpoint='refresh_uptime_host')
    app.add_url_rule('/_refresh_disk_host', view_func=refresh_disk_host,
                     endpoint='refresh_disk_host')
