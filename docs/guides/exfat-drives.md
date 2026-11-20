# Guide: exFAT and FAT32 drives

This page explains what is different on drives formatted as exFAT or FAT32 – common on
drives that are shared between Windows and macOS.

exFAT and FAT32 have no hardlinks. Eichhorn therefore uses **sparse storage**: each snapshot
folder holds only the files that were new or changed in that run. The folder layout is the
same as on other drives.

| | APFS, NTFS, ext4 | exFAT, FAT32 |
|---|---|---|
| A snapshot folder in Finder / Explorer | complete | only new and changed files; the first one is complete |
| Space per version of a file | once | once |
| Web interface, `restore`, `verify` | complete state | complete state |
| Engine | rsync or builtin | builtin |

## What to keep in mind

- **Browse with Eichhorn.** `eichhorn serve` or `eichhorn restore` show and restore the
  complete state of any snapshot. In Finder, look for a file in the snapshot folders from
  newest to oldest.
- **Don't delete snapshot folders by hand.** Newer snapshots may need files from older
  ones. `eichhorn prune` moves those files forward before it deletes a snapshot.
- **FAT32: 4 GiB per file.** Bigger files are stored in parts (`*.eichhorn-part-001`, …)
  and joined again by Eichhorn; see [recovery without Eichhorn](../recovery-without-eichhorn.md).
- **No journaling.** Eject the drive before unplugging it. `eichhorn verify` finds damage.
- **No owners and permissions** on the drive; they are kept in the file index and restored.

If the drive is only used for backups, reformatting it as APFS (macOS), NTFS (Windows) or
ext4 (Linux) gives complete snapshot folders. macOS can read NTFS drives without extra
software.

---

[Back to the docs index](../README.md)
