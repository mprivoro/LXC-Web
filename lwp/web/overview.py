# Overview, About, and host/container AJAX used by the home dashboard.

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
        inf = {'state': 'BROKEN', 'pid': '0', 'error': str(e)}
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
    try:
        diskusg = lwp.container_disk_usage(container)
    except Exception:
        diskusg = 0
    if inf.get('state') != 'BROKEN':
        try:
            memusg = lwp.memory_usage(container)
        except Exception:
            memusg = 0
        try:
            snaps = lxc.snapshots(container)
        except Exception:
            snaps = []
        if running:
            try:
                live = lxc.ip_addresses(container, True)
                if live['ipv4'] or live['ipv6']:
                    settings['ipv4_addrs'] = live['ipv4']
                    settings['ipv6_addrs'] = live['ipv6']
                    settings['ipv4'] = ' '.join(live['ipv4'])
                    settings['ipv6'] = ' '.join(live['ipv6'])
            except Exception:
                pass
    elif not error:
        error = 'LXC cannot load this container config.'

    return {
        'name': container,
        'memusg': memusg,
        'diskusg': diskusg,
        'settings': settings,
        'snapshots': snaps,
        'error': error,
    }


def home():
    '''Overview: containers by state, plus host cards. Unauthenticated -> login.'''

    if 'logged_in' in session:
        try:
            listx = lxc.listx()
        except Exception as e:
            flash(u'Unable to list containers: %s' % e, 'error')
            listx = {'RUNNING': [], 'FROZEN': [], 'STOPPED': [], 'BROKEN': []}

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
                    })
            containers_all.append({
                'status': status.lower(),
                'containers': containers_by_status
            })

        try:
            names = lxc.ls()
        except Exception:
            names = []

        return render_template('index.html', containers=names,
                               containers_all=containers_all,
                               dist=lwp.check_ubuntu(),
                               templates=lwp.get_templates_list(),
                               images=lwp.get_cached_images(),
                               console_available=console_ok())
    return render_template('login.html')


def about():
    '''About page with the local LWP version.'''

    if 'logged_in' in session:
        return render_template('about.html', containers=lxc.ls(),
                               version=lwp.check_version())
    return render_template('login.html')


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



def register(app):
    '''Bind Overview, About, and /_refresh_* AJAX URLs.'''

    app.add_url_rule('/', view_func=home, endpoint='home')
    app.add_url_rule('/home', view_func=home, endpoint='home')
    app.add_url_rule('/about', view_func=about, endpoint='about')
    app.add_url_rule('/_refresh_cpu_host', view_func=refresh_cpu_host,
                     endpoint='refresh_cpu_host')
    app.add_url_rule('/_refresh_uptime_host', view_func=refresh_uptime_host,
                     endpoint='refresh_uptime_host')
    app.add_url_rule('/_refresh_disk_host', view_func=refresh_disk_host,
                     endpoint='refresh_disk_host')
    app.add_url_rule('/_refresh_memory_<name>',
                     view_func=refresh_memory_containers,
                     endpoint='refresh_memory_containers')
