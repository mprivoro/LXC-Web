# LXC Overview (/) and container AJAX used by the home dashboard.

import lxclite as lxc
import lwp
from lwp.web.helpers import console_ok
from flask import current_app, flash, jsonify, render_template, session


def _overview_row(container, running=False):
    '''
    One Overview row. A killed/invalid config must not raise.
    '''

    error = ''
    try:
        inf = lxc.info(container)
    except Exception as e:
        inf = {'state': 'BROKEN', 'pid': '0', 'error': str(e), 'links': []}
    error = inf.get('error') or ''

    try:
        settings = lwp.get_container_settings(container)
    except Exception as e:
        settings = lwp.empty_container_settings(str(e))
        error = error or str(e)
    if not settings:
        settings = lwp.empty_container_settings(
            'Config file is missing or unreadable.')
        error = error or settings['config_error']
    else:
        error = error or settings.get('config_error') or ''

    memusg = 0
    diskusg = 0
    snaps = []
    live = lwp.empty_live_metrics()
    try:
        diskusg = lwp.container_disk_usage(container)
    except Exception:
        diskusg = 0
    if inf.get('state') != 'BROKEN':
        try:
            memusg = lwp.memory_usage(
                container,
                known_live=(inf.get('state') in ('RUNNING', 'FROZEN')))
        except Exception:
            memusg = 0
        try:
            snaps = lxc.snapshots(container)
        except Exception:
            snaps = []
        if inf.get('state') in ('RUNNING', 'FROZEN'):
            try:
                live = lwp.container_live_metrics(
                    container, inf.get('links') or [])
            except Exception:
                live = lwp.empty_live_metrics()
            if running:
                try:
                    addrs = lxc.ip_addresses(container, True)
                    if addrs['ipv4'] or addrs['ipv6']:
                        settings['ipv4_addrs'] = addrs['ipv4']
                        settings['ipv6_addrs'] = addrs['ipv6']
                        settings['ipv4'] = ' '.join(addrs['ipv4'])
                        settings['ipv6'] = ' '.join(addrs['ipv6'])
                except Exception:
                    pass
        else:
            lwp.forget_live_sample(container)
    elif not error:
        error = 'LXC cannot load this container config.'

    row = {
        'name': container,
        'memusg': memusg,
        'diskusg': diskusg,
        'settings': settings,
        'snapshots': snaps,
        'error': error,
    }
    row.update(live)
    return row


def _overview_groups(listx):
    '''Turn lxc.listx() into the containers_all structure the templates use.'''

    containers_all = []
    for status in ['RUNNING', 'FROZEN', 'STOPPED', 'BROKEN']:
        containers_by_status = []
        running = (status == 'RUNNING')
        for container in listx.get(status, []):
            try:
                containers_by_status.append(
                    _overview_row(container, running))
            except Exception as e:
                containers_by_status.append({
                    'name': container,
                    'memusg': 0,
                    'diskusg': 0,
                    'settings': lwp.empty_container_settings(str(e)),
                    'snapshots': [],
                    'error': str(e),
                    **lwp.empty_live_metrics(),
                })
        containers_all.append({
            'status': status.lower(),
            'containers': containers_by_status
        })
    return containers_all


def home():
    '''Overview: containers by state, plus host cards. Unauthenticated -> login.'''

    if 'logged_in' in session:
        try:
            listx = lxc.listx()
        except Exception as e:
            flash(u'Unable to list containers: %s' % e, 'error')
            listx = {'RUNNING': [], 'FROZEN': [], 'STOPPED': [], 'BROKEN': []}

        containers_all = _overview_groups(listx)

        try:
            names = lxc.ls()
        except Exception:
            names = []

        return render_template('index.html', containers=names,
                               containers_all=containers_all,
                               dist=lwp.check_ubuntu(),
                               templates=lwp.get_templates_list(),
                               images=lwp.get_cached_images(),
                               images_dir=lwp.images_download_dir(),
                               lxc_conf=lxc.lxc_conf_path(),
                               lxcpath=lxc.lxcpath(),
                               console_available=console_ok(),
                               overview_refresh=current_app.config.get(
                                   'OVERVIEW_REFRESH', 60))
    return render_template('login.html')


def refresh_memory_containers(name=None):
    '''JSON RAM: all running CTs, the host, or one container (URL suffix).'''

    if 'logged_in' in session:
        if name == 'containers':
            try:
                containers_running = lxc.running()
            except Exception:
                containers_running = []
            containers = []
            for container in containers_running:
                container = container.replace(' (auto)', '')
                try:
                    memusg = lwp.memory_usage(container)
                except Exception:
                    memusg = 0
                containers.append({'name': container, 'memusg': memusg})
            return jsonify(data=containers)
        elif name == 'host':
            return jsonify(lwp.host_memory_usage())
        try:
            return jsonify({'memusg': lwp.memory_usage(name)})
        except Exception:
            return jsonify({'memusg': 0})


def refresh_overview():
    '''JSON HTML fragments: header counts + CT tables (same markup as home).'''

    if 'logged_in' not in session:
        return jsonify({}), 401
    try:
        listx = lxc.listx()
    except Exception:
        listx = {'RUNNING': [], 'FROZEN': [], 'STOPPED': [], 'BROKEN': []}
    containers_all = _overview_groups(listx)
    return jsonify(
        counts=render_template('includes/overview_counts.html',
                               containers_all=containers_all),
        live=render_template('includes/overview_live.html',
                             containers_all=containers_all,
                             console_available=console_ok()),
    )


def register(app):
    '''Bind LXC Overview and container /_refresh_* AJAX URLs.'''

    app.add_url_rule('/', view_func=home, endpoint='home')
    app.add_url_rule('/home', view_func=home, endpoint='home')
    app.add_url_rule('/_refresh_memory_<name>',
                     view_func=refresh_memory_containers,
                     endpoint='refresh_memory_containers')
    app.add_url_rule('/_refresh_overview', view_func=refresh_overview,
                     endpoint='refresh_overview')
