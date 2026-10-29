"""Start a backup or repository check without waiting for it to finish."""

import fcntl
import os
import subprocess
import sys
import time


def busy(cfg):
    os.makedirs(os.path.dirname(cfg.lock), exist_ok=True)
    handle = open(cfg.lock, "a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return True
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()
    return False


def mark_pending(cfg, command):
    os.makedirs(cfg.state_dir, exist_ok=True)
    path = os.path.join(cfg.state_dir, "pending-job")
    tmp = path + "." + str(os.getpid()) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(command + " " + str(time.time()) + "\n")
    os.replace(tmp, path)


def start(cfg, command):
    if command in {"backup", "restore"} and busy(cfg):
        if command == "restore":
            return "A backup or restore is already running"
        return "A backup is already running"
    mark_pending(cfg, command)
    subprocess.Popen(
        [sys.executable, "-m", "immich_backup", command, "--config", cfg.config_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if command == "backup":
        return "Backup started. The backup disk will spin up."
    if command == "restore":
        return "Restore started. Docker and Immich will be installed, and the backup disk will spin up."
    return "Repository check started. The backup disk will spin up."
