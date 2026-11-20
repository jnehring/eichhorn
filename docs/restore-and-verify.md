# Restore and verify

This page shows how to get data back and how to check that the backups are intact.

## Restore files

```bash
eichhorn restore docs latest --to ~/Restore                        # everything
eichhorn restore docs latest docs/Taxes/2025 --to ~/Restore        # one folder
eichhorn restore nextcloud 2026-10-15 data/jan/files/Report.docx --to ~/Desktop
```

- `SNAPSHOT` is a snapshot name, a unique prefix of it (`2026-10-15`), or `latest`.
- The path is the one shown by `eichhorn find` or the web interface; the leading `files/`
  can be left out.
- Original file names, permissions, modification times and symlinks are restored. Owners
  only when you run Eichhorn as root.
- Eichhorn never overwrites existing files unless you add `--force`.
- On drives with sparse storage, files that did not change are fetched from the older
  snapshots automatically.

### Back onto a server

```bash
eichhorn restore nextcloud latest data --to web1:/srv/nextcloud/restore --sudo
```

The files are streamed to the server as a tar archive and unpacked there with their owners
and permissions. The destination folder must be empty unless you add `--force`.

## Restore a database dump

`eichhorn dump` writes a dump as plain SQL – also for [delta dumps](dumps-and-hooks.md#delta-dumps):

```bash
eichhorn dump nextcloud latest nextcloud --to nextcloud.sql
eichhorn dump nextcloud latest nextcloud | ssh web1 docker exec -i nextcloud-db psql -U nextcloud -d nextcloud
```

The [recipe pages](recipes/README.md) describe a full restore for each app.

## Verify

Every snapshot has a `checksums.sha256`. `eichhorn verify` reads the files again and
compares:

```bash
eichhorn verify nextcloud                    # the newest snapshot
eichhorn verify nextcloud --all-snapshots    # all of them; shared files are read once
eichhorn verify --all --sample 5             # a quick check of 5 % of the files of all targets
eichhorn verify nextcloud --against-source   # also compare with the server
```

It reports files that are **corrupted** (content changed on the drive), **missing** or
**unexpected**, and rebuilds every delta dump to check it. For a corrupted file, the report
names other snapshots that still hold a good copy, so that you can restore it from there.

`--against-source` compares the newest snapshot with the host. Files with the same size and
time but different content point to a problem on one side.

Results are kept in `logs/verify/` on the drive; the web interface shows the last result
per snapshot.

---

Next: [Several drives](guides/rotating-drives.md) · [Back to the docs index](README.md)
