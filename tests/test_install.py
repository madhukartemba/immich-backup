"""The installer uses stand-in commands and a temporary directory.

apt, Docker, and systemd are never called. The temporary directory is removed
when the test ends.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from immich_backup.system_install import ProcessRunner, apply, main


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEBIAN = "ID=debian\nVERSION_CODENAME=trixie\n"


class FakeRunner:
    def __init__(self, docker=False):
        self.docker = docker
        self.commands = []

    def run(self, args, env=None):
        self.commands.append((args, env))
        if args[:3] == ["docker", "compose", "version"]:
            return subprocess.CompletedProcess(args, 0 if self.docker else 1, b"", b"")
        if args == ["dpkg", "--print-architecture"]:
            return subprocess.CompletedProcess(args, 0, b"amd64\n", b"")
        return subprocess.CompletedProcess(args, 0, b"", b"")


class InstallerTests(unittest.TestCase):
    def test_a_missing_command_means_it_is_not_installed(self):
        result = ProcessRunner().run(["immich-backup-missing-command"])
        self.assertEqual(result.returncode, 127)

    def test_machine_install_copies_the_program_and_docker_when_needed(self):
        runner = FakeRunner(docker=False)
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            result = apply(ROOT, prefix=tmp, role="machine", runner=runner, os_release_text=DEBIAN)
            self.assertTrue(result["config_created"])
            package = os.path.join(tmp, "opt", "immich-backup", "immich_backup", "backup.py")
            self.assertTrue(os.path.isfile(package))
            config = os.path.join(tmp, "etc", "immich-backup", "config.ini")
            self.assertEqual(stat.S_IMODE(os.stat(config).st_mode), 0o600)
            text = Path(config).read_text(encoding="utf-8")
            self.assertIn("roles = backup, status, check", text)
            self.assertNotIn("@", text)
            unit = os.path.join(tmp, "etc", "systemd", "system", "immich-backup-status.service")
            self.assertIn("python3 -m immich_backup serve", Path(unit).read_text(encoding="utf-8"))
            source = os.path.join(tmp, "etc", "apt", "sources.list.d", "docker.list")
            self.assertIn("linux/debian trixie", Path(source).read_text(encoding="utf-8"))
        flat = [args for args, _env in runner.commands]
        self.assertIn(["apt-get", "install", "-y", "ca-certificates", "curl", "gnupg", "python3", "rsync", "borgbackup", "hdparm", "smartmontools"], flat)
        self.assertIn(["systemctl", "disable", "--now", "smartd"], flat)
        self.assertTrue(any("docker-ce" in args for args in flat))
        self.assertIn(["systemctl", "enable", "--now", "immich-backup-status.service"], flat)
        self.assertIn(["systemctl", "enable", "--now", "immich-backup.timer"], flat)

    def test_existing_config_and_docker_are_left_in_place(self):
        runner = FakeRunner(docker=True)
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            config = os.path.join(tmp, "etc", "immich-backup", "config.ini")
            os.makedirs(os.path.dirname(config))
            with open(config, "w", encoding="utf-8") as fh:
                fh.write("[install]\nroles = backup, status\napp_dir = /opt/immich-backup\n")
            result = apply(ROOT, prefix=tmp, role="machine", runner=runner, os_release_text=DEBIAN)
            self.assertFalse(result["config_created"])
            self.assertIn("roles = backup, status", Path(config).read_text(encoding="utf-8"))
            self.assertFalse(os.path.exists(os.path.join(tmp, "etc", "apt", "sources.list.d", "docker.list")))
        flat = [args for args, _env in runner.commands]
        self.assertFalse(any(arg == "docker-ce" for args in flat for arg in args))

    def test_host_install_skips_docker(self):
        runner = FakeRunner()
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            apply(ROOT, prefix=tmp, role="host", runner=runner, os_release_text=DEBIAN)
            text = Path(os.path.join(tmp, "etc", "immich-backup", "config.ini")).read_text(encoding="utf-8")
            self.assertIn("spindown", text)
        flat = [args for args, _env in runner.commands]
        self.assertIn(["apt-get", "install", "-y", "hdparm", "smartmontools"], flat)
        self.assertFalse(any("docker-ce" in args for args in flat))
        self.assertIn(["systemctl", "enable", "--now", "immich-backup-spindown.service"], flat)

    def test_other_systems_are_refused(self):
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            with self.assertRaises(ValueError):
                apply(ROOT, prefix=tmp, runner=FakeRunner(), os_release_text="ID=fedora\nVERSION_CODENAME=rawhide\n")

    def test_main_asks_for_root(self):
        with patch("immich_backup.system_install.os.geteuid", return_value=1000):
            self.assertEqual(main([]), 1)


if __name__ == "__main__":
    unittest.main()
