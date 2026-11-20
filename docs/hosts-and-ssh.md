# Servers over SSH

This page explains what Eichhorn needs to back up a server over SSH, and how it uses sudo.

Eichhorn runs on your computer and **pulls** from the server. Nothing is installed on the
server, and the server never connects to your computer.

## Requirements on the server

| Tool | Needed for |
|---|---|
| SSH login with a **key** | everything. Eichhorn uses your `ssh` and `~/.ssh/config` and never asks for passwords. |
| `find` (GNU) or `python3` | listing files |
| `tar`, `cat`, `tail` | copying files with the builtin engine |
| `rsync` 3.1 or newer | the rsync engine (optional) |
| `docker` | dumps from databases in containers, and most hooks |

All of these are standard on Linux servers. `eichhorn doctor` checks them:

```bash
eichhorn doctor nextcloud
```

```text
nextcloud  (web1)
  ✓ SSH login: jan@web1 (Linux)
  ✓ file listing: GNU find
  ✓ tar: /usr/bin/tar
  ✓ rsync on host: 3.2.7
  ✓ sudo: works without a password
  ✓ source data: /srv/nextcloud/html/data
  ✓ dump nextcloud: container: nextcloud-db-1
  ✓ dump nextcloud: database: reachable
```

## The SSH connection

Set up a host alias in `~/.ssh/config` and use it as `host:` in the target:

```text
Host web1
    HostName web1.example.org
    User jan
    IdentityFile ~/.ssh/id_ed25519
```

If your key has a passphrase, load it into the agent before a run
(`ssh-add ~/.ssh/id_ed25519`; on macOS add `--apple-use-keychain` to remember it).

On macOS and Linux, Eichhorn reuses one SSH connection for the many small commands of a
run (`ControlMaster`), which makes runs much faster.

## sudo

App data on servers usually belongs to root or to the app's user (`www-data`, uid 33, …).
With `sudo: true` in the target, Eichhorn runs the file listing, the transfer, the dumps and
the hooks with `sudo -n` (never interactively).

Allow these commands without a password, for example in `/etc/sudoers.d/eichhorn`
(edit with `sudo visudo -f /etc/sudoers.d/eichhorn`):

```text
jan ALL=(root) NOPASSWD: /usr/bin/rsync, /usr/bin/tar, /usr/bin/find, /usr/bin/cat, /usr/bin/tail, /usr/bin/docker
```

`eichhorn doctor` prints the exact line with the right paths when sudo does not work yet.

> [!WARNING]
> Hooks run as shell commands (`sudo -n sh -c …`), and anyone allowed to run `docker` is
> effectively root. Keep the SSH key that has these rights safe.

## Engines

| | builtin | rsync |
|---|---|---|
| Needs on the server | find (or python3), tar | rsync ≥ 3.1 |
| Needs locally | nothing | rsync ≥ 3.1 (`brew install rsync` on macOS) |
| Changed files | copied whole | only changed parts sent |
| Repositories | all | hardlink storage on APFS, ext4, … |

`engine: auto` (the default) uses rsync when everything it needs is there, otherwise
builtin. `eichhorn doctor` shows which one a target will use.

---

Next: [Dumps and hooks](dumps-and-hooks.md) · [Back to the docs index](README.md)
