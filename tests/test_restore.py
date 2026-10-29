"""Restore uses stand-in commands and temporary directories.

Docker, Borg, apt, and the machine's disks are never called. The temporary
directory is removed when the test ends.
"""

import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch

from immich_backup.config import load
from immich_backup.restore import (
    ARCHIVE_RE,
    assert_safe,
    find_layout,
    list_archives,
    os_identity,
    run,
)
from immich_backup.status import restore_in_progress, failure_text


SECRET = "SECRET_DB_PASSWORD"
ARCHIVE = "immich-2026-10-27_020011"


def write_config(directory):
    path = os.path.join(directory, "config.ini")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "[paths]\n"
            "state_dir = " + os.path.join(directory, "state") + "\n"
            "lock = " + os.path.join(directory, "backup.lock") + "\n"
            "log = " + os.path.join(directory, "backup.log") + "\n"
            "media_mount = " + os.path.join(directory, "photos") + "\n"
            "backup_mount = " + os.path.join(directory, "backup") + "\n"
            "repo = " + os.path.join(directory, "backup", "immich-borg") + "\n"
            "dump = " + os.path.join(directory, "immich", "dump.sql") + "\n"
            "compose = " + os.path.join(directory, "immich", "docker-compose.yml") + "\n"
            "env_file = " + os.path.join(directory, "immich", ".env") + "\n"
            "[identity]\n[backup]\nkeep_daily = 7\nwarn_gb = 50,20,10\n"
            "[schedule]\nlabel = 01:00\nbackup = *-*-* 01:00:00\n"
            "[notify]\ncommand =\nemail =\n"
            "[restore]\narchive = " + ARCHIVE + "\n"
            "[install]\nroles = backup, status\n"
        )
    return path


class Completed:
    def __init__(self, code, out=""):
        self.returncode = code
        self.stdout = out.encode("utf-8")
        self.stderr = b""


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.sql = b""
        self.missing = True

    def run(self, args, env=None, input=None):
        self.calls.append(list(args))
        if args in (["borg", "--version"], ["rsync", "--version"], ["docker", "compose", "version"]):
            return Completed(1 if self.missing else 0)
        if args[:1] == ["dpkg"]:
            return Completed(0, "amd64\n")
        if args[:2] == ["borg", "mount"]:
            self._archive(args[-1])
            return Completed(0)
        if args[:1] == ["rsync"]:
            self._copy(args[-2], args[-1])
            return Completed(0)
        return Completed(0)

    def run_input(self, args, chunks, env=None):
        self.calls.append(list(args))
        self.sql = b"".join(chunks)
        return Completed(0)

    def _archive(self, dest):
        immich = os.path.join(dest, "opt", "immich")
        os.makedirs(immich, exist_ok=True)
        with open(os.path.join(immich, "docker-compose.yml"), "w", encoding="utf-8") as fh:
            fh.write("name: immich\nservices: {}\n")
        with open(os.path.join(immich, ".env"), "w", encoding="utf-8") as fh:
            fh.write("UPLOAD_LOCATION=/old/photos\nDB_PASSWORD=" + SECRET + "\n")
        dump = os.path.join(dest, "var", "backups", "immich")
        os.makedirs(dump, exist_ok=True)
        with open(os.path.join(dump, "immich-database.sql"), "w", encoding="utf-8") as fh:
            fh.write("SELECT pg_catalog.set_config('search_path', '', false);\n\\connect immich\nSELECT 1;\n")
        photo = os.path.join(dest, "mnt", "immich-media", "upload")
        os.makedirs(photo, exist_ok=True)
        with open(os.path.join(photo, "photo.txt"), "w", encoding="utf-8") as fh:
            fh.write("photo")

    def _copy(self, src, dest):
        os.makedirs(dest, exist_ok=True)
        for name in os.listdir(src):
            source = os.path.join(src, name)
            target = os.path.join(dest, name)
            if os.path.isdir(source):
                import shutil
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                import shutil
                shutil.copy2(source, target)


