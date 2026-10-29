"""Start a backup or repository check without waiting for it to finish."""

import fcntl
import os
import shutil
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


def launcher(command):
    """Run the job in its own systemd unit, so restarting the status page cannot stop it."""
    if os.geteuid() != 0 or not shutil.which("systemd-run") or not os.path.isdir("/run/systemd/system"):
        return []
    unit = "immich-backup-" + command + "-" + str(int(time.time()))
    app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [
        "systemd-run", "--quiet", "--collect", "--unit", unit,
        "--description", "Immich backup " + command,
        "--working-directory", app, "--setenv", "PYTHONPATH=" + app,
    ]


def _check_pids():
    listed = subprocess.run(["ps", "-eo", "pid=,args="], text=True, capture_output=True)
    pids = []
    for line in listed.stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        pid, _, args = text.partition(" ")
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        if re_check(args):
            pids.append(int(pid))
    return pids


def re_check(args):
    import re
    return bool(
        re.search(r"(?:^|\s)borg\s+check(?:\s|$)", args)
        or re.search(r"(?:^|\s)immich_backup\s+check(?:\s|$)", args)
    )


def stop_check_processes():
    listed = subprocess.run(
        ["systemctl", "list-units", "--type=service", "--state=activating,active", "--no-legend", "immich-backup-check*"],
        text=True,
        capture_output=True,
    )
    for line in listed.stdout.splitlines():
        name = line.split()[0] if line.split() else ""
        if name == "immich-backup-check.service" or name.startswith("immich-backup-check-"):
            subprocess.run(["systemctl", "stop", "--no-block", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for pid in _check_pids():
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def cancel_check(cfg, job_fn=None, stop_fn=None):
    """Stop a repository check. A backup or restore is left running."""
    from immich_backup.status import active_job
    if (job_fn or active_job)() != "check":
        raise ValueError("No repository check is running")
    os.makedirs(cfg.state_dir, exist_ok=True)
    path = os.path.join(cfg.state_dir, "check-cancel")
    tmp = path + "." + str(os.getpid()) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("cancel\n")
    os.replace(tmp, path)
    (stop_fn or stop_check_processes)()
    return "Stopping the repository check."


def start(cfg, command):
    if command in {"backup", "restore"} and busy(cfg):
        if command == "restore":
            return "A backup or restore is already running"
        return "A backup is already running"
    mark_pending(cfg, command)
    subprocess.Popen(
        launcher(command) + [sys.executable, "-m", "immich_backup", command, "--config", cfg.config_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if command == "backup":
        return "Backup started. The backup disk will spin up."
    if command == "restore":
        return "Restore started. Docker and Immich will be installed, and the backup disk will spin up."
    return "Repository check started. The backup disk will spin up."
