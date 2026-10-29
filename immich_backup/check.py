"""Weekly repository check. It does not start while a backup is running."""

import json
import os
import re
import signal
import subprocess
import time

from immich_backup.backup import log


def parse_progress(line):
    """One Borg progress line, as JSON or as 'Checking segments 12.5%'."""
    text = (line or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(item, dict) or item.get("type") not in {None, "progress_percent", "progress_message"}:
            return None
        message = str(item.get("message") or "").strip()
        current = item.get("current") if isinstance(item.get("current"), (int, float)) else None
        total = item.get("total") if isinstance(item.get("total"), (int, float)) else None
        percent = None
        if current is not None and total:
            percent = max(0.0, min(100.0, 100.0 * float(current) / float(total)))
        else:
            found = re.search(r"(\d+(?:\.\d+)?)\s*%", message)
            percent = float(found.group(1)) if found else None
        if percent is None:
            return None
        phase = re.sub(r"\s*\d+(?:\.\d+)?\s*%$", "", message).strip() or "Checking repository"
        return {"phase": phase[:80], "percent": percent, "current": current, "total": total}
    found = re.search(r"^(?:Remote:\s*)?(.+?)\s+(\d+(?:\.\d+)?)\s*%\s*$", text)
    if not found:
        return None
    return {
        "phase": found.group(1).strip()[:80] or "Checking repository",
        "percent": float(found.group(2)),
        "current": None,
        "total": None,
    }


def _save_progress(path, item, started):
    body = dict(item)
    body["started"] = started
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(body, fh)
    os.replace(tmp, path)


def _mounted(cfg):
    mounted = subprocess.run(
        ["findmnt", "-n", "-o", "TARGET,UUID", "-T", cfg.backup_mount],
        text=True,
        capture_output=True,
    )
    fields = mounted.stdout.split()
    return mounted.returncode == 0 and len(fields) >= 2 and fields[0] == cfg.backup_mount and fields[1] == cfg.backup_uuid


def _borg_busy():
    return subprocess.run(["pgrep", "-x", "borg"], stdout=subprocess.DEVNULL).returncode == 0


def run(cfg, popen=None, mounted_fn=None, busy_fn=None):
    mounted_fn = mounted_fn or _mounted
    busy_fn = busy_fn or _borg_busy
    if not mounted_fn(cfg):
        log(cfg, "repository check failed, backup disk is not mounted")
        return 1
    if busy_fn():
        log(cfg, "repository check skipped because a backup is running")
        return 0
    log(cfg, "starting repository check")
    os.makedirs(cfg.state_dir, exist_ok=True)
    progress_path = os.path.join(cfg.state_dir, "check-progress")
    cancel_path = os.path.join(cfg.state_dir, "check-cancel")
    try:
        os.remove(cancel_path)
    except OSError:
        pass
    env = os.environ.copy()
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    opener = popen or subprocess.Popen
    proc = opener(
        ["borg", "check", "--progress", "--log-json", cfg.repo],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    stop = {"flag": False}

    def request_stop(signum, _frame):
        stop["flag"] = True
        if proc.poll() is None:
            proc.terminate()

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    started = None
    cancelled = False
    try:
        while True:
            if stop["flag"] or os.path.exists(cancel_path):
                cancelled = True
                if proc.poll() is None:
                    proc.terminate()
                break
            line = proc.stderr.readline()
            if not line:
                break
            item = parse_progress(line)
            if not item:
                continue
            if started is None:
                started = time.time()
            _save_progress(progress_path, item, started)
        rc = proc.wait()
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
        for path in (progress_path, cancel_path):
            try:
                os.remove(path)
            except OSError:
                pass
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if cancelled or rc in {-signal.SIGTERM, -signal.SIGINT, 128 + signal.SIGTERM, 128 + signal.SIGINT}:
        log(cfg, "repository check cancelled")
        return 0
    if rc == 0:
        log(cfg, "repository check passed")
    else:
        log(cfg, "repository check failed with exit " + str(rc))
    return rc
