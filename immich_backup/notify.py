"""Record a backup result and mail only failures and warnings."""

import datetime
import os
import subprocess
import sys


def run(cfg):
    message = sys.stdin.read(20000)
    os.makedirs(cfg.state_dir, mode=0o755, exist_ok=True)
    with open(os.path.join(cfg.state_dir, "last-result"), "w", encoding="utf-8") as fh:
        fh.write(message if message.endswith("\n") or message == "" else message + "\n")
    first = message.splitlines()[0] if message.splitlines() else ""
    from immich_backup.health import check
    with open(os.path.join(cfg.state_dir, "last-smart"), "w", encoding="utf-8") as fh:
        saved = sys.stdout
        sys.stdout = fh
        try:
            check(cfg)
        except Exception as exc:
            fh.write(str(exc) + "\n")
        finally:
            sys.stdout = saved
    if first.startswith("OK"):
        with open(os.path.join(cfg.state_dir, "last-success"), "w", encoding="utf-8") as fh:
            fh.write(datetime.datetime.now().astimezone().isoformat(timespec="seconds") + "\n")
        return 0
    if not cfg.email:
        return 0
    subject = "Immich backup alert"
    if first.startswith("FAILED"):
        subject = "Immich backup FAILED"
    elif first.startswith("WARNING"):
        subject = "Immich backup warning"
    subprocess.run(
        ["mail", "-r", cfg.email, "-s", subject, cfg.email],
        input=message if message.endswith("\n") else message + "\n",
        text=True,
        check=False,
    )
    return 0
