"""Weekly repository check. It does not start while a backup is running."""

import os
import subprocess

from immich_backup.backup import log


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
        log(cfg, "repository check failed, backup disk is not mounted")
        return 1
    running = subprocess.run(["pgrep", "-x", "borg"], stdout=subprocess.DEVNULL)
    if running.returncode == 0:
        log(cfg, "repository check skipped because a backup is running")
        return 0
    log(cfg, "starting repository check")
    rc = subprocess.run(["borg", "check", cfg.repo], env=env).returncode
    if rc == 0:
        log(cfg, "repository check passed")
    else:
        log(cfg, "repository check failed with exit " + str(rc))
    return rc
