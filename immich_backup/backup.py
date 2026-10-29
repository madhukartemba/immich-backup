"""Nightly Borg backup. The log lines match what the status page already understands."""

import datetime
import fcntl
import os
import subprocess

from immich_backup.archives import refresh


def stamp():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def log(cfg, message):
    line = stamp() + " " + message
    os.makedirs(os.path.dirname(cfg.log), exist_ok=True)
    with open(cfg.log, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)


def notify(cfg, message):
    if not cfg.notify_command:
        return
    import shlex
    subprocess.run(shlex.split(cfg.notify_command), input=message + "\n", text=True, check=False)


def fail(cfg, message):
    log(cfg, "FAILED " + message)
    notify(cfg, "FAILED " + message)
    raise SystemExit(1)


def run_out(args, env=None):
    return subprocess.run(args, text=True, capture_output=True, env=env)


def avail_kb(path):
    proc = run_out(["df", "-Pk", path])
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if proc.returncode != 0 or len(lines) < 2:
        return 0
    return int(lines[-1].split()[3])


def mount_fields(path, fields):
    proc = run_out(["findmnt", "-n", "-o", fields, "-T", path])
    if proc.returncode != 0:
        return []
    return proc.stdout.split()


def repo_has_data(repo):
    data = os.path.join(repo, "data")
    for _root, _dirs, files in os.walk(data):
        if files:
            return True
    return False


def space_note(cfg, free_kb):
    free_gb = free_kb / 1024 / 1024
    for limit in sorted(cfg.warn_gb):
        if free_gb < limit:
            return "less than " + str(limit) + " GB is free on the backup disk."
    return ""


def check_backup_disk(cfg):
    fields = mount_fields(cfg.backup_mount, "TARGET,UUID,FSTYPE")
    if len(fields) < 3:
        fail(cfg, "backup disk is not mounted at " + cfg.backup_mount)
    target, uuid, fstype = fields[0], fields[1], fields[2]
    try:
        marker = open(cfg.marker, encoding="utf-8").read().strip()
    except OSError:
        marker = ""
    if target != cfg.backup_mount or fstype != cfg.backup_fstype or uuid != cfg.backup_uuid or marker != uuid:
        fail(cfg, "backup disk is not mounted at " + cfg.backup_mount)
    if not cfg.backup_parent_serial:
        return
    source = mount_fields(cfg.backup_mount, "SOURCE")
    if not source:
        fail(cfg, "backup mount is not the configured disk")
    parent = run_out(["lsblk", "-no", "PKNAME", source[0]]).stdout.split()
    if not parent:
        fail(cfg, "backup mount is not the configured disk")
    disk = "/dev/" + parent[0]
    serial = run_out(["lsblk", "-dno", "SERIAL", disk]).stdout.split()
    size = run_out(["lsblk", "-bdno", "SIZE", disk]).stdout.split()
    serial = serial[0] if serial else ""
    size = int(size[0]) if size else 0
    size_ok = True
    if cfg.backup_parent_max_bytes:
        size_ok = cfg.backup_parent_min_bytes <= size <= cfg.backup_parent_max_bytes
    if serial != cfg.backup_parent_serial or not size_ok:
        fail(cfg, "backup mount is not the configured disk")


def check_media(cfg):
    fields = mount_fields(cfg.media_mount, "TARGET,UUID")
    if len(fields) < 2 or fields[0] != cfg.media_mount or fields[1] != cfg.media_uuid:
        fail(cfg, "Immich media disk is not mounted at " + cfg.media_mount)


def dump_database(cfg):
    os.makedirs(os.path.dirname(cfg.dump), mode=0o700, exist_ok=True)
    running = run_out(["docker", "inspect", "-f", "{{.State.Running}}", cfg.postgres_container])
    if running.returncode != 0 or running.stdout.strip() != "true":
        fail(cfg, cfg.postgres_container + " is not running")
    tmp = cfg.dump + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        proc = subprocess.run(
            ["docker", "exec", "-i", cfg.postgres_container, "pg_dump", "--clean", "--if-exists",
             "--dbname=" + cfg.database, "--username=" + cfg.database_user],
            stdout=fh,
        )
    if proc.returncode != 0:
        fail(cfg, "pg_dump failed")
    with open(tmp, encoding="utf-8", errors="replace") as fh:
        if "PostgreSQL database dump" not in fh.read(4000):
            fail(cfg, "database dump is missing the PostgreSQL header")
    os.replace(tmp, cfg.dump)


def borg_env(cfg):
    env = os.environ.copy()
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    env["BORG_REPO"] = cfg.repo
    return env


def run(cfg):
    os.makedirs(os.path.dirname(cfg.lock), exist_ok=True)
    lock = open(cfg.lock, "a", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        notify(cfg, "FAILED Immich backup is already running")
        return 1
    check_backup_disk(cfg)
    check_media(cfg)
    if not os.path.isfile(os.path.join(cfg.repo, "config")):
        fail(cfg, "Borg repository is missing")
    free = avail_kb(cfg.backup_mount)
    if not repo_has_data(cfg.repo):
        if free < cfg.initial_free_gb * 1024 * 1024:
            fail(cfg, "not enough free space for the initial backup")
    elif free < cfg.stop_free_gb * 1024 * 1024:
        fail(cfg, "less than " + str(cfg.stop_free_gb) + " GB free on the backup disk")
    dump_database(cfg)
    log(cfg, "starting local borg create")
    create = subprocess.run(
        [
            "borg", "create", "--stats", "--compression", "lz4", "--one-file-system",
            "--exclude", cfg.media_mount + "/lost+found",
            "--exclude", "sh:" + cfg.media_mount + "/lost+found/**",
            "::immich-{now:%Y-%m-%d_%H%M%S}",
            cfg.media_mount, cfg.dump, cfg.compose, cfg.env_file,
        ],
        env=borg_env(cfg),
    )
    rc = create.returncode
    log(cfg, "borg create exit " + str(rc))
    if rc >= 2:
        fail(cfg, "borg create failed with exit " + str(rc))
    prune_rc = 0
    if cfg.prune:
        prune = subprocess.run(
            ["borg", "prune", "--stats", "--keep-daily=" + str(cfg.keep_daily)],
            env=borg_env(cfg),
        )
        prune_rc = prune.returncode
        if prune_rc == 0:
            compact = subprocess.run(["borg", "compact"], env=borg_env(cfg))
            prune_rc = compact.returncode
        log(cfg, "prune exit " + str(prune_rc))
    try:
        refresh(cfg)
    except Exception:
        log(cfg, "archive list cache update failed")
    note = space_note(cfg, avail_kb(cfg.backup_mount))
    if rc == 1 or prune_rc != 0:
        message = "WARNING Immich backup archive was created. borg exit " + str(rc) + ". prune exit " + str(prune_rc) + "."
        if note:
            message += " Also, " + note
        notify(cfg, message)
    elif note:
        notify(cfg, "WARNING Immich backup finished, but " + note)
    else:
        notify(cfg, "OK Immich backup finished")
    return 0
