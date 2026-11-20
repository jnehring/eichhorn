# Storage format

This page documents what Eichhorn writes on a backup drive. It is a stable reference:
a change to this format increases `format` in `repository.yaml` and is announced in the
changelog. The complete design is in [section 3 of the specification](design/spec.md#3-repository).

## Layout

```text
<repository>/
  repository.yaml                 id, name, created, format, filesystem, storage
  README.txt                      a short explanation for anyone who finds the drive
  targets/
    <target>/
      snapshots/
        2026-11-20T14-30-05/
          files/<source>/…        the backed-up files
          dumps/                  database dumps (milestone 2)
          checksums.sha256
          metadata.jsonl.gz
          snapshot.json
      latest                      text file: name of the newest snapshot
      .incomplete-<date>/         a run in progress or interrupted
      .abandoned-<date>/          an unfinished run older than resume_max_age
  logs/
    <snapshot>-<target>.log
  .lock                           present while a run uses the repository
```

## `repository.yaml`

```yaml
id: 8f3c2a1e-5b7d-4e0a-9c61-2f4d8e9b7a10
name: Backup-A
created: '2026-11-20T14:00:00+01:00'
format: 1
filesystem: apfs
case_sensitive: false
posix_metadata: true
storage: hardlinks        # or sparse
max_file_size: null       # 4294967295 on FAT32
```

## `checksums.sha256`

The standard `sha256sum` format, one line per file physically stored in the snapshot,
paths relative to the snapshot folder with `/`:

```text
3a7bd3e2…4f1b  files/documents/Report.docx
```

## `metadata.jsonl.gz` – the file index

One JSON object per line, gzip-compressed, listing **every** file, folder and symlink of
the snapshot as it was on the source:

```json
{"p":"files/documents/a.txt","type":"file","size":8,"mtime_ns":1732101234000000000,"sha256":"7f8b…","uid":501,"gid":20,"mode":420,"here":true}
```

| Field | Meaning |
|---|---|
| `p` | original path inside the snapshot |
| `type` | `file`, `dir` or `symlink` |
| `size`, `mtime_ns`, `sha256` | size, modification time on the source (ns), content hash |
| `uid`, `gid`, `mode` | owner, group and permissions on the source |
| `here` | `true` if this snapshot folder physically holds the file (always true with hardlink storage) |
| `stored` | name on the drive, if it differs from `p` (escaped or renamed) |
| `parts` | number of pieces of a split file (FAT32) |
| `target` | symlink target (symlinks are recorded here, not created) |

## `snapshot.json` – the manifest

Written last. Contains target, status (`ok` or `partial`), engine, storage, start and
end time, label, per-source statistics (files, bytes, transferred, stored, new / changed
/ deleted, top folders), timings per phase, free space before and after, and warnings.

---

[Back to the docs index](README.md)
