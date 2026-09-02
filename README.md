# NoDeck

**NoDE DEck - deduplicated:** A lightweight web control plane for libvirt and LXC.

Classic LXC — `lxc-start`, `lxc-stop`, and friends — and KVM/QEMU through
libvirt (`virsh`). Not LXD or Incus.

[github.com/mprivoro/NoDeck](https://github.com/mprivoro/NoDeck)

## Containers

Open the panel in a browser and work with the containers that already live
on the host.

- See every container at a glance: running, stopped, frozen, or broken
- IPv4 and IPv6, live RAM, disk of the live rootfs (snapshot trees are
  listed separately, with their own sizes)
- CPU as percent of one core, with the share of all host CPUs in
  parentheses; network rates since the last refresh
- Start, stop, restart, freeze, and unfreeze
- Start selected / stop selected (bulk)
- Start at boot
- Create from a cached `lxc-download` image (nothing is fetched) or from a
  template (`download`, `local`, `busybox`, `oci`, …); optional backing
  store (directory, LVM, ZFS)
- Clone and destroy
- Snapshots: create, restore (in place or to a new name), delete; a live
  snapshot of a running container is allowed when you ask for it
- Edit settings or the raw config (a `config.bak` is kept; restore from
  that backup)
- Attach a console (`lxc-attach` in the browser)

## VMs

The same process, same login, same MCP. The sidebar has two Overview
links: **Overview LXC** (`/`) and **Overview VMs** (`/virsh`). Libvirt URI
is `[vm] uri` in `lwp.conf` (default `qemu:///system`). New VM disks go
under `[vm] disk` as `{disk}/{name}/{name}.qcow2` unless you set another
path in the create form.

- List VMs: running, paused, shut off, or broken
- Start, ACPI shutdown, power off, pause / resume
- Create: memory, vCPUs, host CPU or a named model, qcow2 size, boot order
  (disk, ISO, PXE, floppy), ISO path, libvirt network or bridge, optional
  static IP, autostart — or paste domain XML
- Clone (`virt-clone`); undefine (disks kept)
- Edit title, domain type (`kvm` / `qemu`), boot devices, ISO, memory,
  vCPUs, autostart
- Snapshots, domain XML (a backup is kept), serial console (`virsh console`)

## Host and panel

- Overview cards: CPU, RAM (used and cache), disk of the partition in
  `[overview] partition`, uptime
- Auto-refresh (seconds in `[overview] refresh`) and a manual refresh
- Reboot the host (admin)
- LXC networking (`lxc-net`) and libvirt networks (start / stop / autostart)
- Check config: kernel LXC features, and KVM / libvirt
- A command log of who changed what (panel or MCP), and a separate MCP
  request log

## MCP

The same process also speaks MCP, so an agent or IDE can manage those
containers and VMs without the browser.

It can list and inspect containers and VMs, read and change config / XML,
start / stop / restart / freeze (pause), take and restore snapshots
(including a live snapshot when allowed), clone, create, and destroy. It
also exposes host stats, cached images / templates, field-level
`set_config` / `set_vm`, restore of a config backup, and JSON/text
resources under `lxc://containers`.

**How to connect**

1. Panel is running (`python3 lwp.py`). MCP listens at the URL in
   `lwp.conf` (`[mcp] url`), by default `http://<host>:5005/mcp`.
2. Copy a token from **Users** in the panel (shown once when you create a
   user or click Regenerate).
3. Point the client at that URL and send the token as a Bearer header.

```json
{
  "url": "http://HOST:5005/mcp",
  "headers": {
    "Authorization": "Bearer YOUR_TOKEN"
  }
}
```

Admin tokens can change containers and VMs. A non-admin user token, or the
default key in `lwp.conf`, is read-only (list and inspect only).

For a locally spawned client, `python3 -m lwp.mcp_server --stdio` is the
stdio transport (LXC and virsh tools). To run only the website:
`python3 lwp.py --no-mcp`.

## Users

Accounts live in the panel (**Users**, admin only).

- **Admin** — full UI, and an MCP token that can change containers and VMs
- **Regular user** — signed-in access to the panel; MCP token is read-only

Each user has an MCP token. It is stored hashed. Copy it when it is shown
— it cannot be displayed again. Lost token: Regenerate.

Default login after install: **admin** / **admin**. Change that password.

## Install

Needs a Linux host with **LXC** and/or **libvirt** already working,
**Python 3**, and **root**.

**Host**

- LXC (`lxc-*` tools) for containers
- libvirt / `virsh` (and `virt-clone` to clone) for the virsh Overview
- Python 3 and pip
- Python **3.10+** if you want MCP (the panel itself runs on 3.8+)

**Python** (`requirements.txt`)

- Flask — web UI
- flask-sock, simple-websocket, ptyprocess — console
- mcp, uvicorn, anyio — MCP HTTP server (skipped automatically on Python
  older than 3.10)

Without the console extras the panel still starts (no Console button).
Without `mcp` the panel still starts (no MCP port).

```bash
git clone https://github.com/mprivoro/NoDeck.git
cd NoDeck
apt install -y python3 python3-pip lxc
pip3 install -r requirements.txt
python3 lwp.py
```

Then open `http://<host>:5000/` and sign in as admin. Overview LXC is `/`;
Overview VMs is `/virsh`.

Bind address and port are in `lwp.conf`. Change `secret_key` on each
install.

```bash
cd /path/to/NoDeck
git pull
python3 lwp.py
```

Restart the panel after pulling.

---

Fork of [LXC Web Panel](https://github.com/lxc-webpanel/LXC-Web-Panel)
(archived). MIT license.
