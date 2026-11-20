# Getting data back without Eichhorn

This page explains how to get your data back if Eichhorn is not available – on another
computer, years from now, or in a hurry.

Everything Eichhorn writes is plain files in documented formats. You need nothing but a
file manager, and for checks `sha256sum` or `shasum`.

## Find the newest backup

```text
<drive>/eichhorn/targets/<target>/latest                 ← a text file with the newest snapshot's name
<drive>/eichhorn/targets/<target>/snapshots/<that name>/files/
```

Copy the files you need from `files/`.

## Check a snapshot

```bash
cd <drive>/eichhorn/targets/<target>/snapshots/<snapshot>
sha256sum -c checksums.sha256          # Linux
shasum -a 256 -c checksums.sha256      # macOS
```

## Drives with sparse storage (exFAT, FAT32)

Each snapshot folder holds only the files that were new or changed in that run. To find a
file, look in the newest snapshot first, then in older ones; the first snapshot is
complete. The file index `metadata.jsonl.gz` of a snapshot lists every file it contained:

```bash
gzip -dc metadata.jsonl.gz | grep '"p":"files/documents/Report.docx"'
```

`"here":false` means the content is stored in an older snapshot.

## Large files on FAT32

Files over 4 GiB are stored in parts. Join them:

```bash
cat video.mp4.eichhorn-part-* > video.mp4                         # macOS, Linux
```

```bat
copy /b video.mp4.eichhorn-part-001 + video.mp4.eichhorn-part-002 video.mp4
```

## Database dumps

`*.sql.gz` is a gzip-compressed SQL dump (`gunzip -c`). With delta dumps, a snapshot has
`<name>.base.sql.zst` (a full dump) and maybe `<name>.patch.zst`:

```bash
zstd -d db.base.sql.zst -o base.sql                                  # the full dump
zstd -d --long=31 --patch-from=base.sql db.patch.zst -o db.sql       # with the patch
```

## Renamed files

If a file name could not be stored on the drive (for example `a:b.txt` on NTFS), it is
stored escaped as `a%3Ab.txt`. The original name is in the file index (`"p"`), the
stored one in `"stored"`.

---

[Storage format](storage-format.md) · [Back to the docs index](README.md)
