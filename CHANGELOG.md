# Changelog

All notable changes to Eichhorn are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org). A change to the storage format on the drives
is always marked **Storage format**.

## [Unreleased]

### Added

- MySQL/MariaDB dumps use `mariadb-dump` where it exists (MariaDB 11 and newer no longer
  ship `mysqldump`) and fall back to `mysqldump`.
- Recipes: a source of the target file that replaces a recipe's source keeps the automatic
  exclusion of nested sources.
- Windows: `verify` no longer reports every file as unexpected; output is always UTF-8.
- Milestone 6, web interface (`pip install "eichhorn[web]"`, `eichhorn serve`):
  dashboard, target and snapshot pages with statistics and SVG charts, point-in-time file
  browser (also for sparse storage) with search, downloads, folder zips and file versions,
  pins and labels in place, background jobs with a live log (backup, verify, prune, restore,
  sync, doctor). Token login, CSRF tokens, host check. `eichhorn stats`.
- Milestone 5, delta dumps: `delta:` per dump stores zstd patches against a full dump
  (`<name>.base.sql.zst` + `<name>.patch.zst`, compatible with `zstd --patch-from`);
  `eichhorn dump` writes a dump as plain SQL.
- Milestone 4, several repositories: `run --repo A --repo B` and `--all-repos` contact the
  host once and copy the new snapshot locally; `repo sync` (with `--both`,
  `--include-older`); pins and labels are copied.
- Milestone 3, history and integrity: retention rules and `prune` (automatic after a run),
  with forward moves and a journal on sparse storage; `pin`, `unpin`, `label`; `diff`,
  `find`; `restore` to a folder or onto a host (`--to host:/path`); `verify` with
  `--all-snapshots`, `--sample`, `--against-source`.
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
