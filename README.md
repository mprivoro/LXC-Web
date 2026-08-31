# LXC-Web

A web panel for **classic LXC** containers — the ones you manage with `lxc-start`, `lxc-stop`, and friends.

This is not a panel for LXD or Incus.

[github.com/mprivoro/LXC-Web](https://github.com/mprivoro/LXC-Web)

## Containers

Open the panel in a browser and work with the containers that already live on the host.

- See every container at a glance: running, stopped, frozen, or broken, with IP, disk, CPU, and network
- Start, stop, restart, freeze, and unfreeze
- Create from a cached image or a template
- Clone and destroy
- Snapshots: create, restore, delete
- Edit settings or the raw config (a backup is kept)
- Attach a console
- Host overview: CPU, RAM, disk, uptime
- A command log of who changed what (panel or MCP)

## MCP

The same process also speaks MCP, so an agent or IDE can manage those containers without the browser.

It can list and inspect containers, read and change config, start / stop / restart / freeze, take and restore snapshots, clone, create, and destroy.

**How to connect**

1. Panel is running (`python3 lwp.py`). MCP listens at the URL in `lwp.conf` (`[mcp] url`), by default `http://<host>:5005/mcp`.
2. Copy a token from **Users** in the panel (shown once when you create a user or click Regenerate).
3. Point the client at that URL and send the token as a Bearer header.

```json
{
  "url": "http://HOST:5005/mcp",
  "headers": {
    "Authorization": "Bearer YOUR_TOKEN"
  }
}
```

Admin tokens can change containers. A non-admin user token, or the default key in `lwp.conf`, is read-only (list and inspect only).

For a locally spawned client, `python3 -m lwp.mcp_server --stdio` is the stdio transport. To run only the website: `python3 lwp.py --no-mcp`.

## Users

Accounts live in the panel (**Users**, admin only).

- **Admin** — full UI, and an MCP token that can change containers
- **Regular user** — signed-in access to the panel; MCP token is read-only

Each user has an MCP token. It is stored hashed. Copy it when it is shown — it cannot be displayed again. Lost token: Regenerate.

Default login after install: **admin** / **admin**. Change that password.

## Install

Needs a Linux host with **LXC** already working, **Python 3**, and **root** (the panel talks to the LXC store on the machine).

**Host**

- LXC (`lxc-*` tools)
- Python 3 and pip
- Python **3.10+** if you want MCP (the panel itself runs on 3.8+)

**Python** (`requirements.txt`)

- Flask — web UI
- flask-sock, simple-websocket, ptyprocess — console
- mcp — MCP server (skipped automatically on Python older than 3.10)

Without the console extras the panel still starts (no Console button). Without `mcp` the panel still starts (no MCP port).

```bash
git clone https://github.com/mprivoro/LXC-Web.git
cd LXC-Web
apt install -y python3 python3-pip lxc
pip3 install -r requirements.txt
python3 lwp.py
```

Then open `http://<host>:5000/` and sign in as admin.

Bind address and port are in `lwp.conf`. Change `secret_key` on each install.

```bash
cd /path/to/LXC-Web
git pull
python3 lwp.py
```

Restart the panel after pulling.

---

Fork of [LXC Web Panel](https://github.com/lxc-webpanel/LXC-Web-Panel) (archived). MIT license.
