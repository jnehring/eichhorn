# Web interface

This page explains the web interface: what it shows, how to start it and how it is
protected.

```bash
pip install "eichhorn[web]"      # the web interface needs a few extra packages
eichhorn serve
```

Eichhorn prints a link with an access token and opens it in your browser. The token is
valid until you stop the server with Ctrl-C.

## What you can do

| Page | Content |
|---|---|
| **Dashboard** | the connected drives with free space; a card per target with the last backup per drive, its status and the last verify result; *Back up now* |
| **Target** | all snapshots with changes, size, time, labels and pins (editable in place); *Verify*, *Prune*, *Diff*; charts of size, stored data, changed files and duration per run |
| **Snapshot** | statistics: files, size, new data, what pruning would free, transfer speed, time per phase, sources, dumps, largest files, warnings; *Restore* |
| **Browse** | the files **as they were at that snapshot** – also on exFAT drives, where unchanged files are stored in older snapshots (marked “stored in …”); search, download files or folders as `.zip`, versions of a file, *Browse as of date* |
| **Repositories** | capacity, space per target, growth per day and an estimate of when the drive is full; *Sync* between two drives |
| **Config** | the target files (read-only), with recipes filled in; *Doctor* |

Long actions (backups, verify, restore, sync, doctor) run in the background. Their output
appears live in the browser; the actions are the same `eichhorn` commands you would run
yourself. A backup shows two progress bars: the current target (phase, bytes, speed,
remaining time) and all targets together.

## Security

The web interface is meant for the person at the computer running Eichhorn:

- it listens on `127.0.0.1` only;
- every request needs the access token (kept in a cookie after the first visit);
- forms carry a second token against cross-site requests, and only the expected host
  name is accepted (against DNS rebinding).

> [!WARNING]
> Do not put it on the internet. Whoever can open it can read every file in every backup
> and restore onto your servers. To use it on a remote backup server, see
> [Eichhorn on a backup server](guides/remote-backup-server.md).

## Statistics on the command line

The same numbers are available without the web interface:

```bash
eichhorn stats                      # each connected drive
eichhorn stats nextcloud            # every snapshot of a target
eichhorn stats nextcloud latest     # one snapshot in detail
```

---

[Back to the docs index](README.md)
