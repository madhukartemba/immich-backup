"""Spin the backup disk down after it has been idle. A power-mode check does not wake a sleeping drive."""

import os
import subprocess
import time

from immich_backup.config import load


def diskstats(name):
    try:
        with open("/proc/diskstats", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) > 9 and parts[2] == name:
                    return " ".join((parts[3], parts[5], parts[7], parts[9]))
    except OSError:
        return ""
    return ""


def power_state(dev):
    """hdparm -C reads the power mode and leaves a standby disk asleep."""
    try:
        proc = subprocess.run(["hdparm", "-C", dev], text=True, capture_output=True)
    except FileNotFoundError:
        return ""
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        if "drive state" in line:
            return line.split(":", 1)[1].replace(" ", "")
    return ""


def _sys_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def drive_kind(dev, sys_root="/sys"):
    """hdd, ssd, or virtual. Reads sysfs only, so a standby disk stays asleep."""
    try:
        name = os.path.basename(os.path.realpath(dev))
    except OSError:
        return ""
    if not name:
        return ""
    link = os.path.join(sys_root, "class", "block", name)
    if not os.path.exists(link):
        return ""
    if os.path.exists(os.path.join(link, "partition")):
        parent = os.path.basename(os.path.dirname(os.path.realpath(link)))
        if parent:
            name = parent
    base = os.path.join(sys_root, "block", name)
    identity = (
        _sys_text(os.path.join(base, "device", "vendor"))
        + " "
        + _sys_text(os.path.join(base, "device", "model"))
    ).lower()
    if any(mark in identity for mark in ("qemu", "vmware", "virtualbox", "vbox", "virtual disk", "virtual hd")):
        return "virtual"
    rota = _sys_text(os.path.join(base, "queue", "rotational"))
    if rota == "0":
        return "ssd"
    if rota == "1":
        return "hdd"
    return ""


def spin_label(power):
    text = (power or "").lower().replace(" ", "")
    if text in {"standby", "sleeping"}:
        return "Sleeping"
    if text == "active/idle":
        return "Spinning"
    return ""


def should_standby(cfg, idle_seconds, power):
    """Return true when an idle spinning hard drive should be sent to standby."""
    if not cfg.spindown_enabled:
        return False
    if idle_seconds < cfg.spindown_idle_seconds:
        return False
    return power == "active/idle"


def run(cfg):
    path = cfg.config_path
    last = ""
    since = time.time()
    while True:
        cfg = load(path) if path else cfg
        if not cfg.spindown_enabled or not cfg.backup_disk or not os.path.exists(cfg.backup_disk):
            time.sleep(30)
            continue
        dev = os.path.realpath(cfg.backup_disk)
        name = os.path.basename(dev)
        io = diskstats(name)
        now = time.time()
        if not io or io != last:
            last = io
            since = now
        elif now - since >= cfg.spindown_idle_seconds:
            if should_standby(cfg, now - since, power_state(dev)):
                subprocess.run(["hdparm", "-y", dev], stdout=subprocess.DEVNULL)
                subprocess.run(["logger", "-t", "immich-backup", "backup disk standby after idle"], check=False)
            since = now
        time.sleep(30)
