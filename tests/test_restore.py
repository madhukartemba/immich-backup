"""Restore uses stand-in commands and temporary directories.

Docker, Borg, apt, and the machine's disks are never called. The temporary
directory is removed when the test ends.
"""

import io
import itertools
import json
import os
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from immich_backup.config import load
from immich_backup.restore import (
    ARCHIVE_RE,
    ProcessRunner,
    assert_safe,
    find_layout,
    last_error,
    list_archives,
    os_identity,
    run,
)
from immich_backup.archives import export_tar, gnu_tar_size, tarinfo_for_item


def _written_gnu_tar(items):
    """The bytes Python's streaming GNU tar writes for the same item records Borg uses."""
    buf = io.BytesIO()
    tar = tarfile.open(fileobj=buf, mode="w|", format=tarfile.GNU_FORMAT)
    for item in items:
        info = tarinfo_for_item(item)
        if not info:
            continue
        if info.isreg():
            tar.addfile(info, io.BytesIO(b"\0" * info.size))
        else:
            tar.addfile(info)
    tar.close()
    return len(buf.getvalue())
from immich_backup.status import (
    active_job,
    configure,
    failed_kind,
    failure_headline,
    failure_text,
    latest_outcome,
    restore_in_progress,
    restore_step,
)


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
            with patch("immich_backup.restore.confirm_mounted"), \
                    patch("immich_backup.restore.immich_answers", return_value=True) as answered, \
                    patch("immich_backup.restore.refresh_archives"):
                self.assertEqual(run(cfg, runner, release, prefix), 0)
            answered.assert_called()
            up = [index for index, call in enumerate(runner.calls) if call[-2:] == ["up", "-d"]]
            loaded = [index for index, call in enumerate(runner.calls) if "psql" in call]
            self.assertTrue(up and loaded and loaded[0] < up[0])
            mounts = [call for call in runner.calls if call[:2] == ["borg", "mount"]]
            self.assertTrue(mounts[0][-1].startswith(cfg.state_dir))
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
            with patch("immich_backup.restore.confirm_mounted"), \
                    patch("immich_backup.restore.immich_answers", return_value=True), \
                    patch("immich_backup.restore.refresh_archives"):
                run(cfg, runner, release, os.path.join(tmp, "aptroot"))
            self.assertFalse(any(call[0] == "apt-get" for call in runner.calls))
            with open(cfg.log, encoding="utf-8") as fh:
                self.assertIn("already installed", fh.read())

    def test_an_unexpected_error_is_logged_as_a_failed_restore(self):
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            cfg = load(write_config(tmp))
            runner = FakeRunner()
            runner.missing = False

            def broken(*_args, **_kwargs):
                raise ValueError("flush of closed file")

            runner.run_input = broken
            with patch("immich_backup.restore.confirm_mounted"), \
                    patch("immich_backup.restore.wait_database"), \
                    patch("immich_backup.restore.immich_answers", return_value=True):
                with self.assertRaises(SystemExit):
                    run(cfg, runner, os.path.join(tmp, "missing"), os.path.join(tmp, "aptroot"))
            with open(cfg.log, encoding="utf-8") as fh:
                logged = fh.read()
            self.assertIn("FAILED restore stopped unexpectedly: ValueError flush of closed file", logged)
            self.assertNotIn("restore finished", logged)
            self.assertTrue(any(call[:2] == ["borg", "umount"] for call in runner.calls))
            self.assertFalse(restore_in_progress(logged.splitlines()))

    def test_immich_must_answer_before_the_restore_is_finished(self):
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            cfg = load(write_config(tmp))
            runner = FakeRunner()
            runner.missing = False
            with patch("immich_backup.restore.confirm_mounted"), \
                    patch("immich_backup.restore.wait_database"), \
                    patch("immich_backup.restore.immich_answers", return_value=False), \
                    patch("immich_backup.restore.time.sleep"), \
                    patch("immich_backup.restore.time.monotonic", side_effect=itertools.count(0, 500)):
                with self.assertRaises(SystemExit):
                    run(cfg, runner, os.path.join(tmp, "missing"), os.path.join(tmp, "aptroot"))
            with open(cfg.log, encoding="utf-8") as fh:
                logged = fh.read()
            self.assertIn("FAILED Immich started but did not answer", logged)
            self.assertNotIn("restore finished", logged)

    def test_the_database_loader_feeds_every_line_and_survives_an_early_exit(self):
        counted = ProcessRunner().run_input(
            [sys.executable, "-c", "import sys; print(len(sys.stdin.buffer.read()))"],
            (b"x" * 1000 for _ in range(500)),
        )
        self.assertEqual(counted.returncode, 0)
        self.assertEqual(counted.stdout.strip(), b"500000")
        quit_early = ProcessRunner().run_input(
            [sys.executable, "-c", "import sys; sys.stderr.write('ERROR: relation exists\\n'); sys.exit(3)"],
            (b"x" * 65536 for _ in range(200)),
        )
        self.assertEqual(quit_early.returncode, 3)
        self.assertEqual(last_error(quit_early.stderr), "ERROR: relation exists")

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

    def test_restore_steps_move_the_progress_bar(self):
        lines = ["2026-10-29T01:00:00+00:00 starting restore of " + ARCHIVE]
        self.assertEqual(restore_step(lines)[0], 2)
        self.assertEqual(restore_step(lines)[2], ARCHIVE)
        lines.append("2026-10-29T01:01:00+00:00 restored Immich configuration")
        self.assertIn("photo library", restore_step(lines)[1])
        lines.append("2026-10-29T01:02:00+00:00 restore step loading the database")
        self.assertEqual(restore_step(lines)[0], 80)
        lines.append("2026-10-29T01:03:00+00:00 restore step waiting for Immich")
        self.assertEqual(restore_step(lines)[0], 95)
        lines.append("2026-10-29T01:04:00+00:00 starting local borg create")
        self.assertFalse(restore_in_progress(lines))

    def test_a_running_restore_is_seen_from_its_process(self):
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            configure(load(write_config(tmp)))
            lines = ["2026-10-29T01:00:00+00:00 starting restore of " + ARCHIVE]
            running = "/usr/bin/python3 -m immich_backup restore --config /etc/immich-backup/config.ini\n"
            self.assertEqual(active_job(running, lines), "restore")
            self.assertEqual(active_job("", lines), "")
            self.assertEqual(active_job("borg create ::x /mnt\n", []), "backup")

    def test_a_backup_failure_after_a_restore_is_not_a_restore_failure(self):
        finished = [
            "2026-10-29T09:39:24+00:00 starting restore of " + ARCHIVE,
            "2026-10-29T09:45:00+00:00 restore finished, Immich is running",
        ]
        space = finished + ["2026-10-29T09:50:00+00:00 FAILED less than 10 GB free on the backup disk"]
        self.assertEqual(failure_text(space), ("backup", "less than 10 GB free on the backup disk"))
        self.assertEqual(failure_headline("backup"), "Backup failed")
        self.assertEqual(latest_outcome(space), ("backup", "failed"))
        dump = finished + ["2026-10-29T09:50:00+00:00 FAILED pg_dump failed"]
        self.assertEqual(failure_text(dump)[0], "backup")
        self.assertEqual(failed_kind("restore", "could not open the archive"), "restore")
        opened = [
            "2026-10-29T09:39:24+00:00 starting restore of " + ARCHIVE,
            "2026-10-29T09:40:00+00:00 FAILED could not open the archive",
        ]
        self.assertEqual(failure_text(opened), ("restore", "could not open the archive"))
        self.assertEqual(latest_outcome(opened), ("restore", "failed"))

    def test_export_names_one_archive_and_does_not_use_a_shell(self):
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            cfg = load(write_config(tmp))
            with patch("immich_backup.archives.subprocess.Popen") as popen:
                export_tar(cfg, ARCHIVE)
            args, kwargs = popen.call_args
            self.assertEqual(args[0][:2], ["borg", "export-tar"])
            self.assertTrue(args[0][2].endswith("::" + ARCHIVE))
            self.assertEqual(args[0][3], "-")
            self.assertNotIn("shell", kwargs)
            self.assertEqual(popen.call_count, 1)
            with self.assertRaises(ValueError):
                export_tar(cfg, "../etc/passwd")
            self.assertEqual(popen.call_count, 1)

    def test_gnu_tar_size_matches_borg_export_layout(self):
        long_name = "library/albums/" + ("vacation-" * 12) + "photo.jpg"
        items = [
            {"path": "library", "mode": "drwxr-xr-x", "size": 4096, "user": "immich", "group": "immich", "uid": 1000, "gid": 1000},
            {"path": "library/photo.jpg", "mode": "-rw-r--r--", "size": 1, "user": "immich", "group": "immich", "uid": 1000, "gid": 1000},
            {"path": "library/empty.jpg", "mode": "-rw-r--r--", "size": 0, "user": "immich", "group": "immich", "uid": 1000, "gid": 1000},
            {"path": "library/block.jpg", "mode": "-rw-r--r--", "size": 512, "user": "immich", "group": "immich", "uid": 1000, "gid": 1000},
            {"path": "library/pad.jpg", "mode": "-rw-r--r--", "size": 513, "user": "immich", "group": "immich", "uid": 1000, "gid": 1000},
            {"path": long_name, "mode": "-rw-r--r--", "size": 3, "user": "immich", "group": "immich", "uid": 1000, "gid": 1000},
            {"path": "library/link.jpg", "mode": "lrwxrwxrwx", "type": "l", "source": "photo.jpg", "size": 9, "user": "immich", "group": "immich", "uid": 1000, "gid": 1000},
            {"path": "library/hard.jpg", "mode": "hrw-r--r--", "type": "-", "source": "library/photo.jpg", "size": 99999, "user": "immich", "group": "immich", "uid": 1000, "gid": 1000},
            {"path": "library/wide-link.jpg", "mode": "lrwxrwxrwx", "type": "l", "source": "photos/" + ("x" * 120), "size": 0, "user": "immich", "group": "immich", "uid": 1000, "gid": 1000},
        ]
        info = tarinfo_for_item(items[1])
        short = len(info.tobuf(tarfile.GNU_FORMAT, tarfile.ENCODING, "surrogateescape"))
        info.mtime = 1_763_000_000
        self.assertEqual(len(info.tobuf(tarfile.GNU_FORMAT, tarfile.ENCODING, "surrogateescape")), short)
        info.size = 5_000_000_000
        self.assertEqual(len(info.tobuf(tarfile.GNU_FORMAT, tarfile.ENCODING, "surrogateescape")), short)
        self.assertEqual(gnu_tar_size(items), _written_gnu_tar(items))
        self.assertEqual(gnu_tar_size([]), _written_gnu_tar([]))
        self.assertIsNone(gnu_tar_size([{"path": "library/photo.jpg", "mode": "-rw-r--r--"}]))

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
