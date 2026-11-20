# Configuration

This page shows how to write Eichhorn's configuration. Every key is listed in the
[configuration reference](configuration-reference.md).

## Where the files are

| OS | Folder |
|---|---|
| Linux | `~/.config/eichhorn/` |
| macOS | `~/Library/Application Support/eichhorn/` |
| Windows | `%APPDATA%\eichhorn\` |

Use another folder with `--config DIR` or the environment variable `EICHHORN_CONFIG`.

```text
eichhorn/
  eichhorn.yaml          global settings (optional)
  repositories.yaml      written by `eichhorn repo init`
  targets/
    documents.yaml       one file per target; the file name is the target name
```

## `eichhorn.yaml`

All keys are optional:

```yaml
defaults:
  checksums: true      # write checksums.sha256 into every snapshot
  keep_awake: true     # keep the computer awake during runs
  engine: auto
```

## Targets

```yaml
description: Documents and pictures
host: local

sources:
  - name: documents            # becomes files/documents/ in the snapshot
    path: ~/Documents
    exclude: [.DS_Store, Thumbs.db, "~$*"]
  - name: pictures
    path: ~/Pictures

# Only these repositories (default: all)
repositories: [Backup-A]
```

More examples are in [`examples/`](../examples/).

## Exclude patterns

| Pattern | Matches |
|---|---|
| `*.tmp` | any file ending in `.tmp`, at any depth |
| `/cache/` | the folder `cache` at the top of the source only |
| `cache/` | any folder named `cache` (a trailing `/` means folders only) |
| `*/cache/` | a `cache` folder inside any folder |
| `**/node_modules/` | `node_modules` at any depth |
| `file?.txt`, `[abc].txt` | one character, one of a set |

rsync filter rules such as `+ pattern` or `merge` are not supported, so that every
engine behaves the same.

## YAML pitfalls

- `yes`, `no`, `on` and `off` are booleans in YAML. Put them in quotes when you mean text.
  `eichhorn config check` tells you when this happens.
- Windows paths: write `C:/Users/jan/Documents`, or use single quotes
  `'C:\Users\jan\Documents'`. In double quotes YAML treats `\` as an escape.

## Checking

```bash
eichhorn config check
```

It reports every problem with file and line, for example:

```text
targets/docs.yaml:4: sources.0.pth: unknown key (did you mean 'path'?)
```

---

Next: [Command reference](cli.md) · [Back to the docs index](README.md)
