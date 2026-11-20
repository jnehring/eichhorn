# Eichhorn

> *Eichhorn* (German, short for *Eichhörnchen*): squirrel. It hides its supplies in many
> places and finds them again when it needs them.

Eichhorn backs up servers and folders onto **drives you plug in**, as **dated
snapshots of plain files**. You can open a backup in Finder or Explorer and copy a
file back, without Eichhorn. Unchanged files are hardlinks, so every snapshot looks
complete but only new data takes space.

![Eichhorn architecture](https://raw.githubusercontent.com/OWNER/eichhorn/main/docs/architecture.png)

> [!WARNING]
> Eichhorn is in early development. Milestone 1 (local folders, snapshots, the
> command line) works. SSH servers, database dumps, pruning and the web interface
> are coming. See the [roadmap](#roadmap).

## Quick start

```bash
pip install eichhorn          # or: pipx install eichhorn / uv tool install eichhorn
```

```bash
# 1. Make a folder on your backup drive into a repository
eichhorn repo init /Volumes/Backup-A/eichhorn --name Backup-A

# 2. Describe what to back up
eichhorn target new documents      # creates targets/documents.yaml – edit the path
eichhorn config check

# 3. Back up, and look at the result
eichhorn run documents
eichhorn snapshots documents
eichhorn status
```

The backup is now on the drive:

```text
eichhorn/targets/documents/snapshots/2026-11-20T14-30-05/files/documents/…
```

## Features

- **Plain files.** Every snapshot is an ordinary folder tree, browsable without Eichhorn.
- **History without wasted space.** Unchanged files are hardlinked to the previous snapshot.
- **Any drive.** APFS, NTFS, ext4 – and exFAT/FAT32, where each snapshot keeps only what changed.
- **Windows, macOS and Linux**, with the same config files.
- **Integrity.** Each snapshot has a standard `checksums.sha256` (`sha256sum -c` works).
- **Safe.** Drives are recognised by ID, runs lock the drive, an interrupted run resumes,
  and an unfinished snapshot never looks complete.
- **Run summary** after each backup: new, changed and deleted files, time and space used.
- **Keeps your computer awake** during long runs.

## Documentation

The full documentation is in [`docs/`](https://github.com/OWNER/eichhorn/tree/main/docs):

- [Getting started](https://github.com/OWNER/eichhorn/blob/main/docs/getting-started.md)
- [Installation](https://github.com/OWNER/eichhorn/blob/main/docs/installation.md)
- [Concepts](https://github.com/OWNER/eichhorn/blob/main/docs/concepts.md)
- [Configuration](https://github.com/OWNER/eichhorn/blob/main/docs/configuration.md)
- [Command reference](https://github.com/OWNER/eichhorn/blob/main/docs/cli.md)
- [Getting data back without Eichhorn](https://github.com/OWNER/eichhorn/blob/main/docs/recovery-without-eichhorn.md)

## Roadmap

| Milestone | Content | State |
|---|---|---|
| 1 | Core CLI: repositories, local folders, snapshots, summary | ✅ done |
| 2 | SSH servers, rsync engine, database dumps, hooks, recipes (Immich, Nextcloud, …) | planned |
| 3 | Retention and pruning, pins, `diff`, `find`, `restore`, `verify` | planned |
| 4 | Several drives per run, `repo sync` | planned |
| 5 | Sparse-storage browsing and pruning, delta dumps | planned |
| 6 | Web interface with point-in-time browser and statistics | planned |
| 7 | Release 0.1.0 on PyPI | planned |

The design is described in the [specification](https://github.com/OWNER/eichhorn/blob/main/docs/design/spec.md).

## Contributing

See [CONTRIBUTING.md](https://github.com/OWNER/eichhorn/blob/main/CONTRIBUTING.md).
Security problems: please report them privately, see
[SECURITY.md](https://github.com/OWNER/eichhorn/blob/main/SECURITY.md).
