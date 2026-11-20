# History: snapshots, pruning, pins

This page explains how to look at past snapshots, keep the right ones and delete the rest.

## Looking back

```bash
eichhorn snapshots nextcloud              # all snapshots with their changes, labels and pins
eichhorn diff nextcloud                   # what changed between the last two
eichhorn diff nextcloud 2026-10-15 latest # between any two (a unique prefix of a name is enough)
eichhorn find nextcloud 'Report*.docx'    # every matching file and its versions
eichhorn stats nextcloud latest           # size, time per phase, largest files, …
```

## Retention

Without retention rules, Eichhorn keeps every snapshot. Add rules to the target or to
`defaults` in `eichhorn.yaml`:

```yaml
retention:
  keep_last: 6       # the 6 newest
  keep_monthly: 12   # plus the newest of each of the last 12 months
  keep_yearly: 5     # plus the newest of each of the last 5 years
```

| Rule | Keeps |
|---|---|
| `keep_last` | the N newest snapshots |
| `keep_daily` / `keep_weekly` / `keep_monthly` / `keep_yearly` | the newest snapshot of each of the last N days / ISO weeks / months / years that have one |

A snapshot is kept if any rule keeps it. The newest snapshot with status `ok` and all
**pinned** snapshots are always kept.

After every successful `eichhorn run`, the rules are applied (unless `--no-prune`). To see
what would happen, or to prune by hand:

```bash
eichhorn prune --dry-run
eichhorn prune
```

On drives with **sparse storage** (exFAT, FAT32), pruning first moves files that newer
snapshots still need into the next kept snapshot. An interrupted prune is finished
automatically by the next `prune` or `run`.

## Pins and labels

Before an upgrade, take a snapshot that is never pruned:

```bash
eichhorn run nextcloud --label "before Nextcloud 31" --pin
```

Change it later:

```bash
eichhorn pin nextcloud 2026-11-20 --note "keep until 31 runs stable"
eichhorn unpin nextcloud 2026-11-20
eichhorn label nextcloud latest "after the migration"
eichhorn label nextcloud latest ""        # remove the label
```

Pins and labels are stored in `targets/<target>/annotations.yaml` on the drive, so they move
with the drive and are copied by `repo sync`.

---

Next: [Restore and verify](restore-and-verify.md) · [Back to the docs index](README.md)
