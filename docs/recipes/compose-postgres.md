# Recipe: compose-postgres

This page is for a self-written app in docker compose with **one Postgres database** and,
optionally, folders with uploads or other data.

## What is backed up

| Part | From |
|---|---|
| `files/compose/` | `compose_dir` |
| `files/data-1/`, `files/data-2/`, … | each folder in `data_dirs` |
| `dumps/<db_name>.sql.gz` | `pg_dump` of `db_name` in `db_container` |

Folders in `data_dirs` that lie inside `compose_dir` are excluded there automatically, so
nothing is copied twice. The raw Postgres data folder is never copied – the dump is the
database's backup.

## Example

```yaml
# targets/didgelab.yaml
host: web1
sudo: true
recipe: compose-postgres
with:
  compose_dir: /srv/didgelab
  db_container: didgelab-db-1
  db_name: didgelab
  db_user: didgelab
  data_dirs:
    - /srv/didgelab/uploads
```

If the database needs a password for local connections, name the variable that holds it in
the container: `db_password_env: POSTGRES_PASSWORD`.

## Restore

```bash
gunzip -c didgelab.sql.gz | docker exec -i didgelab-db-1 psql -U didgelab -d didgelab
```

---

[All recipes](README.md) · [Back to the docs index](../README.md)
