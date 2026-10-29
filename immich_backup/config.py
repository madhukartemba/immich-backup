"""One configuration file for the backup, the status page, and health checks."""

import configparser
from dataclasses import dataclass


def _text(parser, section, key, default=""):
    if not parser.has_option(section, key):
        return default
    return parser.get(section, key).strip()


def _int(parser, section, key, default):
    value = _text(parser, section, key, "")
    return default if value == "" else int(value)


def _bool(parser, section, key, default):
    value = _text(parser, section, key, "")
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _list(parser, section, key, default):
    value = _text(parser, section, key, "")
    if value == "":
        return default
    return tuple(part.strip() for part in value.split(",") if part.strip())


@dataclass
class Config:
    state_dir: str
    log: str
    lock: str
    backup_mount: str
    media_mount: str
    repo: str
    dump: str
    compose: str
    env_file: str
    backup_uuid: str
    media_uuid: str
    backup_fstype: str
    backup_parent_serial: str
    backup_parent_min_bytes: int
    backup_parent_max_bytes: int
    postgres_container: str
    database: str
    database_user: str
    keep_daily: int
    prune: bool
    initial_free_gb: int
    stop_free_gb: int
    warn_gb: tuple
    backup_calendar: str
    check_calendar: str
    schedule_label: str
    status_host: str
    status_port: int
    status_ttl: int
    immich_ping: str
    backup_service: str
    backup_timer: str
    check_service: str
    notify_command: str
    email: str
    backup_disk: str
    backup_disk_name: str
    primary_match: str
    primary_name: str
    os_disk: str
    os_disk_name: str
    publish_target: str
    publish_key: str
    smartd_backup_json: str
    smartd_primary_json: str
    spindown_idle_seconds: int
    spindown_enabled: bool
    spin_command: str
    backup_rotational: bool
    app_dir: str
    roles: tuple
    archive_cache: str
    smart_file: str
    marker: str
    restore_archive: str
    config_path: str


def load(path):
    parser = configparser.ConfigParser()
    read = parser.read(path)
    if not read:
        raise SystemExit("cannot read config " + path)
    state_dir = _text(parser, "paths", "state_dir", "/var/lib/immich-backup")
    backup_mount = _text(parser, "paths", "backup_mount", "/mnt/immich-backup")
    return Config(
        state_dir=state_dir,
        log=_text(parser, "paths", "log", "/var/log/immich-backup.log"),
        lock=_text(parser, "paths", "lock", "/var/lock/immich-backup.lock"),
        backup_mount=backup_mount,
        media_mount=_text(parser, "paths", "media_mount", "/mnt/immich-media"),
        repo=_text(parser, "paths", "repo", backup_mount + "/immich-borg"),
        dump=_text(parser, "paths", "dump", "/var/backups/immich/immich-database.sql"),
        compose=_text(parser, "paths", "compose", "/opt/immich/docker-compose.yml"),
        env_file=_text(parser, "paths", "env_file", "/opt/immich/.env"),
        backup_uuid=_text(parser, "identity", "backup_uuid"),
        media_uuid=_text(parser, "identity", "media_uuid"),
        backup_fstype=_text(parser, "identity", "backup_fstype", "ext4"),
        backup_parent_serial=_text(parser, "identity", "backup_parent_serial"),
        backup_parent_min_bytes=_int(parser, "identity", "backup_parent_min_bytes", 0),
        backup_parent_max_bytes=_int(parser, "identity", "backup_parent_max_bytes", 0),
        postgres_container=_text(parser, "backup", "postgres_container", "immich_postgres"),
        database=_text(parser, "backup", "database", "immich"),
        database_user=_text(parser, "backup", "database_user", "postgres"),
        keep_daily=_int(parser, "backup", "keep_daily", 7),
        prune=_bool(parser, "backup", "prune", True),
        initial_free_gb=_int(parser, "backup", "initial_free_gb", 360),
        stop_free_gb=_int(parser, "backup", "stop_free_gb", 10),
        warn_gb=tuple(int(part) for part in _list(parser, "backup", "warn_gb", ("50", "20", "10"))),
        backup_calendar=_text(parser, "schedule", "backup", "*-*-* 01:00:00"),
        check_calendar=_text(parser, "schedule", "check", "Sun *-*-* 10:00:00"),
        schedule_label=_text(parser, "schedule", "label", "01:00"),
        status_host=_text(parser, "status", "host", "0.0.0.0"),
        status_port=_int(parser, "status", "port", 8090),
        status_ttl=_int(parser, "status", "ttl", 15),
        immich_ping=_text(parser, "status", "immich_ping", "http://127.0.0.1:2283/api/server/ping"),
        backup_service=_text(parser, "status", "backup_service", "immich-backup.service"),
        backup_timer=_text(parser, "status", "backup_timer", "immich-backup.timer"),
        check_service=_text(parser, "status", "check_service", "immich-backup-check.service"),
        notify_command=_text(parser, "notify", "command"),
        email=_text(parser, "notify", "email"),
        backup_disk=_text(parser, "health", "backup_disk"),
        backup_disk_name=_text(parser, "health", "backup_disk_name", "Backup disk"),
        primary_match=_text(parser, "health", "primary_match"),
        primary_name=_text(parser, "health", "primary_name", "Photo NVMe"),
        os_disk=_text(parser, "health", "os_disk"),
        os_disk_name=_text(parser, "health", "os_disk_name", "OS SSD"),
        publish_target=_text(parser, "health", "publish_target"),
        publish_key=_text(parser, "health", "publish_key"),
        smartd_backup_json=_text(parser, "health", "smartd_backup_json"),
        smartd_primary_json=_text(parser, "health", "smartd_primary_json"),
        spindown_idle_seconds=_int(parser, "health", "spindown_idle_seconds", 600),
        spindown_enabled=_bool(parser, "health", "spindown_enabled", True),
        spin_command=_text(parser, "health", "spin_command"),
        backup_rotational=_bool(parser, "identity", "backup_rotational", False),
        app_dir=_text(parser, "install", "app_dir", "/opt/immich-backup"),
        roles=_list(parser, "install", "roles", ("backup", "status", "check")),
        archive_cache=state_dir + "/archives.cache",
        smart_file=state_dir + "/smart.json",
        marker=backup_mount + "/.backup-disk",
        restore_archive=_text(parser, "restore", "archive"),
        config_path=path,
    )


def update(path, values):
    """Update config values. values is {section: {key: value}}."""
    parser = configparser.ConfigParser()
    parser.read(path)
    for section, keys in values.items():
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in keys.items():
            parser.set(section, key, str(value))
    with open(path, "w", encoding="utf-8") as fh:
        parser.write(fh)
