# Troubleshooting

This page lists common messages and what to do about them.

## "no repository is connected; plug in a backup drive"

Eichhorn did not find any registered drive. Check:

- the drive is mounted;
- `eichhorn repo list` shows the repository;
- the repository folder is called `eichhorn` at the top of the drive, or its path is
  listed under `paths` in `repositories.yaml`.

## "several repositories are connected … choose one with --repo"

Two backup drives are plugged in. Name the one to use: `eichhorn run docs --repo Backup-A`.
Writing to both in one run comes with milestone 4.

## "repository … is locked"

Another Eichhorn command is using the drive. If no command is running anymore (for
example after a crash), remove the lock:

```bash
eichhorn repo unlock Backup-A
```

## "unfinished run" in `eichhorn status`

A run was interrupted. The next `eichhorn run` continues it. To start over instead:
`eichhorn run docs --restart`.

## "less than 10 % free"

The drive is getting full. Pruning old snapshots comes with milestone 3; until then,
delete the oldest snapshot folders by hand (never the newest).

## "cannot connect to web1"

Eichhorn runs `ssh` without asking questions (`BatchMode`). Check that `ssh web1 true` works
without a password prompt. If your key has a passphrase, load it into the agent first:
`ssh-add ~/.ssh/id_ed25519`.

## "sudo -n failed" in `eichhorn doctor`

The server asks for a password for sudo. Add the line that `doctor` prints to
`/etc/sudoers.d/eichhorn`, see [Servers over SSH](hosts-and-ssh.md#sudo).

## "pre hook … failed"

The target was not backed up, but the `post_always` hooks ran (for example to leave
maintenance mode). The hook's output is in the error message.

## "dump … failed" and status `partial`

The files were backed up, the database was not. Run `eichhorn doctor TARGET` to check the
container and the database connection; the error message contains the database's output.

## A file name looks strange on the drive (`a%3Ab.txt`, `photo (eichhorn-2).jpg`)

The drive's file system cannot store the original name, or two names differ only in
upper and lower case. The original name is kept in the file index; see
[Storage format](storage-format.md).

---

[Back to the docs index](README.md)
