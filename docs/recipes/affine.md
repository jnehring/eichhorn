# Recipe: AFFiNE

This page shows how to back up a self-hosted [AFFiNE](https://affine.pro) server that runs
with docker compose.

## What is backed up

| Part | From |
|---|---|
| `files/storage/` | `upload_location` – uploaded files and blobs |
| `files/config/` | `config_location` |
| `files/compose/` | `compose_dir` – compose file and `.env` |
| `dumps/affine.sql.gz` | `pg_dump` of the AFFiNE database in `db_container` |

The documents themselves live in the database; the dump is what you need to get them back.
For an extra copy you can read without AFFiNE, export important workspaces as Markdown from
time to time.

## Example

AFFiNE's `.env` uses paths like `~/.affine/self-host/storage`. Write them as absolute paths:

```yaml
# targets/affine.yaml
host: web1
sudo: true
recipe: affine
with:
  compose_dir: /srv/affine
  upload_location: /home/jan/.affine/self-host/storage
  config_location: /home/jan/.affine/self-host/config
```

## Restore

1. Restore the compose folder, the storage and the config folder.
2. Start only Postgres and load the dump:

   ```bash
   gunzip -c affine.sql.gz | docker exec -i affine_postgres psql -U affine -d affine
   ```

3. Start AFFiNE: `docker compose up -d`.

---

[All recipes](README.md) · [Back to the docs index](../README.md)
