# Development

This page explains how to work on Eichhorn: setup, tests, documentation and releases.

## Setup

Eichhorn uses [uv](https://docs.astral.sh/uv/) for everything.

```bash
git clone https://github.com/jnehring/eichhorn.git
cd eichhorn
uv sync                     # creates .venv with Eichhorn and the dev tools
uv run eichhorn --help
```

## Checks

```bash
uv run pytest               # tests
uv run ruff check .         # lint
uv run ruff format .        # format
uv run mypy                 # types (strict)
```

CI runs the tests on Ubuntu, macOS and Windows with Python 3.11, 3.12 and 3.13
([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)).

## Documentation

The documentation is Markdown in `docs/`, read directly on GitHub. Rules:

- relative links between pages; one topic per page; end with "Next" and "Back" links;
- callouts with `> [!NOTE]`, `> [!TIP]`, `> [!WARNING]`; small diagrams in Mermaid;
- a feature is done only when its docs page is written.

Two pages are generated. Regenerate them after changing commands or the config schema –
CI fails otherwise:

```bash
uv run python -m eichhorn._docs cli > docs/cli.md
uv run python -m eichhorn._docs config > docs/configuration-reference.md
```

Example configs in `examples/` are validated in CI:
`uv run python -m eichhorn._docs check-examples examples`.

## Releasing

Releases are built and published by
[`.github/workflows/release.yml`](../.github/workflows/release.yml).

### One-time setup

1. On [PyPI](https://pypi.org/manage/account/publishing/) add a **pending trusted
   publisher**: project `eichhorn`, owner and repository of the GitHub repo, workflow
   `release.yml`, environment `pypi`.
2. Do the same on [TestPyPI](https://test.pypi.org/manage/account/publishing/) with
   environment `testpypi`.
3. In the GitHub repository, *Settings → Environments*, create `pypi` (add yourself as a
   required reviewer) and `testpypi`.

No API tokens are stored in GitHub.

### Dry run on TestPyPI

*Actions → Release → Run workflow*. This runs the full CI, builds, and publishes to
TestPyPI. Try it:

```bash
pipx run --index-url https://test.pypi.org/simple/ --pip-args="--extra-index-url https://pypi.org/simple/" eichhorn --version
```

### A real release

1. Bump the version: `uv version --bump minor` (or `patch`, or set it:
   `uv version 0.1.0`).
2. In `CHANGELOG.md`, rename *Unreleased* to the new version with today's date.
3. Commit, merge to `main`, then tag and push:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

The workflow then:

1. runs the full CI;
2. checks that the tag matches the package version and that the changelog has an entry;
3. builds sdist and wheel with `uv build` and checks them;
4. waits for approval in the `pypi` environment, then publishes to PyPI with trusted
   publishing (with PEP 740 attestations);
5. creates a GitHub Release with the changelog section as notes and the built files
   attached. Versions like `0.2.0rc1` are marked as pre-releases.

---

[Back to the docs index](README.md)
