# Installation

This page shows how to install Eichhorn on Windows, macOS and Linux.

Eichhorn needs **Python 3.11 or newer**. It has two small dependencies (PyYAML and
platformdirs) and otherwise uses only the Python standard library.

## Install

Pick one of these:

```bash
pipx install eichhorn        # recommended: an isolated install with the eichhorn command
```

```bash
uv tool install eichhorn     # the same with uv
```

```bash
pip install eichhorn         # into the current Python environment
```

Check that it works:

```bash
eichhorn --version
```

> [!TIP]
> If the `eichhorn` command clashes with something else on your system, `python -m eichhorn`
> runs the same program.

## Per operating system

### macOS

Install Python with [Homebrew](https://brew.sh) (`brew install python pipx`) or from
python.org. Nothing else is needed for backing up local folders.

### Windows

Install Python from python.org or with `winget install Python.Python.3.12`, then
`pip install pipx` and `pipx install eichhorn`.

> [!NOTE]
> Turn on long path support so that Explorer can open deep folders in a backup:
> *Local Group Policy → Computer Configuration → Administrative Templates → System →
> Filesystem → Enable Win32 long paths*.

### Linux

Use your distribution's Python and pipx, for example `sudo apt install pipx` on
Debian and Ubuntu.

## Upgrading

```bash
pipx upgrade eichhorn
```

The storage format on your drives is versioned. A newer Eichhorn reads older drives;
the [changelog](https://github.com/jnehring/eichhorn/blob/main/CHANGELOG.md) says when a
version changes the format.

---

Next: [Getting started](getting-started.md) · [Back to the docs index](README.md)
