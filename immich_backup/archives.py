"""Archive list for the status page. Called at the end of a backup, while the disk is already spinning."""

import json
import os
import subprocess


def refresh(cfg):
    env = os.environ.copy()
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    env["BORG_LOCK_WAIT"] = "1"
    proc = subprocess.run(
        ["borg", "list", "--short", "--last", "12", "--format", "{archive} {time:%Y-%m-%dT%H:%M:%S}{NL}", cfg.repo],
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "borg list failed")
    rows = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, when = line.partition(" ")
        rows.append({"name": name, "time": when})
    rows.reverse()
    repo_id = ""
    with open(os.path.join(cfg.repo, "config"), encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("id = "):
                repo_id = line.split("=", 1)[1].strip()
    os.makedirs(cfg.state_dir, exist_ok=True)
    tmp = cfg.archive_cache + "." + str(os.getpid()) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"repo": repo_id, "archives": rows}, fh)
    os.replace(tmp, cfg.archive_cache)
