"""A stand-in for `ssh` in tests: runs the remote command locally with sh -c.

Accepts the same argument shapes as OpenSSH as used by Eichhorn and rsync:
    fake_ssh.py [-o opt]... [-l user] [-p port] [--] host command...
"""

import subprocess
import sys

args = sys.argv[1:]
i = 0
with_value = {"-o", "-l", "-p", "-i", "-F", "-S", "-O", "-E", "-J"}
while i < len(args):
    a = args[i]
    if a == "--":
        i += 1
        break
    if a in with_value:
        i += 2
        continue
    if a.startswith("-"):
        i += 1
        continue
    break
host, command = args[i], " ".join(args[i + 1 :])
if host == "unreachable":
    sys.stderr.write("ssh: connect to host unreachable: Connection refused\n")
    sys.exit(255)
sys.exit(subprocess.call(["sh", "-c", command]))
