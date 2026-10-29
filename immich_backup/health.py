"""Disk health. The backup disk is skipped when it is asleep. Publish and store never query a disk."""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

ATA_IDS = {5, 187, 196, 197, 198}


def smartctl(dev, skip_when_asleep):
    args = ["smartctl", "-j", "-H", "-A", dev]
    if skip_when_asleep:
        args[1:1] = ["-n", "standby"]
    proc = subprocess.run(args, text=True, capture_output=True)
    text = proc.stdout + "\n" + proc.stderr
    low = text.lower()
    if skip_when_asleep and ("standby mode" in low or "sleep mode" in low):
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"unreadable": text.strip()[-400:]}


def problems(name, data):
    if data is None:
        return []
    if data.get("unreadable"):
        return [name + ": SMART data could not be read"]
    found = []
    status = data.get("smart_status") or {}
    if status.get("passed") is False:
        found.append(name + ": SMART health check failed")
    for attr in ((data.get("ata_smart_attributes") or {}).get("table") or []):
        if attr.get("id") not in ATA_IDS:
            continue
        raw = (attr.get("raw") or {}).get("value")
        if isinstance(raw, int) and raw > 0:
            found.append(name + ": " + str(attr.get("name")) + " is " + str(raw))
    nvme = data.get("nvme_smart_health_information_log") or {}
    if nvme.get("critical_warning"):
        found.append(name + ": NVMe critical warning " + str(nvme.get("critical_warning")))
    if nvme.get("media_errors"):
        found.append(name + ": NVMe media errors " + str(nvme.get("media_errors")))
    used = nvme.get("percentage_used")
    if isinstance(used, int) and used >= 90:
        found.append(name + ": NVMe wear is " + str(used) + " percent")
    return found


def primary_path(cfg):
    base = "/dev/disk/by-id"
    if not cfg.primary_match or not os.path.isdir(base):
        return ""
    found = []
    for name in os.listdir(base):
        if cfg.primary_match not in name or "part" in name:
            continue
        path = os.path.join(base, name)
        real = os.path.basename(os.path.realpath(path))
        if real.startswith("nvme") and "n" in real:
            found.append(path)
    found.sort(key=lambda path: (not os.path.basename(os.path.realpath(path)).endswith("n1"), path))
    return found[0] if found else ""


def unsupported(data):
    if not isinstance(data, dict):
        return True
    support = data.get("smart_support") or {}
    if support.get("available") is False:
        return True
    text = str(data.get("unreadable") or "").lower()
    return "unavailable" in text or "not supported" in text


def device_from_source(source, type_name, parent_name):
    """The disk to ask for SMART. A filesystem on the disk itself has no parent partition."""
    if parent_name:
        return "/dev/" + parent_name
    if type_name == "disk" and source:
        return source if source.startswith("/") else "/dev/" + source
    return ""


def device_for_mount(mount):
    if not mount:
        return ""
    found = subprocess.run(
        ["findmnt", "-n", "-o", "SOURCE", "-T", mount],
        text=True,
        capture_output=True,
    )
    source = (found.stdout or "").split()
    if found.returncode != 0 or not source:
        return ""
    info = subprocess.run(
        ["lsblk", "-ndo", "TYPE,PKNAME", source[0]],
        text=True,
        capture_output=True,
    )
    fields = (info.stdout or "").split()
    if info.returncode != 0 or not fields:
        return ""
    parent = fields[1] if len(fields) > 1 else ""
    return device_from_source(source[0], fields[0], parent)


def during_backup(cfg, smartctl_fn=None, device_fn=None):
    """Read SMART after a backup has spun the disks up. A standby backup disk is left asleep."""
    smartctl_fn = smartctl_fn or smartctl
    device_fn = device_fn or device_for_mount
    if smartctl_fn is smartctl and shutil.which("smartctl") is None:
        return ["smartctl is not installed"]
    photo = primary_path(cfg) or device_fn(cfg.media_mount)
    backup = cfg.backup_disk or device_fn(cfg.backup_mount)
    checks = []
    if backup:
        checks.append(("backup", cfg.backup_disk_name or "Backup disk", backup, True))
    if photo and os.path.realpath(photo) != os.path.realpath(backup or ""):
        checks.append(("primary", cfg.primary_name or "Photo disk", photo, False))
    found = []
    for key, name, dev, skip in checks:
        if not dev or not os.path.exists(dev):
            continue
        data = smartctl_fn(dev, skip)
        if data is None:
            continue
        if unsupported(data):
            remember_note(cfg, key, True, "SMART is not available for this disk")
            continue
        remember(cfg, key, data, name)
        found.extend(item for item in problems(name, data) if "could not be read" not in item)
    if cfg.media_mount and not any(item[0] == "primary" for item in checks):
        remember_note(cfg, "primary", True, "SMART is not available for this disk")
    return found


