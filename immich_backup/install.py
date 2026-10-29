"""Write systemd units for the roles selected in the config."""

import os


def _python(cfg, command):
    return (
        "/usr/bin/python3 -m immich_backup " + command
        + " --config /etc/immich-backup/config.ini"
    )


def units(cfg):
    app = cfg.app_dir
    env = "Environment=PYTHONPATH=" + app + "\n"
    work = "WorkingDirectory=" + app + "\n"
    files = {}
    if "backup" in cfg.roles:
        files["immich-backup.service"] = (
            "[Unit]\nDescription=Immich Borg backup\n"
            "After=docker.service network-online.target\nWants=network-online.target\n\n"
            "[Service]\nType=oneshot\n"
            + env + work
            + "ExecStart=" + _python(cfg, "backup") + "\n"
            "Nice=10\nIOSchedulingClass=best-effort\nIOSchedulingPriority=7\n"
            "TimeoutStartSec=infinity\n"
        )
        files["immich-backup.timer"] = (
            "[Unit]\nDescription=Nightly Immich backup\n\n[Timer]\n"
            "OnCalendar=" + cfg.backup_calendar + "\nPersistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
    if "check" in cfg.roles:
        files["immich-backup-check.service"] = (
            "[Unit]\nDescription=Weekly Immich Borg repository check\n\n[Service]\n"
            "Type=oneshot\n" + env + work
            + "ExecStart=" + _python(cfg, "check") + "\n"
            "TimeoutStartSec=infinity\nNice=19\n"
        )
        files["immich-backup-check.timer"] = (
            "[Unit]\nDescription=Weekly Immich Borg integrity check\n\n[Timer]\n"
            "OnCalendar=" + cfg.check_calendar + "\nPersistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
    if "status" in cfg.roles:
        files["immich-backup-status.service"] = (
            "[Unit]\nDescription=Immich backup status page\n"
            "After=network-online.target\nWants=network-online.target\n\n"
            "[Service]\nType=simple\n" + env + work
            + "ExecStart=" + _python(cfg, "serve") + "\n"
            "Restart=on-failure\nRestartSec=2\n\n"
            "[Install]\nWantedBy=multi-user.target\n"
        )
    if "publish" in cfg.roles:
        files["immich-backup-publish.service"] = (
            "[Unit]\nDescription=Publish the latest SMART snapshot\n\n[Service]\n"
            "Type=oneshot\n" + env + work
            + "ExecStart=" + _python(cfg, "publish") + "\n"
        )
        files["immich-backup-publish.timer"] = (
            "[Unit]\nDescription=Publish saved SMART results every 30 minutes\n\n[Timer]\n"
            "OnBootSec=2min\nOnUnitActiveSec=30min\nPersistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
    if "spindown" in cfg.roles:
        files["immich-backup-spindown.service"] = (
            "[Unit]\nDescription=Spin down the backup disk after it is idle\n"
            "After=local-fs.target\n\n[Service]\nType=simple\n" + env + work
            + "ExecStart=" + _python(cfg, "spindown") + "\n"
            "Restart=always\nRestartSec=10\n\n[Install]\nWantedBy=multi-user.target\n"
        )
    return files


def write_units(cfg, output):
    target = output or "dist/systemd"
    os.makedirs(target, exist_ok=True)
    for name, text in units(cfg).items():
        path = os.path.join(target, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(path)
    if "notify" in cfg.roles:
        print("notify command: " + _python(cfg, "notify"))
    if "store" in cfg.roles:
        print("ssh forced command: " + _python(cfg, "store"))
