# LXC-Web

Web UI for managing LXC containers.

Fork of [lxc-webpanel/LXC-Web-Panel](https://github.com/lxc-webpanel/LXC-Web-Panel) (archived 2020). This copy adds Python 3 and current LXC support.

## Get it

```bash
git clone https://github.com/mprivoro/LXC-Web.git
cd LXC-Web
```

## Install / run

Needs Python 3, Flask, and LXC. Run as root so the panel can read `/var/lib/lxc`.

```bash
apt install -y python3 python3-flask lxc
python3 lwp.py
```

Listen address and port are in `lwp.conf` (default `0.0.0.0:5000`).

## Update

```bash
cd /path/to/LXC-Web
git pull
python3 lwp.py
```

Restart the panel after pulling.

## Login

| | |
|---|---|
| URL | `http://<host-ip>:5000/` |
| User | `admin` |
| Password | `admin` |

Change the admin password after the first login.
