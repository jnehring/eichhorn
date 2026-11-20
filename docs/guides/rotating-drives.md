# Guide: two drives in rotation

This guide sets up two backup drives that you swap, for example one at home and one at work.

## Set up both drives

```bash
eichhorn repo init /Volumes/Backup-A/eichhorn --name Backup-A
eichhorn repo init /Volumes/Backup-B/eichhorn --name Backup-B
```

## Back up

With one drive plugged in, `eichhorn run --all` uses that one. With both plugged in, say
which:

```bash
eichhorn run --all --all-repos          # to both
eichhorn run --all --repo Backup-B      # to one
```

With `--all-repos` the servers are contacted only once: the new snapshot is written to the
first drive and then copied locally to the second. Unchanged files are hardlinked on both
drives (or left out on exFAT).

## Let the drives catch up

When you bring the second drive back, copy what it missed – without contacting the
servers:

```bash
eichhorn repo sync Backup-A Backup-B --dry-run
eichhorn repo sync Backup-A Backup-B
eichhorn repo sync Backup-A Backup-B --both     # in both directions
```

Snapshots keep their names; pins and labels are copied too. Snapshots older than the
destination's oldest are skipped unless you add `--include-older` (they were probably
pruned there on purpose).

Each drive keeps its own history and applies the retention rules on its own.

---

[Back to the docs index](../README.md)
