"""Archive list for the status page. Called at the end of a backup, while the disk is already spinning."""

import json
import os
import subprocess
import tarfile

exporting = False

_BLOCK = 512
_RECORD = 512 * 20


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


def _checked_archive(archive):
    from immich_backup.restore import ARCHIVE_RE
    if not ARCHIVE_RE.match(archive or ""):
        raise ValueError("Choose an archive")


def _borg_env(cfg):
    env = os.environ.copy()
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    env["BORG_REPO"] = cfg.repo
    return env


def tarinfo_for_item(item):
    """One Borg list entry as the GNU TarInfo borg export-tar would write.

    Unknown file types are skipped, matching Borg. A regular file with no size
    cannot be measured and returns False.
    """
    path = item.get("path") if isinstance(item, dict) else None
    if not isinstance(path, str):
        return None
    mode = item.get("mode")
    if isinstance(mode, str) and mode:
        kind = mode[0]
    else:
        kind = item.get("type") if isinstance(item.get("type"), str) and item.get("type") else "-"
    source = item.get("source") if isinstance(item.get("source"), str) else ""
    info = tarfile.TarInfo()
    info.name = path
    info.uname = item["user"] if isinstance(item.get("user"), str) else ""
    info.gname = item["group"] if isinstance(item.get("group"), str) else ""
    try:
        info.uid = int(item.get("uid") or 0)
        info.gid = int(item.get("gid") or 0)
    except (TypeError, ValueError):
        info.uid = 0
        info.gid = 0
    if kind == "h" or (kind == "-" and source):
        info.type = tarfile.LNKTYPE
        info.linkname = source
    elif kind == "d":
        info.type = tarfile.DIRTYPE
    elif kind == "l":
        info.type = tarfile.SYMTYPE
        info.linkname = source
    elif kind == "b":
        info.type = tarfile.BLKTYPE
    elif kind == "c":
        info.type = tarfile.CHRTYPE
    elif kind == "p":
        info.type = tarfile.FIFOTYPE
    elif kind == "-":
        if "size" not in item:
            return False
        try:
            info.size = int(item["size"])
        except (TypeError, ValueError):
            return False
        if info.size < 0:
            return False
        info.type = tarfile.REGTYPE
    else:
        return None
    return info


def _item_bytes(info):
    total = len(info.tobuf(tarfile.GNU_FORMAT, tarfile.ENCODING, "surrogateescape"))
    if info.isreg():
        total += (info.size + (_BLOCK - 1)) // _BLOCK * _BLOCK
    return total


def _finish_tar(total):
    total += _BLOCK * 2
    remainder = total % _RECORD
    if remainder:
        total += _RECORD - remainder
    return total


def gnu_tar_size(items):
    """Bytes borg export-tar writes for these items, including the ending blocks.

    Borg uses Python's streaming GNU tar. The header length comes from that
    same writer. File bytes are padded to 512, then the archive is padded to
    10240. None means the size cannot be known exactly.
    """
    total = 0
    for item in items:
        info = tarinfo_for_item(item)
        if info is False:
            return None
        if info is None:
            continue
        total += _item_bytes(info)
    return _finish_tar(total)


def archive_tar_bytes(cfg, archive):
    """Exact export size, from the archive listing. None if it cannot be measured."""
    _checked_archive(archive)
    proc = subprocess.Popen(
        ["borg", "list", "--json-lines", "--format", "{size}{NL}", cfg.repo + "::" + archive],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_borg_env(cfg),
        text=True,
        encoding="utf-8",
    )
    total = 0
    failed = False
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                failed = True
                break
            if not isinstance(item, dict):
                continue
            info = tarinfo_for_item(item)
            if info is False:
                failed = True
                break
            if info is not None:
                total += _item_bytes(info)
        if failed or proc.wait() != 0:
            return None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    return _finish_tar(total)


def export_tar(cfg, archive):
    """Stream one archive as a tar file. The name is restricted to an Immich archive."""
    _checked_archive(archive)
    return subprocess.Popen(
        ["borg", "export-tar", cfg.repo + "::" + archive, "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_borg_env(cfg),
    )