class RestoreTests(unittest.TestCase):
    def test_archive_names_are_limited_to_immich_copies(self):
        self.assertTrue(ARCHIVE_RE.match(ARCHIVE))
        self.assertFalse(ARCHIVE_RE.match("../etc/passwd"))
        self.assertFalse(ARCHIVE_RE.match("immich-2026-10-27_020011;id"))

    def test_other_systems_are_refused_before_install(self):
        with self.assertRaises(ValueError):
            os_identity('ID="darwin"\nVERSION_CODENAME="mac"\n')

    def test_root_and_the_same_drive_are_refused(self):
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            cfg = load(write_config(tmp))
            cfg.media_mount = "/"
            with self.assertRaises(ValueError):
                assert_safe(cfg)
            cfg.media_mount = cfg.backup_mount
            with self.assertRaises(ValueError):
                assert_safe(cfg)

    def test_restore_installs_docker_and_immich_inside_the_temporary_directory(self):
        real_list = "/etc/apt/sources.list.d/docker.list"
        existed = os.path.exists(real_list)
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            cfg = load(write_config(tmp))
            os.makedirs(os.path.dirname(cfg.compose), exist_ok=True)
            with open(cfg.compose, "w", encoding="utf-8") as fh:
                fh.write("old\n")
            release = os.path.join(tmp, "os-release")
            with open(release, "w", encoding="utf-8") as fh:
                fh.write('ID=debian\nVERSION_CODENAME=trixie\n')
            prefix = os.path.join(tmp, "aptroot")
            runner = FakeRunner()
            with patch("immich_backup.restore.confirm_mounted"):
                self.assertEqual(run(cfg, runner, release, prefix), 0)
            joined = [" ".join(call) for call in runner.calls]
            self.assertIn("apt-get install -y ca-certificates curl gnupg rsync borgbackup", joined)
            self.assertIn(
                "apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin",
                joined,
            )
            self.assertIn("systemctl enable --now docker", joined)
            self.assertTrue(any(call[:2] == ["borg", "mount"] and call[2] == "::" + ARCHIVE for call in runner.calls))
            self.assertTrue(any(call[:2] == ["borg", "umount"] for call in runner.calls))
            self.assertTrue(any(call[:3] == ["docker", "compose", "--project-directory"] and call[-2:] == ["up", "-d"] for call in runner.calls))
            self.assertTrue(any("stop" in call for call in runner.calls if call[:2] == ["docker", "compose"]))
            self.assertFalse(any("down" in call for call in runner.calls if call[:2] == ["docker", "compose"]))
            self.assertFalse(any(part in {"mkfs", "mkfs.ext4", "parted", "wipefs"} for call in runner.calls for part in call))
            source = os.path.join(prefix, "etc", "apt", "sources.list.d", "docker.list")
            with open(source, encoding="utf-8") as fh:
                line = fh.read()
            self.assertIn("https://download.docker.com/linux/debian", line)
            self.assertIn("trixie", line)
            with open(cfg.env_file, encoding="utf-8") as fh:
                env = fh.read()
            self.assertIn("UPLOAD_LOCATION=" + cfg.media_mount, env)
            self.assertIn(SECRET, env)
            self.assertEqual(stat.S_IMODE(os.stat(cfg.env_file).st_mode), 0o600)
            with open(os.path.join(cfg.media_mount, "upload", "photo.txt"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "photo")
            self.assertIn(b"public, pg_catalog", runner.sql)
            self.assertNotIn(b"false", runner.sql)
            with open(cfg.log, encoding="utf-8") as fh:
                logged = fh.read()
            self.assertIn("starting restore of " + ARCHIVE, logged)
            self.assertIn("restore finished", logged)
            self.assertNotIn(SECRET, logged)
            self.assertTrue(cfg.compose.startswith(tmp))
            self.assertTrue(source.startswith(tmp))
        self.assertEqual(os.path.exists(real_list), existed)

    def test_installed_tools_are_not_installed_again(self):
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            cfg = load(write_config(tmp))
            release = os.path.join(tmp, "os-release")
            with open(release, "w", encoding="utf-8") as fh:
                fh.write("ID=debian\nVERSION_CODENAME=trixie\n")
            runner = FakeRunner()
            runner.missing = False
            with patch("immich_backup.restore.confirm_mounted"):
                run(cfg, runner, release, os.path.join(tmp, "aptroot"))
            self.assertFalse(any(call[0] == "apt-get" for call in runner.calls))
            with open(cfg.log, encoding="utf-8") as fh:
                self.assertIn("already installed", fh.read())

    def test_archive_list_keeps_only_immich_names(self):
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            repo = os.path.join(tmp, "immich-borg")
            os.makedirs(repo)
            with open(os.path.join(repo, "config"), "w", encoding="utf-8") as fh:
                fh.write("repo\n")

            class Listing:
                def run(self, args, env=None, input=None):
                    body = json.dumps({"archives": [
                        {"name": "immich-2026-10-26_010000", "start": "2026-10-26T01:00:00"},
                        {"name": ARCHIVE, "start": "2026-10-27T02:00:11"},
                        {"name": "../etc/passwd", "start": ""},
                    ]})
                    return Completed(0, body)

            rows = list_archives(repo, Listing())
            self.assertEqual([row["name"] for row in rows], [ARCHIVE, "immich-2026-10-26_010000"])
            with self.assertRaises(ValueError):
                list_archives(os.path.join(tmp, "missing"))

    def test_layout_finds_immich_without_walking_the_library(self):
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            runner = FakeRunner()
            runner._archive(tmp)
            layout = find_layout(tmp)
            self.assertEqual(layout.missing(), [])
            self.assertTrue(layout.media.endswith("immich-media"))

    def test_restore_progress_follows_the_log(self):
        lines = ["2026-10-29T01:00:00+05:30 starting restore of " + ARCHIVE]
        self.assertTrue(restore_in_progress(lines))
        lines.append("2026-10-29T02:00:00+05:30 restore finished")
        self.assertFalse(restore_in_progress(lines))
        lines.append("2026-10-29T03:00:00+05:30 starting restore of " + ARCHIVE)
        lines.append("2026-10-29T03:01:00+05:30 FAILED database restore failed")
        self.assertFalse(restore_in_progress(lines))

    def test_failure_reason_names_the_job(self):
        restore_lines = [
            "2026-10-29T01:00:00+05:30 starting restore of " + ARCHIVE,
            "2026-10-29T01:05:00+05:30 FAILED could not install Docker",
        ]
        self.assertEqual(failure_text(restore_lines), ("restore", "could not install Docker"))
        backup_lines = [
            "2026-10-29T01:00:00+05:30 starting local borg create",
            "2026-10-29T01:01:00+05:30 FAILED borg create failed with exit 2",
        ]
        self.assertEqual(failure_text(backup_lines), ("backup", "borg create failed with exit 2"))


if __name__ == "__main__":
    unittest.main()
