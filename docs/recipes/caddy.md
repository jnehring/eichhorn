# Recipe: Caddy

This page shows how to back up a [Caddy](https://caddyserver.com) reverse proxy that runs
with docker compose.

## What is backed up

| Part | From |
|---|---|
| `files/compose/` | `compose_dir` – `Caddyfile`, compose file |
| `files/data/` | `data_dir`, if set – certificates and keys |

Certificates are optional: Caddy fetches new ones automatically. Backing them up avoids
hitting rate limits of the certificate authority after a restore.

## Example

```yaml
# targets/caddy.yaml
host: web1
sudo: true
recipe: caddy
with:
  compose_dir: /srv/caddy
  data_dir: /srv/caddy/data
```

## Restore

Copy the folders back and run `docker compose up -d`.

---

[All recipes](README.md) · [Back to the docs index](../README.md)
