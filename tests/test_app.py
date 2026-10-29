from types import SimpleNamespace
import os
import tempfile
import unittest

from immich_backup.backup import space_note
from immich_backup.config import load
from immich_backup.health import during_backup
from immich_backup.install import write_units
from immich_backup.spindown import should_standby
from immich_backup.status import (
    clock_iso,
    configure,
    last_success,
    latest_outcome,
    launch_state,
    listed_command,
    local_iso,
    space_level,
)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AppTests(unittest.TestCase):
    def test_config_and_units(self):
        cfg = load(os.path.join(ROOT, "config.vm.example.ini"))
        self.assertEqual(cfg.backup_calendar, "*-*-* 01:00:00")
        self.assertEqual(cfg.keep_daily, 7)
        self.assertIn("status", cfg.roles)
        with tempfile.TemporaryDirectory() as tmp:
            write_units(cfg, tmp)
            with open(os.path.join(tmp, "immich-backup.service"), encoding="utf-8") as fh:
                text = fh.read()
        self.assertIn("python3 -m immich_backup backup", text)
        self.assertIn("PYTHONPATH=/opt/immich-backup", text)

    def test_space_note_uses_the_lowest_limit(self):
        cfg = load(os.path.join(ROOT, "config.vm.example.ini"))
        self.assertIn("10 GB", space_note(cfg, 9 * 1024 * 1024))
        self.assertIn("20 GB", space_note(cfg, 15 * 1024 * 1024))
        self.assertEqual(space_note(cfg, 60 * 1024 * 1024), "")

    def test_the_free_space_stop_is_separate_from_the_warnings(self):
        cfg = load(os.path.join(ROOT, "config.vm.example.ini"))
        cfg.stop_free_gb = 2
        cfg.warn_gb = (50, 20, 10)
        configure(cfg)
        self.assertEqual(space_level({"mount_ok": True, "avail": 9 * 1024 ** 3}), ("low", 10))
        self.assertEqual(space_level({"mount_ok": True, "avail": int(1.5 * 1024 ** 3)}), ("critical", 2))
        cfg.stop_free_gb = 0
        configure(cfg)
        self.assertEqual(space_level({"mount_ok": True, "avail": 1024 ** 3}), ("low", 10))

    def test_warning_exit_counts_as_a_finished_backup(self):
        cfg = load(os.path.join(ROOT, "config.vm.example.ini"))
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "repo"))
            open(os.path.join(tmp, "repo", "config"), "w", encoding="utf-8").close()
            os.utime(os.path.join(tmp, "repo", "config"), (1_600_000_000, 1_600_000_000))
            old = cfg.repo
            cfg.repo = os.path.join(tmp, "repo")
            configure(cfg)
            result = last_success([
                "2026-10-27T02:00:11+05:30 starting local borg create",
                "2026-10-27T02:00:41+05:30 borg create exit 1",
            ])
            cfg.repo = old
        self.assertEqual(result["at"], "2026-10-27T02:00:41+05:30")
        self.assertEqual(result["took"], "30 sec")

    def test_a_later_repository_touch_keeps_the_success(self):
        cfg = load(os.path.join(ROOT, "config.vm.example.ini"))
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "repo"))
            config = os.path.join(tmp, "repo", "config")
            open(config, "w", encoding="utf-8").close()
            os.utime(config, (2_000_000_000, 2_000_000_000))
            cfg.repo = os.path.join(tmp, "repo")
            configure(cfg)
            result = last_success([
                "2026-10-29T08:41:58+00:00 starting local borg create",
                "2026-10-29T08:42:04+00:00 borg create exit 0",
                "2026-10-29T08:42:05+00:00 prune exit 0",
            ])
        self.assertEqual(result["at"], "2026-10-29T08:42:04+00:00")
        self.assertEqual(result["took"], "6 sec")
        self.assertEqual(clock_iso("Fri 2026-10-30 01:00:00 UTC"), "2026-10-30T01:00:00+00:00")
        shown = local_iso("Thu, 2026-10-29 08:41:59")
        self.assertTrue(shown.startswith("2026-10-29T08:41:59"))
        self.assertIsNotNone(__import__("datetime").datetime.fromisoformat(shown).tzinfo)
        self.assertTrue(listed_command("/usr/bin/python3 -m immich_backup backup --config x\n", "backup"))
        self.assertFalse(listed_command("/usr/bin/python3 -m immich_backup serve --config x\n", "backup"))
        self.assertEqual(launch_state("backup", 1000, 1005, [], ""), "backup")
        self.assertEqual(
            launch_state("backup", 1000, 1005, ["2026-10-29T08:42:04+00:00 borg create exit 0"], ""),
            "",
        )
        self.assertEqual(latest_outcome([
            "2026-10-29T08:42:04+00:00 borg create exit 0",
            "2026-10-29T08:42:05+00:00 prune exit 0",
        ]), ("backup", "ok"))
        self.assertEqual(latest_outcome([
            "2026-10-29T08:42:05+00:00 prune exit 0",
            "2026-10-29T08:46:00+00:00 repository check passed",
        ]), ("check", "passed"))

    def test_backup_reads_smart_and_leaves_a_sleeping_disk_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            disk = os.path.join(tmp, "backup-disk")
            open(disk, "w", encoding="utf-8").close()
            cfg = SimpleNamespace(
                backup_disk=disk,
                backup_disk_name="Backup disk",
                media_mount="",
                primary_match="",
                primary_name="Photo disk",
                state_dir=tmp,
                smart_file=os.path.join(tmp, "smart.json"),
            )
            skipped = []

            def asleep(dev, skip):
                skipped.append(skip)
                return None

            self.assertEqual(during_backup(cfg, smartctl_fn=asleep, device_fn=lambda mount: ""), [])
            self.assertEqual(skipped, [True])
            self.assertFalse(os.path.exists(cfg.smart_file))
            notes = during_backup(
                cfg,
                smartctl_fn=lambda dev, skip: {"smart_status": {"passed": False}},
                device_fn=lambda mount: "",
            )
            self.assertIn("Backup disk: SMART health check failed", notes)

    def test_hard_drive_sleeps_only_after_it_has_been_idle(self):
        cfg = SimpleNamespace(spindown_enabled=True, spindown_idle_seconds=600)
        self.assertFalse(should_standby(cfg, 100, "active/idle"))
        self.assertTrue(should_standby(cfg, 600, "active/idle"))
        self.assertFalse(should_standby(cfg, 600, "standby"))
        cfg.spindown_enabled = False
        self.assertFalse(should_standby(cfg, 600, "active/idle"))


if __name__ == "__main__":
    unittest.main()
