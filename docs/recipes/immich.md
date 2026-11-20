# Recipe: Immich

This page shows how to back up an [Immich](https://immich.app) server that runs with
docker compose, and how to restore it.

## What is backed up

| Part | From | Notes |
|---|---|---|
| `files/library/` | `upload_location` (Immich's `UPLOAD_LOCATION`) | Originals (`library/`, `upload/`), profile pictures. Thumbnails and transcoded videos are left out because Immich can regenerate them – set `include_thumbs: true` to keep them. Immich's own database backups (`backups/`) are left out too. |
| `files/compose/` | `compose_dir` | `docker-compose.yml`, `.env`; the upload folder is excluded automatically if it lies inside. |
| `dumps/immich.sql.gz` | `pg_dumpall` in `db_container` | As recommended by the Immich documentation. |

> [!TIP]
> Turn on Immich's **storage template** (Administration → Settings → Storage Template), so
> that originals are stored as `library/<user>/<year>/<month>/<file>`. Then the backup is
> easy to browse in Finder or Explorer.

## Example

```yaml
# targets/immich.yaml
host: web1
sudo: true
recipe: immich
with:
  compose_dir: /srv/immich
  upload_location: /srv/immich/library
```

Check it, then run it:

```bash
eichhorn doctor immich
eichhorn run immich
```

For a fully consistent copy, stop the server during the backup with `stop_server: true`.
Immich is then unavailable while the files are copied.

## Restore

1. Copy `files/compose/` back to the compose folder and `files/library/` back to
   `UPLOAD_LOCATION`.
2. Start only the database: `docker compose up -d database`.
3. Load the dump (the container must be empty):

   ```bash
   gunzip -c immich.sql.gz | docker exec -i immich_postgres psql -U postgres
   ```

4. Start everything: `docker compose up -d`. Immich regenerates missing thumbnails.

---

[All recipes](README.md) · [Back to the docs index](../README.md)
