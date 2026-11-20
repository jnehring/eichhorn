# Guide: Eichhorn on a backup server

This page shows how to run Eichhorn on a Linux machine with backup disks – a home server or
a NAS – instead of on a laptop.

Everything works the same: install Eichhorn there, register its disks with
`eichhorn repo init`, and add targets. The backup server pulls from your other servers over
SSH. Schedule runs with a systemd timer or cron:

```text
# crontab -e: every night at 2:30
30 2 * * * /home/backup/.local/bin/eichhorn -q run --all --all-repos
```

## The web interface over SSH

`eichhorn serve` listens on `127.0.0.1` of the backup server only. Reach it from your laptop
through an SSH tunnel:

```bash
ssh -L 8765:127.0.0.1:8765 backup-server
eichhorn serve --no-browser          # on the backup server, in that SSH session
```

Then open the printed link on your laptop. Eichhorn notices the SSH session and prints the
tunnel command for you.

> [!WARNING]
> Do not use `--listen 0.0.0.0` to reach it directly. The web interface has no user
> accounts and no TLS; see [Web interface](../web-ui.md#security).

---

[Back to the docs index](../README.md)
