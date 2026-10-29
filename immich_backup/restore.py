"""Rebuild Immich from a Borg archive.

The backup disk is read. Nothing is formatted. Docker, the Compose plugin,
Borg, and rsync are installed when they are missing. Immich itself is the
compose file and images from the archive, not a distro package.
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import time

from immich_backup.backup import borg_env, check_backup_disk, check_media, fail, log


ARCHIVE_RE = re.compile(r"^immich-[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$")
SKIP_DIRS = {"upload", "library", "encoded-video", "thumbs", "profile"}
DOCKER_PACKAGES = (
    "docker-ce",
    "docker-ce-cli",
    "containerd.io",
    "docker-buildx-plugin",
    "docker-compose-plugin",
)


class Layout:
    def __init__(self, compose, env, dump, media):
        self.compose = compose
        self.env = env
        self.dump = dump
        self.media = media

    def missing(self):
        names = []
        if not self.compose:
            names.append("docker-compose.yml")
        if not self.env:
            names.append(".env")
        if not self.dump:
            names.append("immich-database.sql")
        if not self.media:
            names.append("photo library")
        return names


class ProcessRunner:
    def run(self, args, env=None, input=None):
        return subprocess.run(args, env=env, input=input, capture_output=True)

    def run_input(self, args, chunks, env=None):
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            for chunk in chunks:
                proc.stdin.write(chunk)
            proc.stdin.close()
        except Exception:
            proc.kill()
            proc.wait()
            raise
        out, err = proc.communicate()
        return subprocess.CompletedProcess(args, proc.returncode, out, err)


def assert_safe(cfg):
    media = os.path.realpath(cfg.media_mount or "")
    backup = os.path.realpath(cfg.backup_mount or "")
    if cfg.media_mount in {"", "/"} or cfg.backup_mount in {"", "/"}:
        raise ValueError("Refusing the root disk")
    if media == backup:
        raise ValueError("The photo drive and backup drive must be different")
    repo = os.path.realpath(cfg.repo or "")
    if repo == media or repo.startswith(media + os.sep) or media.startswith(repo + os.sep):
        raise ValueError("The photo drive and the backup repository must be different")
    for path in (cfg.compose, cfg.env_file, cfg.dump):
        if path in {"", "/"}:
            raise ValueError("Refusing to write to the root disk")


def os_identity(text):
    values = {}
    for line in text.splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    distro = values.get("ID", "")
    codename = values.get("VERSION_CODENAME", "")
    if distro not in {"debian", "ubuntu"} or not codename:
        raise ValueError("Restore installs Docker with apt. This system is not Debian or Ubuntu.")
    return distro, codename


def docker_source_line(distro, codename, arch):
    return (
        "deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] "
        "https://download.docker.com/linux/%s %s stable\n" % (arch, distro, codename)
    )


def _apt_env():
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    return env


def _check(cfg, runner, args, env=None, message="command failed"):
    proc = runner.run(args, env=env)
    if proc.returncode != 0:
        fail(cfg, message)
    return proc


def missing_tools(runner):
    missing = []
    if runner.run(["borg", "--version"]).returncode != 0:
        missing.append("borg")
    if runner.run(["rsync", "--version"]).returncode != 0:
        missing.append("rsync")
    if runner.run(["docker", "compose", "version"]).returncode != 0:
        missing.append("docker")
    return missing


def install_runtime(cfg, runner, os_release_text, arch, prefix="/"):
    distro, codename = os_identity(os_release_text)
    env = _apt_env()
    _check(cfg, runner, ["apt-get", "update"], env, "could not update the package list")
    _check(
        cfg, runner,
        ["apt-get", "install", "-y", "ca-certificates", "curl", "gnupg", "rsync", "borgbackup"],
        env,
        "could not install Borg and rsync",
    )
    if arch is None:
        return
    keydir = os.path.join(prefix, "etc", "apt", "keyrings")
    key = os.path.join(keydir, "docker.asc")
    _check(cfg, runner, ["install", "-m", "0755", "-d", keydir], message="could not prepare the Docker key")
    _check(
        cfg, runner,
        ["curl", "-fsSL", "https://download.docker.com/linux/%s/gpg" % distro, "-o", key],
        message="could not download the Docker key",
    )
    _check(cfg, runner, ["chmod", "a+r", key], message="could not prepare the Docker key")
    source = os.path.join(prefix, "etc", "apt", "sources.list.d", "docker.list")
    os.makedirs(os.path.dirname(source), exist_ok=True)
    with open(source, "w", encoding="utf-8") as fh:
        fh.write(docker_source_line(distro, codename, arch))
    _check(cfg, runner, ["apt-get", "update"], env, "could not update the package list")
    _check(
        cfg, runner,
        ["apt-get", "install", "-y", *DOCKER_PACKAGES],
        env,
        "could not install Docker",
    )
    _check(cfg, runner, ["systemctl", "enable", "--now", "docker"], message="could not start Docker")


def find_layout(root):
    compose = env = dump = media = None
    env_beside = None
    root = os.path.abspath(root)
    for dirpath, dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth > 6:
            dirs.clear()
            continue
        if "docker-compose.yml" in files and compose is None:
            compose = os.path.join(dirpath, "docker-compose.yml")
        if ".env" in files:
            candidate = os.path.join(dirpath, ".env")
            if compose and os.path.dirname(compose) == dirpath:
                env_beside = candidate
            elif env is None:
                env = candidate
        if "immich-database.sql" in files and dump is None:
            dump = os.path.join(dirpath, "immich-database.sql")
        if media is None and ({"upload", "encoded-video"} & set(dirs)):
            media = dirpath
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
    return Layout(compose, env_beside or env, dump, media)


def rewrite_upload(text, mount):
    lines = []
    found = False
    for line in text.splitlines():
        if line.startswith("UPLOAD_LOCATION="):
            lines.append("UPLOAD_LOCATION=" + mount)
            found = True
        else:
            lines.append(line)
    if not found:
        lines.append("UPLOAD_LOCATION=" + mount)
    return "\n".join(lines) + "\n"


def transform_line(line, database):
    if "set_config" in line and "search_path" in line and "false" in line:
        return "SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);\n"
    if line.startswith("\\connect "):
        return "\\connect " + database + "\n"
    if line.endswith("\n"):
        return line
    return line + "\n"


def place_files(cfg, layout):
    os.makedirs(os.path.dirname(cfg.compose), exist_ok=True)
    os.makedirs(os.path.dirname(cfg.env_file), exist_ok=True)
    os.makedirs(os.path.dirname(cfg.dump), exist_ok=True)
    os.makedirs(cfg.media_mount, exist_ok=True)
    shutil.copy2(layout.compose, cfg.compose)
    with open(layout.env, encoding="utf-8") as fh:
        rewritten = rewrite_upload(fh.read(), cfg.media_mount)
    with open(cfg.env_file, "w", encoding="utf-8") as fh:
        fh.write(rewritten)
    os.chmod(cfg.env_file, 0o600)
    shutil.copy2(layout.dump, cfg.dump)


def compose_command(cfg, *args):
    return [
        "docker", "compose",
        "--project-directory", os.path.dirname(cfg.compose),
        "-f", cfg.compose,
        *args,
    ]


def wait_database(cfg, runner):
    for _ in range(30):
        proc = runner.run([
            "docker", "exec", cfg.postgres_container,
            "pg_isready", "-U", cfg.database_user, "-d", cfg.database,
        ])
        if proc.returncode == 0:
            return
        time.sleep(2)
    fail(cfg, "database did not become ready")


def load_database(cfg, runner):
    def chunks():
        with open(cfg.dump, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                yield transform_line(line, cfg.database).encode("utf-8")

    return runner.run_input([
        "docker", "exec", "-i", cfg.postgres_container,
        "psql", "-U", cfg.database_user, "-d", cfg.database,
        "--single-transaction", "--set", "ON_ERROR_STOP=on",
    ], chunks())


def list_archives(repo, runner=None):
    if not os.path.isfile(os.path.join(repo, "config")):
        raise ValueError("That drive has no Borg repository")
    runner = runner or ProcessRunner()
    env = os.environ.copy()
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    env["BORG_REPO"] = repo
    proc = runner.run(["borg", "list", "--json"], env=env)
    if proc.returncode != 0:
        raise ValueError("Could not read the backup archives")
    try:
        data = json.loads((proc.stdout or b"{}").decode("utf-8") if isinstance(proc.stdout, bytes) else proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("Could not read the backup archives") from exc
    rows = []
    for item in data.get("archives") or []:
        name = str(item.get("name") or "")
        if ARCHIVE_RE.match(name):
            rows.append({"name": name, "time": str(item.get("start") or item.get("time") or "")})
    rows.reverse()
    if not rows:
        raise ValueError("That repository has no Immich archives")
    return rows


def confirm_mounted(cfg):
    check_backup_disk(cfg)
    check_media(cfg)


def run(cfg, runner=None, os_release_path="/etc/os-release", prefix="/"):
    runner = runner or ProcessRunner()
    archive = cfg.restore_archive or ""
    if not ARCHIVE_RE.match(archive):
        fail(cfg, "restore archive was not chosen")
    try:
        assert_safe(cfg)
    except ValueError as exc:
        fail(cfg, str(exc))
    os.makedirs(os.path.dirname(cfg.lock), exist_ok=True)
    lock = open(cfg.lock, "a", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        from immich_backup.backup import notify
        notify(cfg, "FAILED Immich restore is already running")
        return 1
    mount_dir = os.path.join(cfg.backup_mount, "restore-mount")
    mounted = False
    try:
        log(cfg, "starting restore of " + archive)
        confirm_mounted(cfg)
        missing = missing_tools(runner)
        if missing:
            try:
                with open(os_release_path, encoding="utf-8") as fh:
                    text = fh.read()
                arch = None
                if "docker" in missing:
                    probed = runner.run(["dpkg", "--print-architecture"])
                    raw = probed.stdout or b""
                    arch = raw.decode("utf-8").strip() if isinstance(raw, bytes) else raw.strip()
                    if probed.returncode != 0 or not arch:
                        fail(cfg, "could not detect the system architecture")
                install_runtime(cfg, runner, text, arch, prefix)
            except ValueError as exc:
                fail(cfg, str(exc))
        else:
            log(cfg, "Docker, Borg, and rsync are already installed")
        existed = os.path.isfile(cfg.compose)
        os.makedirs(mount_dir, exist_ok=True)
        mounted_proc = runner.run(["borg", "mount", "::" + archive, mount_dir], env=borg_env(cfg))
        if mounted_proc.returncode != 0:
            fail(cfg, "could not open the archive")
        mounted = True
        layout = find_layout(mount_dir)
        missing_files = layout.missing()
        if missing_files:
            fail(cfg, "archive is missing " + ", ".join(missing_files))
        place_files(cfg, layout)
        log(cfg, "restored Immich configuration")
        copied = runner.run(["rsync", "-aH", "--numeric-ids", layout.media + "/", cfg.media_mount + "/"])
        if copied.returncode != 0:
            fail(cfg, "could not copy the photo library")
        log(cfg, "restored photo library")
        if existed:
            runner.run(compose_command(cfg, "stop"))
        for args in (
            compose_command(cfg, "pull"),
            compose_command(cfg, "create"),
            compose_command(cfg, "start", "database"),
        ):
            if runner.run(args).returncode != 0:
                fail(cfg, "docker compose failed")
        wait_database(cfg, runner)
        loaded = load_database(cfg, runner)
        if loaded.returncode != 0:
            fail(cfg, "database restore failed")
        log(cfg, "database restore exit 0")
        if runner.run(compose_command(cfg, "up", "-d")).returncode != 0:
            fail(cfg, "Immich did not start")
        log(cfg, "restore finished")
        return 0
    finally:
        if mounted:
            runner.run(["borg", "umount", mount_dir])
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock.close()
