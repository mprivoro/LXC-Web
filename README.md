# LXC-Web

Web UI for **LXC** (`lxc-*` tools, containers under `/var/lib/lxc`).

This is not a UI for **LXD** or **Incus**. Those are separate projects and already have their own panels. LXC-Web talks to classic LXC only.

Fork of [lxc-webpanel/LXC-Web-Panel](https://github.com/lxc-webpanel/LXC-Web-Panel) (archived 2020). This copy adds Python 3 and current LXC support.

## Supported

- Python 3 + Flask, LXC on the host (Ubuntu / current LXC).
- cgroup v1 and v2; old 0.8/0.9 keys and current `lxc.net.0.*` / `lxc.uts.name` / `lxc.rootfs.path`
- Overview: start / stop / freeze, clone, destroy
- Create CT from cached `lxc-download` images or templates
- Edit: form fields and raw `config` (with backup)
- Snapshots: list, create, restore, destroy
- Attach console (`lxc-attach`) if `flask-sock` is installed
- Broken configs shown as Broken instead of taking the site down
- Users, `lxc-net`, host reboot

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

Listen address, port, and `secret_key` are in `lwp.conf` (default `0.0.0.0:5000`). Change `secret_key` on each install. Container start/stop/create/snapshot/config commands are appended to the file in `[logging] file` (default `lwp-containers.log`).

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
