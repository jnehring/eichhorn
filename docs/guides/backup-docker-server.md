# Guide: backing up a Docker server

This guide backs up a typical self-hosted server – Immich, Nextcloud, AFFiNE, a self-written
app with Postgres, static websites and Caddy, all in docker compose – onto a USB drive at
your computer. It takes about half an hour the first time.

## 1. SSH and sudo

Make sure you can log in with a key, using an alias:

```bash
ssh web1 true
```

Allow the backup tools with sudo, as shown in [Servers over SSH](../hosts-and-ssh.md#sudo).

## 2. The drive

```bash
eichhorn repo init /Volumes/Backup-A/eichhorn --name Backup-A
```

## 3. One target per app

Find the folders and container names on the server:

```bash
ssh web1 'docker ps --format "{{.Names}}\t{{.Image}}"; docker compose ls'
```

Then create the targets from recipes, and fill in the parameters:

```bash
eichhorn target new immich --recipe immich
eichhorn target new nextcloud --recipe nextcloud
eichhorn target new affine --recipe affine
eichhorn target new didgelab --recipe compose-postgres
eichhorn target new websites --recipe static-site
eichhorn target new caddy --recipe caddy
```

Ready-made examples for exactly this setup are in
[`examples/targets/`](../../examples/targets/). Each recipe has a page with details and
restore steps: [Recipes](../recipes/README.md).

See what a recipe produces:

```bash
eichhorn target show nextcloud --expanded
```

## 4. Check everything

```bash
eichhorn config check
eichhorn doctor
```

Fix every ✗ before the first run. `doctor` checks the SSH login, tools, sudo, every folder,
every container and every database connection – without changing anything.

## 5. Back up

```bash
eichhorn run --all
```

The first run copies everything and can take hours for a photo library; Eichhorn keeps
the computer awake. Later runs copy only what changed. If a run is interrupted, the next
`eichhorn run` continues where it stopped.

Before an upgrade of an app, take a labelled, pinned snapshot:

```bash
eichhorn run nextcloud --label "before Nextcloud 31" --pin
```

## 6. Look at the result

```bash
eichhorn status
eichhorn snapshots nextcloud
```

The photos are in `…/targets/immich/snapshots/<date>/files/library/`, the Nextcloud files in
`…/targets/nextcloud/snapshots/<date>/files/data/<user>/files/`.

---

[Back to the docs index](../README.md)
