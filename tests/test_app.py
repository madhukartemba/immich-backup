from types import SimpleNamespace
import json
import os
import tempfile
import unittest

from immich_backup.backup import borg_warning, space_note
from immich_backup.config import load
from immich_backup.health import device_from_source, during_backup
from immich_backup.install import write_units
from immich_backup.spindown import disk_word, drive_kind, reading_from_text, should_standby, spin_label
from immich_backup.status import PAGE, backup_spin
from immich_backup.web import SETUP
from immich_backup.status import (
    clock_iso,
    configure,
    last_success,
    latest_outcome,
    launch_state,
    listed_command,
    local_iso,
    space_level,
    without_stale_space_failures,
)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AppTests(unittest.TestCase):
    def _write_drive(self, tmp, name, vendor, model, rotational):
        root = os.path.join(tmp, "sys")
        os.makedirs(os.path.join(root, "class", "block", name), exist_ok=True)
        device = os.path.join(root, "block", name, "device")
        queue = os.path.join(root, "block", name, "queue")
        os.makedirs(device, exist_ok=True)
        os.makedirs(queue, exist_ok=True)
        for path, text in (
            (os.path.join(device, "vendor"), vendor),
            (os.path.join(device, "model"), model),
            (os.path.join(queue, "rotational"), rotational),
        ):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
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

    def test_raising_the_free_space_stop_clears_that_failure(self):
        cfg = load(os.path.join(ROOT, "config.vm.example.ini"))
        lines = [
            "2026-10-29T09:00:21+00:00 borg create exit 0",
            "2026-10-29T09:11:30+00:00 FAILED less than 10 GB free on the backup disk",
        ]
        cfg.stop_free_gb = 10
        configure(cfg)
        self.assertEqual(latest_outcome(without_stale_space_failures(lines, 9 * 1024 ** 3)), ("backup", "failed"))
        cfg.stop_free_gb = 1
        configure(cfg)
        self.assertEqual(latest_outcome(without_stale_space_failures(lines, 9 * 1024 ** 3)), ("backup", "ok"))

    def test_a_changed_file_is_described_as_a_warning(self):
        text = "file changed while we backed it up: /mnt/immich-media/encoded-video/clip.mp4\n"
        self.assertIn("changed", borg_warning(text))
        many = "\n".join(
            "file changed while we backed it up: /mnt/immich-media/encoded-video/%s.mp4" % n
            for n in range(3)
        )
        self.assertEqual(borg_warning(many), "3 files changed while being copied")

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
        ist = clock_iso("Fri 2026-10-30 01:00:00 IST")
        self.assertTrue(ist.startswith("2026-10-30T01:00:00"))
        self.assertNotIn("IST", ist)
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

    def test_a_filesystem_on_the_whole_disk_is_still_checked(self):
        self.assertEqual(device_from_source("/dev/sdc1", "part", "sdc"), "/dev/sdc")
        self.assertEqual(device_from_source("/dev/sdb", "disk", ""), "/dev/sdb")
        self.assertEqual(device_from_source("", "", ""), "")
        with tempfile.TemporaryDirectory() as tmp:
            photo = os.path.join(tmp, "photo-disk")
            open(photo, "w", encoding="utf-8").close()
            cfg = SimpleNamespace(
                backup_disk="",
                backup_disk_name="Backup disk",
                backup_mount="",
                media_mount=os.path.join(tmp, "photos"),
                primary_match="",
                primary_name="Photo disk",
                state_dir=tmp,
                smart_file=os.path.join(tmp, "smart.json"),
            )
            during_backup(
                cfg,
                smartctl_fn=lambda dev, skip: {"smart_status": {"passed": True}},
                device_fn=lambda mount: photo if mount == cfg.media_mount else "",
            )
            with open(cfg.smart_file, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["primary"]["summary"], "Healthy")
            self.assertTrue(saved["primary"]["ok"])
            os.remove(cfg.smart_file)
            during_backup(
                cfg,
                smartctl_fn=lambda dev, skip: {"smart_support": {"available": False}},
                device_fn=lambda mount: "",
            )
            with open(cfg.smart_file, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["primary"]["summary"], "SMART is not available for this disk")

    def test_spin_state_uses_only_the_power_check(self):
        self.assertEqual(spin_label("standby"), "Sleeping")
        self.assertEqual(spin_label("sleeping"), "Sleeping")
        self.assertEqual(spin_label("active/idle"), "Spinning")
        self.assertEqual(spin_label("unknown"), "")
        self.assertEqual(disk_word("hdd", "standby", False), "HDD")
        self.assertEqual(disk_word("hdd", "active/idle", True), "Spinning")
        self.assertEqual(disk_word("ssd"), "SSD")
        self.assertNotIn("confirm(", PAGE)
        self.assertNotIn("confirm(", SETUP)
        self.assertIn('id="spin"', PAGE)
        self.assertIn('id="modal"', PAGE)
        self.assertIn('id="modal"', SETUP)
        with tempfile.TemporaryDirectory() as tmp:
            disk = os.path.join(tmp, "disk")
            open(disk, "w", encoding="utf-8").close()
            seen = []

            def power(dev):
                seen.append(dev)
                return "standby"

            cfg = SimpleNamespace(backup_rotational=True, backup_disk=disk, backup_mount="/mnt/nowhere")
            self.assertEqual(backup_spin(cfg, power, lambda dev: "ssd"), "SSD")
            self.assertEqual(backup_spin(cfg, power, lambda dev: "virtual"), "Virtual")
            self.assertEqual(seen, [])
            self.assertEqual(backup_spin(cfg, power, lambda dev: "hdd"), "Sleeping")
            self.assertEqual(seen, [disk])
            cfg.spin_command = "ssh backup-spin"
            self.assertEqual(backup_spin(cfg, power, lambda dev: "hdd", lambda cmd: ("standby", True)), "Sleeping")
            self.assertEqual(backup_spin(cfg, power, lambda dev: "hdd", lambda cmd: ("standby", False)), "HDD")
            self.assertEqual(seen, [disk])
            state, reliable = reading_from_text(" drive state is:  active/idle\n", 0)
            self.assertEqual((state, reliable), ("active/idle", True))
            _state, reliable = reading_from_text(" drive state is:  standby\nSG_IO: bad\n", 0)
            self.assertFalse(reliable)
            cfg.spin_command = ""
            cfg.backup_disk = os.path.join(tmp, "missing")
            self.assertEqual(backup_spin(cfg, power, lambda dev: "hdd"), "")
            self.assertEqual(seen, [disk])
            self._write_drive(tmp, "sdc", "QEMU", "QEMU HARDDISK", "1")
            self._write_drive(tmp, "sdd", "ATA", "TOSHIBA MG08ADA800E", "1")
            self._write_drive(tmp, "nvme0n1", "", "Samsung SSD 970 EVO", "0")
            sys_root = os.path.join(tmp, "sys")
            self.assertEqual(drive_kind(os.path.join(tmp, "sdc"), sys_root), "hdd")
            self.assertEqual(drive_kind(os.path.join(tmp, "sdd"), sys_root), "hdd")
            self.assertEqual(drive_kind(os.path.join(tmp, "nvme0n1"), sys_root), "ssd")

    def test_repository_check_shows_progress_and_can_be_stopped(self):
        from immich_backup.actions import cancel_check, re_check
        from immich_backup.check import parse_progress, run
        parsed = parse_progress('{"type":"progress_percent","message":"Checking segments","current":10,"total":40}')
        self.assertEqual(parsed["percent"], 25)
        self.assertEqual(parsed["phase"], "Checking segments")
        self.assertEqual(parse_progress("Remote: Verifying data 12.5%")["percent"], 12.5)
        self.assertFalse(re_check("borg create --progress /repo"))
        self.assertTrue(re_check("borg check --progress --log-json /repo"))
        self.assertTrue(re_check("/usr/bin/python3 -m immich_backup check --config /etc/immich-backup/config.ini"))
        self.assertEqual(latest_outcome(["2026-10-29T01:00:00+05:30 repository check cancelled"]), ("check", "cancelled"))
        self.assertIn('? "Stop"', PAGE)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SimpleNamespace(
                state_dir=tmp,
                log=os.path.join(tmp, "backup.log"),
                backup_mount="/mnt/immich-backup",
                backup_uuid="disk",
                repo=os.path.join(tmp, "repo"),
            )

            class Proc:
                def __init__(self, args, **_kwargs):
                    self.args = args
                    self.stderr = self
                    self.lines = [
                        '{"type":"progress_percent","message":"Checking segments","current":10,"total":40}\n',
                        "",
                    ]
                    self.returncode = None
                    self.terminated = False

                def readline(self):
                    return self.lines.pop(0) if self.lines else ""

                def poll(self):
                    return self.returncode

                def terminate(self):
                    self.terminated = True
                    self.returncode = -15

                def kill(self):
                    self.returncode = -9

                def wait(self, timeout=None):
                    if self.returncode is None:
                        self.returncode = 0
                    return self.returncode

            seen = []

            def opener(args, **kwargs):
                proc = Proc(args, **kwargs)
                seen.append(args)
                return proc

            self.assertEqual(run(cfg, popen=opener, mounted_fn=lambda _cfg: True, busy_fn=lambda: False), 0)
            self.assertEqual(seen[0][:4], ["borg", "check", "--progress", "--log-json"])
            self.assertIsInstance(seen[0], list)
            with open(cfg.log, encoding="utf-8") as fh:
                self.assertIn("repository check passed", fh.read())

            class Stopping(Proc):
                def readline(self):
                    raise AssertionError("cancel should stop before reading Borg")

            def stopping_opener(args, **kwargs):
                with open(os.path.join(tmp, "check-cancel"), "w", encoding="utf-8") as fh:
                    fh.write("cancel\n")
                return Stopping(args, **kwargs)

            self.assertEqual(run(cfg, popen=stopping_opener, mounted_fn=lambda _cfg: True, busy_fn=lambda: False), 0)
            with open(cfg.log, encoding="utf-8") as fh:
                self.assertIn("repository check cancelled", fh.read())
            stopped = []
            cancel_check(cfg, job_fn=lambda: "check", stop_fn=lambda: stopped.append("stopped"))
            self.assertEqual(stopped, ["stopped"])
            with self.assertRaises(ValueError):
                cancel_check(cfg, job_fn=lambda: "backup", stop_fn=lambda: stopped.append("no"))
            self.assertEqual(stopped, ["stopped"])

    def test_hard_drive_sleeps_only_after_it_has_been_idle(self):
        cfg = SimpleNamespace(spindown_enabled=True, spindown_idle_seconds=600)
        self.assertFalse(should_standby(cfg, 100, "active/idle"))
        self.assertTrue(should_standby(cfg, 600, "active/idle"))
        self.assertFalse(should_standby(cfg, 600, "standby"))
        cfg.spindown_enabled = False
        self.assertFalse(should_standby(cfg, 600, "active/idle"))


if __name__ == "__main__":
    unittest.main()
