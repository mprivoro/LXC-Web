# LXC host settings: /etc/default/lxc-net and lxc-checkconfig.

import lxclite as lxc
import lwp
from lwp.util import IPV4, RE_BYTE, RE_IFACE, matches
from flask import abort, flash, render_template, request, session


def lxc_net():
    '''GET/POST /etc/default/lxc-net (bridge DHCP). Only if no CT is running.'''

    if 'logged_in' in session:
        if session['su'] != 'Yes':
            return abort(403)

        if request.method == 'POST':
            if lxc.running() == []:
                cfg = lwp.get_net_settings()
                form = {}
                form['use'] = request.form.get('use', 'false')
                form['bridge'] = request.form.get('bridge')
                form['address'] = request.form.get('address')
                form['netmask'] = request.form.get('netmask')
                form['network'] = request.form.get('network')
                form['range'] = request.form.get('range')
                form['max'] = request.form.get('max')

                if form['use'] == 'true' and form['use'] != cfg['use']:
                    lwp.push_net_value('USE_LXC_BRIDGE', 'true')

                elif form['use'] == 'false' and form['use'] != cfg['use']:
                    lwp.push_net_value('USE_LXC_BRIDGE', 'false')

                if form['bridge'] and form['bridge'] != cfg['bridge'] \
                        and matches(RE_IFACE, form['bridge']):
                    lwp.push_net_value('LXC_BRIDGE', form['bridge'])

                if form['address'] and form['address'] != cfg['address'] \
                        and matches('^%s$' % IPV4, form['address']):
                    lwp.push_net_value('LXC_ADDR', form['address'])

                if form['netmask'] and form['netmask'] != cfg['netmask'] \
                        and matches('^%s$' % IPV4, form['netmask']):
                    lwp.push_net_value('LXC_NETMASK', form['netmask'])

                if form['network'] and form['network'] != cfg['network'] and \
                        matches('^%s(?:/\\d{1,2}|)$' % IPV4, form['network']):
                    lwp.push_net_value('LXC_NETWORK', form['network'])

                if form['range'] and form['range'] != cfg['range'] and \
                        matches('^%s,%s$' % (IPV4, IPV4), form['range']):
                    lwp.push_net_value('LXC_DHCP_RANGE', form['range'])

                if form['max'] and form['max'] != cfg['max'] and \
                        matches(RE_BYTE, form['max']):
                    lwp.push_net_value('LXC_DHCP_MAX', form['max'])

                if lwp.net_restart() == 0:
                    flash(u'LXC Network settings applied successfully!',
                          'success')
                else:
                    flash(u'Failed to restart LXC networking.', 'error')
            else:
                flash(u'Stop all containers before restart lxc-net.',
                      'warning')
        return render_template('lxc/net.html', containers=lxc.ls(),
                               cfg=lwp.get_net_settings(),
                               running=lxc.running())
    return render_template('login.html')


def checkconfig():
    '''Show output of lxc-checkconfig (kernel features).'''

    if 'logged_in' in session:
        if session['su'] != 'Yes':
            return abort(403)

        try:
            names = lxc.ls()
        except Exception:
            names = []
        return render_template('lxc/checkconfig.html', containers=names,
                               cfg=lxc.checkconfig())
    return render_template('login.html')


def register(app):
    '''Bind /settings/lxc-net and /checkconfig.'''

    app.add_url_rule('/settings/lxc-net', view_func=lxc_net, endpoint='lxc_net',
                     methods=['GET', 'POST'])
    app.add_url_rule('/checkconfig', view_func=checkconfig, endpoint='checkconfig')