def remember_note(cfg, key, ok, summary):
    if not key:
        return
    os.makedirs(cfg.state_dir, exist_ok=True)
    current = load_json(cfg.smart_file)
    current[key] = {
        "ok": bool(ok),
        "checked_at": datetime.now().strftime("%a %H:%M"),
        "at": time.time(),
        "summary": summary,
    }
    tmp = cfg.smart_file + "." + str(os.getpid()) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(current, fh)
    os.replace(tmp, cfg.smart_file)


def remember(cfg, key, data, label):
    if data is None or not key:
        return
    os.makedirs(cfg.state_dir, exist_ok=True)
    current = {}
    try:
        with open(cfg.smart_file, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            current = loaded
    except (OSError, json.JSONDecodeError):
        current = {}
    found = problems(label, data)
    current[key] = {
        "ok": not found,
        "checked_at": datetime.now().strftime("%a %H:%M"),
        "at": time.time(),
        "summary": "Healthy" if not found else "; ".join(item.split(": ", 1)[-1] for item in found),
    }
    tmp = cfg.smart_file + "." + str(os.getpid()) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(current, fh)
    os.replace(tmp, cfg.smart_file)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def issues_for(data, kind):
    issues = []
    if (data.get("smart_status") or {}).get("passed") is False:
        issues.append("SMART health check failed")
    if kind == "ata":
        for attr in ((data.get("ata_smart_attributes") or {}).get("table") or []):
            if attr.get("id") not in ATA_IDS:
                continue
            raw = (attr.get("raw") or {}).get("value")
            if isinstance(raw, int) and raw > 0:
                issues.append(str(attr.get("name")) + " is " + str(raw))
        return issues
    nvme = data.get("nvme_smart_health_information_log") or {}
    if nvme.get("critical_warning"):
        issues.append("critical warning " + str(nvme.get("critical_warning")))
    if nvme.get("media_errors"):
        issues.append("media errors " + str(nvme.get("media_errors")))
    used = nvme.get("percentage_used")
    if isinstance(used, int) and used >= 90:
        issues.append("wear is " + str(used) + " percent")
    return issues


def snapshot(path, kind):
    data = load_json(path)
    if not data:
        return None
    issues = issues_for(data, kind)
    checked = os.path.getmtime(path)
    return {
        "ok": not issues,
        "checked_at": datetime.fromtimestamp(checked).strftime("%a %H:%M"),
        "at": checked,
        "summary": "Healthy" if not issues else "; ".join(issues),
    }


def publish(cfg):
    current = load_json(cfg.smart_file)
    for key, path, kind in (
        ("backup", cfg.smartd_backup_json, "ata"),
        ("primary", cfg.smartd_primary_json, "nvme"),
    ):
        if not path:
            continue
        fresh = snapshot(path, kind)
        if not fresh:
            continue
        old = current.get(key) if isinstance(current.get(key), dict) else {}
        if float(fresh["at"]) >= float(old.get("at") or 0):
            current[key] = fresh
    if not current:
        return
    os.makedirs(cfg.state_dir, exist_ok=True)
    tmp = cfg.smart_file + "." + str(os.getpid()) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(current, fh)
    os.replace(tmp, cfg.smart_file)
    payload = {key: current[key] for key in ("backup", "primary") if key in current}
    if not payload or not cfg.publish_target or not os.path.exists(cfg.publish_key):
        return
    subprocess.run(
        [
            "ssh", "-T", "-i", cfg.publish_key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "RequestTTY=no", "-o", "StrictHostKeyChecking=accept-new", cfg.publish_target,
        ],
        input=json.dumps(payload),
        text=True,
        check=False,
    )


def check(cfg):
    checks = [
        ("backup", cfg.backup_disk_name, cfg.backup_disk, True),
        ("primary", cfg.primary_name, primary_path(cfg), False),
        ("", cfg.os_disk_name, cfg.os_disk, False),
    ]
    found = []
    for key, name, dev, skip in checks:
        if not dev or not os.path.exists(dev):
            if dev or key:
                found.append(name + ": disk was not found")
            continue
        data = smartctl(dev, skip)
        remember(cfg, key, data, name)
        if data is None:
            continue
        found.extend(problems(name, data))
    publish(cfg)
    if not found:
        print("SMART ok")
        return 0
    body = "\n".join(found)
    print(body)
    if cfg.email:
        subprocess.run(
            ["mail", "-r", cfg.email, "-s", "Disk health warning", cfg.email],
            input=body + "\n",
            text=True,
            check=False,
        )
    return 1


def store(cfg):
    raw = sys.stdin.read(12000)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise SystemExit(1)
    clean = {}
    for key in ("backup", "primary"):
        item = data.get(key)
        if not isinstance(item, dict):
            continue
        checked = str(item.get("checked_at") or "")[:40]
        if not checked:
            continue
        clean[key] = {
            "ok": bool(item.get("ok")),
            "checked_at": checked,
            "summary": str(item.get("summary") or "")[:160],
        }
    if not clean:
        raise SystemExit(1)
    os.makedirs(cfg.state_dir, exist_ok=True)
    current = load_json(cfg.smart_file)
    current.update(clean)
    tmp = cfg.smart_file + "." + str(os.getpid()) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(current, fh)
    os.replace(tmp, cfg.smart_file)
