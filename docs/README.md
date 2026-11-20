# Eichhorn documentation

Eichhorn backs up servers and folders onto drives you plug in, as dated snapshots of
plain files.

## Start here

1. [Installation](installation.md): install Eichhorn on Windows, macOS or Linux.
2. [Getting started](getting-started.md): your first repository, target and backup.
3. [Concepts](concepts.md): repositories, targets, snapshots and how storage works.

## Servers and apps

- [Servers over SSH](hosts-and-ssh.md): requirements, SSH setup, sudo, engines.
- [Dumps and hooks](dumps-and-hooks.md): databases and maintenance mode.
- [Recipes](recipes/README.md): Immich, Nextcloud, AFFiNE, Postgres apps, Caddy, static sites.
- [Guide: backing up a Docker server](guides/backup-docker-server.md): step by step.

## Reference

- [Configuration](configuration.md): how to write `eichhorn.yaml` and target files.
- [Configuration reference](configuration-reference.md): every key, generated from the schema.
- [Command reference](cli.md): every command and option, generated from the program.
- [Storage format](storage-format.md): what Eichhorn writes on the drive.

## When things go wrong

- [Getting data back without Eichhorn](recovery-without-eichhorn.md)
- [Troubleshooting](troubleshooting.md)

## For contributors

- [Architecture](architecture.md): the layers and the main modules.
- [Development](development.md): working with uv, tests, documentation and releases.
- [Specification](design/spec.md): the full design, including features that are not built yet.

## Coming with later milestones

Guides for rotating two drives, restoring an app, exFAT drives, Windows and a remote
backup server, and the web interface.
