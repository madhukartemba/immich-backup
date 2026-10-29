"""List mounted filesystems so setup can choose the photo drive and the backup drive."""

import json
import os
import shutil
import subprocess


def _rotational(value, parent):
    if value in (True, 1, "1"):
        return True
    if value in (False, 0, "0"):
        return False
    return bool(parent)


def list_disks():
    fixture = os.environ.get("IMMICH_BACKUP_DISK_FIXTURE", "")
    if fixture:
        with open(fixture, encoding="utf-8") as fh:
            data = json.load(fh)
        return [item for item in data if isinstance(item, dict) and item.get("mount")]
    if shutil.which("lsblk"):
        return _lsblk()
    return _df()


def _lsblk():
    proc = subprocess.run(
        ["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT,UUID,LABEL,MODEL,SERIAL,PKNAME,ROTA"],
        text=True,
        capture_output=True,
        timeout=8,
    )
    if proc.returncode != 0:
        return []
    try:
        tree = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    flat = []

    def walk(nodes, parent_serial="", parent_rota=False):
        for node in nodes or []:
            serial = node.get("serial") or parent_serial
            rota = _rotational(node.get("rota"), parent_rota)
            walk(node.get("children"), serial, rota)
            mount = node.get("mountpoint") or ""
            if mount:
                flat.append({
                    "mount": mount,
                    "uuid": node.get("uuid") or "",
                    "fstype": node.get("fstype") or "",
                    "label": node.get("label") or node.get("model") or node.get("name") or mount,
                    "size": int(node.get("size") or 0),
                    "source": node.get("path") or "",
                    "serial": serial or "",
                    "model": node.get("model") or "",
                    "rotational": rota,
                })

    walk(tree.get("blockdevices"))
    return flat


def _df():
    proc = subprocess.run(["df", "-P"], text=True, capture_output=True, timeout=8)
    rows = []
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        mount = parts[-1]
        if mount == "/":
            continue
        rows.append({
            "mount": mount,
            "uuid": "",
            "fstype": "",
            "label": os.path.basename(mount) or mount,
            "size": int(parts[1]) * 512 if parts[1].isdigit() else 0,
            "source": parts[0],
            "serial": "",
            "model": "",
            "rotational": False,
        })
    return rows
