"""Weekly repository check. It does not start while a backup is running."""

import os
import subprocess


def run(cfg):
    env = os.environ.copy()
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    mounted = subprocess.run(
        ["findmnt", "-n", "-o", "TARGET,UUID", "-T", cfg.backup_mount],
        text=True,
        capture_output=True,
    )
    fields = mounted.stdout.split()
    if mounted.returncode != 0 or len(fields) < 2 or fields[0] != cfg.backup_mount or fields[1] != cfg.backup_uuid:
        print("backup disk is not mounted", flush=True)
        return 1
    running = subprocess.run(["pgrep", "-x", "borg"], stdout=subprocess.DEVNULL)
    if running.returncode == 0:
        print("backup is running; skipping integrity check")
        return 0
    return subprocess.run(["borg", "check", cfg.repo], env=env).returncode
