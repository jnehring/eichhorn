# Recipe: static-site

This page shows how to back up a static website: a folder of HTML, CSS and images.

## Example

One target per site:

```yaml
# targets/example-org.yaml
host: web1
recipe: static-site
with:
  path: /srv/sites/example.org
```

Several sites in one target – add them as your own sources:

```yaml
# targets/websites.yaml
host: web1
recipe: static-site
with:
  path: /srv/sites/first.example
  name: first
sources:
  - name: second
    path: /srv/sites/second.example
```

> [!TIP]
> If a site is built from a Git repository, the repository is the real source. The backup
> then mainly protects uploads and anything that is not in Git.

## Restore

Copy `files/<name>/` back to the web root.

---

[All recipes](README.md) · [Back to the docs index](../README.md)
