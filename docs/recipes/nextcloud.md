# Recipe: Nextcloud

This page shows how to back up a [Nextcloud](https://nextcloud.com) server that runs with
docker compose (official `nextcloud` image), and how to restore it.

## What is backed up

| Part | From | Notes |
|---|---|---|
| `files/data/` | `data_dir` | Every user's files as plain files: `files/data/<user>/files/…`. Preview images, caches, chunked uploads and updater leftovers are excluded. |
| `files/config/` | `config_dir` | Only if set and not inside `compose_dir`. Contains `config.php` with secrets – keep the backup drive encrypted. |
| `files/compose/` | `compose_dir` | Compose file, `.env`, and whatever else lies there; `data_dir` is excluded automatically if it lies inside. |
| `dumps/nextcloud.sql.gz` | the database container | Postgres or MySQL/MariaDB (`db_type`). |

During the run Nextcloud is in **maintenance mode** (`occ maintenance:mode --on`), so that
files and database match. Maintenance mode is turned off again afterwards, also when the
backup fails.

## Example

```yaml
# targets/nextcloud.yaml
host: web1
sudo: true
recipe: nextcloud
with:
  compose_dir: /srv/nextcloud
  data_dir: /srv/nextcloud/html/data
  app_container: nextcloud-app-1
  db_container: nextcloud-db-1
  db_type: postgres
```

With MariaDB, the dump usually needs a password. Name the variable that holds it inside the
database container – the password itself never goes into Eichhorn's config:

```yaml
  db_type: mysql
  db_password_env: MYSQL_PASSWORD
```

Find the container names with `docker ps` on the server.

## Restore

1. Restore `files/compose/`, then the data folder from `files/data/`.
2. Start only the database and load the dump:

   ```bash
   gunzip -c nextcloud.sql.gz | docker exec -i nextcloud-db-1 psql -U nextcloud -d nextcloud
   ```

   (MariaDB: `docker exec -i nextcloud-db-1 sh -c 'mysql -u nextcloud -p"$MYSQL_PASSWORD"'`.)

3. Start Nextcloud, then rescan the files and leave maintenance mode:

   ```bash
   docker exec -u www-data nextcloud-app-1 php occ files:scan --all
   docker exec -u www-data nextcloud-app-1 php occ maintenance:mode --off
   ```

> [!NOTE]
> Single files are easiest to restore without any of this: copy them from
> `files/data/<user>/files/` on the backup drive and upload them again.

---

[All recipes](README.md) · [Back to the docs index](../README.md)
