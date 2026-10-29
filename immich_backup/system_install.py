"""Copy this program onto the machine and install the packages it needs.

The status page then asks for the account, the drives, and the mail address.
Disks are not partitioned or formatted.
"""

import argparse
import os
import shutil
import subprocess
import sys

from immich_backup.config import load
from immich_backup.install import write_units
from immich_backup.restore import DOCKER_PACKAGES, docker_source_line, os_identity


MACHINE_CONFIG = """\
[paths]
state_dir = /var/lib/immich-backup
log = /var/log/immich-backup.log
lock = /var/lock/immich-backup.lock
dump = /var/backups/immich/immich-database.sql
compose = /opt/immich/docker-compose.yml
env_file = /opt/immich/.env

[backup]
keep_daily = 7
prune = true
initial_free_gb = 0
stop_free_gb = 10
warn_gb = 50,20,10

[schedule]
backup = *-*-* 01:00:00
check = Sun *-*-* 10:00:00
label = 01:00

[status]
host = 0.0.0.0
port = 8090

[notify]
command =
email =

[install]
app_dir = /opt/immich-backup
roles = backup, status, check
"""

HOST_CONFIG = """\
[paths]
state_dir = /var/lib/immich-backup
log = /var/log/immich-backup.log

[health]
spindown_idle_seconds = 600
spindown_enabled = true

[notify]
command =
email =

[install]
app_dir = /opt/immich-backup
roles = health, publish, spindown, notify
"""

BASE_PACKAGES = (
    "ca-certificates",
    "curl",
    "gnupg",
    "python3",
    "rsync",
    "borgbackup",
    "smartmontools",
)
HOST_PACKAGES = ("hdparm", "smartmontools")


class ProcessRunner:
    def run(self, args, env=None):
        try:
            return subprocess.run(args, env=env, capture_output=True)
        except FileNotFoundError:
            return subprocess.CompletedProcess(args, 127, b"", b"")


def rooted(prefix, path):
    if prefix in {"", "/"}:
        return path
    return os.path.join(prefix, path.lstrip("/"))


def _apt_env():
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    return env


def _run(runner, args, env=None, message="command failed"):
    proc = runner.run(args, env=env)
    if proc.returncode != 0:
        raise ValueError(message)
    return proc


def supported_os(text):
    try:
        return os_identity(text)
    except ValueError:
        raise ValueError("This installer uses apt. Debian and Ubuntu are supported.") from None


def copy_package(source, app_dir):
    package = os.path.join(source, "immich_backup")
    dest = os.path.join(app_dir, "immich_backup")
    os.makedirs(dest, exist_ok=True)
    names = [name for name in sorted(os.listdir(package)) if name.endswith(".py")]
    if not names:
        raise ValueError("could not find the immich_backup package")
    for name in names:
        shutil.copy2(os.path.join(package, name), os.path.join(dest, name))
    for name in ("install.py", "README.md"):
        path = os.path.join(source, name)
        if os.path.isfile(path):
            shutil.copy2(path, os.path.join(app_dir, name))
    return names


def install_packages(runner, distro, codename, arch, prefix, role):
    env = _apt_env()
    _run(runner, ["apt-get", "update"], env, "could not update the package list")
    _run(
        runner,
        ["apt-get", "install", "-y", *BASE_PACKAGES],
        env,
        "could not install Borg and rsync",
    )
    if role == "machine":
        runner.run(["systemctl", "disable", "--now", "smartd"])
    if role == "host":
        _run(
            runner,
            ["apt-get", "install", "-y", *HOST_PACKAGES],
            env,
            "could not install the disk tools",
        )
        return
    if runner.run(["docker", "compose", "version"]).returncode == 0:
        return
    keydir = rooted(prefix, "/etc/apt/keyrings")
    key = os.path.join(keydir, "docker.asc")
    _run(runner, ["install", "-m", "0755", "-d", keydir], message="could not prepare the Docker key")
    _run(
        runner,
        ["curl", "-fsSL", "https://download.docker.com/linux/%s/gpg" % distro, "-o", key],
        message="could not download the Docker key",
    )
    _run(runner, ["chmod", "a+r", key], message="could not prepare the Docker key")
    source = rooted(prefix, "/etc/apt/sources.list.d/docker.list")
    os.makedirs(os.path.dirname(source), exist_ok=True)
    with open(source, "w", encoding="utf-8") as fh:
        fh.write(docker_source_line(distro, codename, arch))
    _run(runner, ["apt-get", "update"], env, "could not update the package list")
    _run(
        runner,
        ["apt-get", "install", "-y", *DOCKER_PACKAGES],
        env,
        "could not install Docker",
    )


def write_config(path, role):
    if os.path.exists(path):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(MACHINE_CONFIG if role == "machine" else HOST_CONFIG)
    os.chmod(path, 0o600)
    return True


def units_for(cfg):
    names = []
    if "status" in cfg.roles:
        names.append("immich-backup-status.service")
    if "backup" in cfg.roles:
        names.append("immich-backup.timer")
    if "check" in cfg.roles:
        names.append("immich-backup-check.timer")
    if "spindown" in cfg.roles:
        names.append("immich-backup-spindown.service")
    if "publish" in cfg.roles:
        names.append("immich-backup-publish.timer")
    return names


def enable_units(runner, names):
    _run(runner, ["systemctl", "daemon-reload"], message="could not reload systemd")
    for name in names:
        _run(runner, ["systemctl", "enable", "--now", name], message="could not enable " + name)


def apply(source, prefix="/", role="machine", runner=None, os_release_text=None):
    if role not in {"machine", "host"}:
        raise ValueError("unknown install role")
    if os_release_text is None:
        release = rooted(prefix, "/etc/os-release")
        if not os.path.isfile(release):
            release = "/etc/os-release"
        with open(release, encoding="utf-8") as fh:
            os_release_text = fh.read()
    distro, codename = supported_os(os_release_text)
    runner = runner or ProcessRunner()
    arch = "amd64"
    if role == "machine":
        probed = runner.run(["dpkg", "--print-architecture"])
        raw = probed.stdout or b""
        arch = raw.decode("utf-8").strip() if isinstance(raw, bytes) else str(raw).strip()
        if probed.returncode != 0 or not arch:
            raise ValueError("could not detect the system architecture")
    install_packages(runner, distro, codename, arch, prefix, role)
    app_dir = rooted(prefix, "/opt/immich-backup")
    config_path = rooted(prefix, "/etc/immich-backup/config.ini")
    for path in (
        "/opt/immich-backup",
        "/etc/immich-backup",
        "/var/lib/immich-backup",
        "/var/backups/immich",
        "/var/log",
        "/etc/systemd/system",
    ):
        os.makedirs(rooted(prefix, path), exist_ok=True)
    copy_package(source, app_dir)
    created = write_config(config_path, role)
    cfg = load(config_path)
    write_units(cfg, rooted(prefix, "/etc/systemd/system"))
    enable_units(runner, units_for(cfg))
    return {"role": role, "config_created": created, "port": cfg.status_port}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install Immich backup on this machine.")
    parser.add_argument(
        "--host",
        action="store_true",
        help="install disk health and hard-drive sleep on the machine that holds the backup disk",
    )
    args = parser.parse_args(argv)
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        print("Run this with sudo.", file=sys.stderr)
        return 1
    source = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        result = apply(source, role="host" if args.host else "machine")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.host:
        print("Installed the disk services.")
        print("Set the disk paths in /etc/immich-backup/config.ini.")
        return 0
    print("Installed.")
    print("Open http://<this-machine>:%s and create the account." % result["port"])
    return 0
