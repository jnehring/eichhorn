# Recipe: eichhorn-config

This recipe backs up Eichhorn's own configuration folder: `eichhorn.yaml`,
`repositories.yaml`, `targets/` and your own recipes. The local cache in `state/` is left
out. It runs on the computer Eichhorn runs on (`host: local`), whatever the operating system.

## Example

```yaml
# in eichhorn.yaml
targets:
  eichhorn:
    recipe: eichhorn-config
```

or as `targets/eichhorn.yaml` with just `recipe: eichhorn-config`.

## Restore

On a new computer: install Eichhorn, plug in the drive, and copy
`targets/eichhorn/snapshots/<newest>/files/config/` into the config folder
(see [Configuration](../configuration.md#where-the-files-are)). `eichhorn repo list` then
finds the drive again.

---

[All recipes](README.md) · [Back to the docs index](../README.md)
