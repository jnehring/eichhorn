# Guide: Windows

This page lists what is specific to Eichhorn on Windows.

- **Install:** Python from python.org or `winget install Python.Python.3.12`, then
  `pip install pipx` and `pipx install eichhorn`.
- **SSH:** Windows 10 and 11 include the OpenSSH client. Put your servers in
  `%USERPROFILE%\.ssh\config` and load your key with `ssh-add` (start the
  *OpenSSH Authentication Agent* service first).
- **Drives:** repositories are found under any drive letter. NTFS gives complete snapshot
  folders with hardlinks; exFAT works with [sparse storage](exfat-drives.md).
- **Engine:** the builtin engine is used; rsync is not needed.
- **Paths in the config:** write `C:/Users/jan/Documents` or `'C:\Users\jan\Documents'`
  (single quotes).
- **Hooks** on a local Windows host run in `cmd.exe`; set `shell: powershell` for
  PowerShell.
- **Long paths:** turn on *Enable Win32 long paths* so that Explorer can open deep folders
  in a backup; `eichhorn doctor` warns when it is off.
- **Symlinks** from servers are kept in the file index and restored on macOS and Linux;
  on Windows they are listed but not created.

---

[Back to the docs index](../README.md)
