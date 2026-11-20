# Changelog

All notable changes to Eichhorn are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org). A change to the storage format on the drives
is always marked **Storage format**.

## [Unreleased]

### Added

- Milestone 2, servers and apps:
  - SSH hosts through the system's OpenSSH client, with connection reuse; listing with GNU
    find or python3, small files in one tar stream, large files resumable with `tail -c +N`.
  - The rsync engine (delta transfer), chosen automatically by `engine: auto`.
  - Database dumps (`postgres`, `mysql`, `command`), streamed and gzip-compressed locally;
    passwords only through `password_env`.
  - Hooks `pre`, `post` and `post_always` (also after failures and Ctrl-C).
  - `sudo: true` for listing, transfer, dumps and hooks.
  - Recipes `immich`, `nextcloud`, `affine`, `compose-postgres`, `caddy`, `static-site`;
    `recipe list`, `target new --recipe`, `target show --expanded`; own recipes in
    `<config>/recipes/`.
  - `eichhorn doctor`: checks SSH, tools, sudo, folders, containers and databases, and
    prints the sudoers line that is needed.
- MIT licence.
- Milestone 1, the core command line:
  - `repo init`, `repo list`, `repo scan`, `repo unlock`; drives are recognised by ID and
    discovered on all mounted volumes.
  - `target new`, `target list`, `target show`, `config check` with file and line in
    every error message.
  - `run` with the builtin engine for local folders: hardlinked snapshots, sparse storage
    for exFAT/FAT32, split files on FAT32, escaped names and case collisions, resume after
    interruption, `--dry-run`, `--label`, `--pin`, `--restart`.
  - Run summary, `status`, `snapshots`, `--json` output, keep-awake during runs.
  - **Storage format 1**: `repository.yaml`, snapshot folders with `checksums.sha256`,
    the file index `metadata.jsonl.gz` and the manifest `snapshot.json`.
- Documentation in `docs/`, CI on Windows, macOS and Linux, release workflow.
