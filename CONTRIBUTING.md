# Contributing to Eichhorn

Thank you for helping! Bug reports, documentation fixes and pull requests are welcome.

## Before you start

- For a larger change, please open an issue first so we can agree on the approach.
- The design is in [docs/design/spec.md](docs/design/spec.md). If your change differs
  from it, update the spec in the same pull request.

## Setup and checks

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

See [docs/development.md](docs/development.md) for details.

## Pull requests

- Add or update tests.
- Update the documentation in `docs/`; regenerate `docs/cli.md` and
  `docs/configuration-reference.md` if commands or config keys changed.
- Add an entry under *Unreleased* in [CHANGELOG.md](CHANGELOG.md).
- Never change the storage format without bumping `format` and describing a migration.

## Reporting bugs

Please include your OS, `eichhorn --version`, the command you ran and its full output.
Do **not** post file lists or logs that contain private data.
