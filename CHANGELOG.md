# Changelog

All notable changes to Eichhorn are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org). A change to the storage format on the drives
is always marked **Storage format**.

## [Unreleased]

### Added

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
