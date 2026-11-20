# Getting started

This page takes you from an empty drive to your first backup and back to a restored file.
It takes about five minutes.

## 1. Prepare the drive

Plug in the drive you want to back up to. A drive formatted as **APFS** (macOS),
**NTFS** (Windows) or **ext4** (Linux) works best. exFAT and FAT32 work too, see
[Concepts](concepts.md#storage-modes).

Make a folder on it into a *repository*:

```bash
eichhorn repo init /Volumes/Backup-A/eichhorn --name Backup-A
```

On Windows the path looks like `E:\eichhorn`, on Linux like `/media/you/Backup-A/eichhorn`.

> [!TIP]
> If you name the folder `eichhorn` at the top of the drive, Eichhorn finds the drive
> again under any drive letter or mount path.

## 2. Describe what to back up

A *target* is one thing to back up. Create one:

```bash
eichhorn target new documents
```

This prints where the file was created. Open it and set the path:

```yaml
description: My documents
host: local
sources:
  - name: documents
    path: ~/Documents
    exclude:
      - .DS_Store
      - Thumbs.db
```

Check the configuration:

```bash
eichhorn config check
```

## 3. Back up

See first what would be copied, then run it:

```bash
eichhorn run documents --dry-run
eichhorn run documents
```

While it runs, two bars show the progress of the current target and of all targets
together, with speed and remaining time:

```text
documents      ██████████░░░░░░░░  58.2 %  copying documents 1.2 GB/2.1 GB at 24.0 MB/s  ~38 s
all 1/3        ████░░░░░░░░░░░░░░  21.0 %  ~4 min left
```

The overall estimate uses how long each target took last time, so it gets better after the
first run. When the output goes to a file instead of a terminal, Eichhorn writes a progress
line every 30 seconds instead.

At the end you get a summary:

```text
documents → Backup-A  2026-11-20T14-30-05  ok  (2 min 14 s)
  documents  +1204 new   2.1 GB transferred
  repository +2.1 GB used, 812.4 GB free
```

Run it again later. Only new and changed files are copied; everything else is
hardlinked to the previous snapshot and takes no extra space.

## 4. Look at your backups

```bash
eichhorn status                 # every target, its newest snapshot and its age
eichhorn snapshots documents    # all snapshots with their changes
```

## 5. Get a file back

Open the drive in Finder or Explorer:

```text
Backup-A/eichhorn/targets/documents/snapshots/2026-11-20T14-30-05/files/documents/
```

and copy the file you need. That's all – the backup is plain files.

Or let Eichhorn do it, with permissions, times and original names:

```bash
eichhorn find documents 'Letter*'
eichhorn restore documents latest documents/Letter.docx --to ~/Desktop
```

Prefer clicking? `eichhorn serve` opens the [web interface](web-ui.md).

---

Next: [Concepts](concepts.md) · [Back to the docs index](README.md)
