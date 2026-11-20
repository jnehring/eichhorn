# Dumps and hooks

This page explains database dumps and hooks: the two things that make a backup of a server
app consistent.

## Database dumps

Never back up a running database's data folder as files – the copy would be broken.
Eichhorn asks the database for a **dump** instead, streams it over SSH and compresses it on
your computer. Nothing is written on the server's disk.

```yaml
dumps:
  - name: nextcloud
    type: postgres            # postgres | mysql | command
    container: nextcloud-db   # omit if the database runs directly on the host
    database: nextcloud       # postgres: "*" dumps all databases (pg_dumpall)
    user: nextcloud
```

| Type | Command (inside the container if `container` is set) |
|---|---|
| `postgres` | `pg_dump -U <user> --clean --if-exists <database>`, or `pg_dumpall` for `"*"` |
| `mysql` | `mariadb-dump` (MariaDB) or `mysqldump` (MySQL), whichever exists, with `-u <user> --single-transaction --routines --triggers --databases <database>` (`--all-databases` without a database) |
| `command` | any command whose output is the dump: `run: "redis-cli --rdb -"`, with `ext:` for the file extension |

The result is `dumps/<name>.sql.gz` in the snapshot. A dump that fails or is empty marks
the snapshot as `partial` and the run exits with an error.

### Passwords

Postgres in a container normally needs no password for local connections. When a database
does, name the **environment variable** that holds the password – in the container, or on
the host:

```yaml
    password_env: MYSQL_PASSWORD
```

Eichhorn then sets `MYSQL_PWD="$MYSQL_PASSWORD"` before running the dump tool. The password never appears in
Eichhorn's config, logs or on the command line.

### Restoring a dump

```bash
eichhorn dump nextcloud latest nextcloud | ssh web1 docker exec -i nextcloud-db psql -U nextcloud -d nextcloud
```

Without Eichhorn: `gunzip -c nextcloud.sql.gz | …`.

### Delta dumps

Large databases change only a little between runs, but every snapshot would store the whole
dump again. With `delta`, most snapshots store only a **patch** against an earlier full
dump:

```yaml
dumps:
  - name: nextcloud
    type: postgres
    container: nextcloud-db
    database: nextcloud
    user: nextcloud
    delta:
      full_every: 10      # a new full dump at least every 10 runs
      max_ratio: 0.5      # …or when a patch would be over half the size of a full dump
      max_base_age: 90d   # …or when the full dump is older than this
```

| File in `dumps/` | Content |
|---|---|
| `nextcloud.base.sql.zst` | the full dump (zstd). In snapshots with a patch it is the full dump the patch refers to – a hardlink, so it takes no space. |
| `nextcloud.patch.zst` | the patch (`zstd --patch-from`), only in snapshots that store one |

A patch always refers directly to a full dump, never to another patch, and every snapshot
can be restored on its own. `eichhorn dump` and `eichhorn verify` rebuild the dump; by hand:

```bash
zstd -d nextcloud.base.sql.zst -o base.sql
zstd -d --long=31 --patch-from=base.sql nextcloud.patch.zst -o nextcloud.sql
```

Dumps larger than 2 GiB are always stored in full (a limit of zstd's patch mode).

## Hooks

Hooks are shell commands that run on the host:

```yaml
hooks:
  pre:            # before dumps and files; a failure stops the target
    - docker exec -u www-data nextcloud-app php occ maintenance:mode --on
  post:           # after a successful run
    - echo done
  post_always:    # always at the end – also after a failure or Ctrl-C
    - docker exec -u www-data nextcloud-app php occ maintenance:mode --off
```

Order of a run: **pre hooks → dumps → files → post hooks → post_always hooks**.

Hooks must be safe to run twice: an interrupted run is resumed later, and the hooks run
again then. On a local Windows host, hooks run in `cmd.exe`; set `shell: powershell` for
PowerShell.

---

Next: [Recipes](recipes/README.md) · [Back to the docs index](README.md)
