"""Full setup flow with stand-in disks and a stand-in backup command.

Nothing here uses the machine's disks, Borg, Docker, or systemd. Every file is
created under a temporary directory and removed when the test ends.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import fcntl
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import quote

from immich_backup import auth, disks
from immich_backup.config import load
from immich_backup.status import Handler, configure


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
            "dump = " + os.path.join(directory, "dump.sql") + "\n"
            "compose = " + os.path.join(directory, "compose.yml") + "\n"
            "env_file = " + os.path.join(directory, "env") + "\n"
            "[identity]\n[backup]\nkeep_daily = 7\nwarn_gb = 50,20,10\n"
            "[schedule]\nlabel = 01:00\nbackup = *-*-* 01:00:00\n"
            "[notify]\ncommand =\nemail =\n[install]\nroles = backup, status\n"
        )
    return path


class Client:
    def __init__(self, port):
        self.port = port
        self.cookie = ""

    def request(self, method, path, body=None, csrf=True):
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(data))
            if csrf:
                headers["X-Immich-Backup"] = "1"
        if self.cookie:
            headers["Cookie"] = self.cookie
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            cookie = response.getheader("Set-Cookie") or ""
            if cookie.startswith("ib_session="):
                value = cookie.split(";", 1)[0]
                self.cookie = "" if "Max-Age=0" in cookie else value
            return response.status, raw
        finally:
            connection.close()

    def json(self, method, path, body=None, csrf=True):
        status, raw = self.request(method, path, body, csrf)
        return status, json.loads(raw.decode("utf-8") or "{}")


def forbid_host(*_args, **_kwargs):
    raise AssertionError("this test tried to use the host")


class FlowTests(unittest.TestCase):
    def setUp(self):
        auth._failures.clear()
        self.addCleanup(auth._failures.clear)
        self.tmp = tempfile.TemporaryDirectory(prefix="immich-backup-test-")
        self.root = self.tmp.name
        watched = ("/mnt/immich-backup", "/mnt/immich-media", "/mnt/immich-backup/.backup-disk")
        self.addCleanup(self._untouched, {path: os.path.exists(path) for path in watched})
        self.addCleanup(self._removed, self.root)
        self.addCleanup(self.tmp.cleanup)
        self.photos = os.path.join(self.root, "photos")
        self.backup = os.path.join(self.root, "backup")
        os.makedirs(self.photos)
        os.makedirs(self.backup)
        self.rows = [
            {"mount": "/", "uuid": "root-uuid", "fstype": "ext4", "label": "root", "size": 1, "serial": "root"},
            {"mount": self.photos, "uuid": "photo-uuid", "fstype": "ext4", "label": "photos", "size": 100, "serial": "photo"},
            {"mount": self.backup, "uuid": "backup-uuid", "fstype": "ext4", "label": "backup", "size": 200, "serial": "backup"},
            {"mount": os.path.join(self.root, "blank"), "uuid": "", "fstype": "ext4", "label": "blank", "size": 10, "serial": ""},
        ]
        self.cfg_path = write_config(self.root)
        configure(load(self.cfg_path))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self._stop)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = Client(self.server.server_address[1])
        self.disks = patch("immich_backup.web.disks.list_disks", return_value=self.rows)
        self.popen = patch("immich_backup.actions.subprocess.Popen")
        self.status = patch("immich_backup.status.cached_status", return_value={"state": "idle"})
        self.host = [
            patch("immich_backup.disks.subprocess.run", side_effect=forbid_host),
            patch("immich_backup.backup.subprocess.run", side_effect=forbid_host),
            patch("immich_backup.check.subprocess.run", side_effect=forbid_host),
            patch("immich_backup.status.subprocess.run", side_effect=forbid_host),
            patch("immich_backup.status.urllib.request.urlopen", side_effect=forbid_host),
        ]
        self.disks.start()
        self.started = self.popen.start()
        self.status.start()
        for item in self.host:
            item.start()
        self.addCleanup(self.disks.stop)
        self.addCleanup(self.popen.stop)
        self.addCleanup(self.status.stop)
        for item in self.host:
            self.addCleanup(item.stop)

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()

    def _removed(self, root):
        self.assertFalse(os.path.exists(root))

    def _untouched(self, before):
        for path, existed in before.items():
            self.assertEqual(os.path.exists(path), existed)

    def test_setup_settings_and_actions_stay_inside_the_temporary_directory(self):
        status, page = self.client.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Set up this backup", page)

        status, body = self.client.json("POST", "/api/setup/account", {"email": "owner@example.com", "password": "short"})
        self.assertEqual(status, 400)
        self.assertIn("8 characters", body["error"])
        status, body = self.client.json(
            "POST", "/api/setup/account",
            {"email": "owner@example.com", "password": "correct-horse"},
            csrf=False,
        )
        self.assertEqual(status, 400)

        status, body = self.client.json("POST", "/api/setup/account", {"email": "owner@example.com", "password": "correct-horse"})
        self.assertEqual(status, 200)
        self.assertTrue(self.client.cookie.startswith("ib_session="))
        status, body = self.client.json("POST", "/api/setup/account", {"email": "owner@example.com", "password": "correct-horse"})
        self.assertEqual(status, 400)

        status, page = self.client.request("GET", "/")
        self.assertIn(b"Run backup now", page)
        self.assertIn(b"Check repository", page)
        status, rows = self.client.json("GET", "/api/disks")
        self.assertEqual(status, 200)
        self.assertEqual([row["mount"] for row in rows], [row["mount"] for row in self.rows])

        status, body = self.client.json("POST", "/api/setup/drives", {"photos": self.photos, "backup": self.photos})
        self.assertEqual(status, 400)
        self.assertIn("different", body["error"])
        status, body = self.client.json("POST", "/api/setup/drives", {"photos": "/", "backup": self.backup})
        self.assertEqual(status, 400)
        status, body = self.client.json("POST", "/api/setup/drives", {"photos": self.photos, "backup": os.path.join(self.root, "blank")})
        self.assertEqual(status, 400)
        self.assertIn("UUID", body["error"])

        status, body = self.client.json("POST", "/api/setup/drives", {"photos": self.photos, "backup": self.backup})
        self.assertEqual(status, 200)
        saved = load(self.cfg_path)
        self.assertEqual(saved.media_uuid, "photo-uuid")
        self.assertEqual(saved.backup_uuid, "backup-uuid")
        self.assertEqual(saved.backup_parent_serial, "backup")
        self.assertTrue(saved.repo.startswith(self.root))
        marker = os.path.join(self.backup, ".backup-disk")
        with open(marker, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "backup-uuid")
        self.assertTrue(marker.startswith(self.root))

        status, body = self.client.json("POST", "/api/setup/alerts", {"email": "not-an-email"})
        self.assertEqual(status, 400)
        status, body = self.client.json("POST", "/api/setup/alerts", {"email": "alerts@example.com"})
        self.assertEqual(status, 200)
        status, settings = self.client.json("GET", "/api/settings")
        self.assertEqual(settings["email"], "alerts@example.com")

        status, body = self.client.json("POST", "/api/settings", {
            "email": "alerts@example.com", "label": "03:30", "keep_daily": 14, "warn_gb": "nope",
        })
        self.assertEqual(status, 400)
        status, body = self.client.json("POST", "/api/settings", {
            "email": "alerts@example.com", "label": "03:30", "keep_daily": 14, "warn_gb": "40,15,5",
        })
        self.assertEqual(status, 200)
        status, settings = self.client.json("GET", "/api/settings")
        self.assertEqual(settings["label"], "03:30")
        self.assertEqual(settings["keep_daily"], 14)
        self.assertEqual(settings["warn_gb"], "40,15,5")
        self.assertEqual(settings["stop_free_gb"], 10)
        self.assertEqual(load(self.cfg_path).backup_calendar, "*-*-* 03:30:00")
        status, body = self.client.json("POST", "/api/settings", {
            "email": "alerts@example.com", "label": "03:30", "keep_daily": 14, "warn_gb": "40,15,5",
            "stop_free_gb": -1,
        })
        self.assertEqual(status, 400)
        status, body = self.client.json("POST", "/api/settings", {
            "email": "alerts@example.com", "label": "03:30", "keep_daily": 14, "warn_gb": "40,15,5",
            "stop_free_gb": 2,
        })
        self.assertEqual(status, 200)
        self.assertEqual(load(self.cfg_path).stop_free_gb, 2)

        self.client.cookie = ""
        status, _body = self.client.json("GET", "/api/status")
        self.assertEqual(status, 401)
        status, _page = self.client.request("GET", "/settings")
        self.assertEqual(status, 302)
        status, body = self.client.json("POST", "/api/login", {"email": "owner@example.com", "password": "wrong-pass"})
        self.assertEqual(status, 401)
        status, body = self.client.json("POST", "/api/login", {"email": "owner@example.com", "password": "correct-horse"})
        self.assertEqual(status, 200)
        status, body = self.client.json("GET", "/api/status")
        self.assertEqual(body, {"state": "idle"})

        status, body = self.client.json("POST", "/api/actions/backup", {})
        self.assertEqual(status, 200)
        self.assertIn("spin up", body["message"])
        args, kwargs = self.started.call_args
        self.assertEqual(args[0], [sys.executable, "-m", "immich_backup", "backup", "--config", self.cfg_path])
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(self.cfg_path.startswith(self.root))

        self.started.reset_mock()
        status, body = self.client.json("POST", "/api/actions/check", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.started.call_args[0][0][3], "check")

        self.started.reset_mock()
        handle = open(os.path.join(self.root, "backup.lock"), "a", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            status, body = self.client.json("POST", "/api/actions/backup", {})
            self.assertEqual(status, 200)
            self.assertIn("already running", body["message"])
            self.started.assert_not_called()
        finally:
            handle.close()

        status, body = self.client.json("POST", "/api/password", {"current": "wrong-pass", "new_password": "new-password"})
        self.assertEqual(status, 400)
        status, body = self.client.json("POST", "/api/password", {"current": "correct-horse", "new_password": "new-password"})
        self.assertEqual(status, 200)
        self.assertEqual(self.client.cookie, "")
        status, _body = self.client.json("GET", "/api/status")
        self.assertEqual(status, 401)
        status, body = self.client.json("POST", "/api/login", {"email": "owner@example.com", "password": "correct-horse"})
        self.assertEqual(status, 401)
        status, body = self.client.json("POST", "/api/login", {"email": "owner@example.com", "password": "new-password"})
        self.assertEqual(status, 200)

        status, body = self.client.json("POST", "/api/logout", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.client.cookie, "")
        status, page = self.client.request("GET", "/")
        self.assertIn(b"Sign in", page)

    def test_login_locks_after_repeated_failures(self):
        self.client.json("POST", "/api/setup/account", {"email": "owner@example.com", "password": "correct-horse"})
        self.client.cookie = ""
        for _ in range(8):
            status, body = self.client.json("POST", "/api/login", {"email": "owner@example.com", "password": "wrong-pass"})
            self.assertEqual(status, 401)
        status, body = self.client.json("POST", "/api/login", {"email": "owner@example.com", "password": "correct-horse"})
        self.assertEqual(status, 429)
        self.assertIn("Wait", body["error"])

    def test_restore_choice_does_not_touch_the_host(self):
        status, page = self.client.request("GET", "/")
        self.assertIn(b"Restore this machine", page)
        self.assertIn(b"Docker and Immich", page)
        self.client.json("POST", "/api/setup/account", {"email": "owner@example.com", "password": "correct-horse"})
        status, setup = self.client.request("GET", "/setup")
        self.assertEqual(status, 200)
        self.assertIn(b">Back<", setup)
        self.assertIn(b"Try restore again", self.client.request("GET", "/")[1])
        self.assertIn(b"Set up a new backup instead", self.client.request("GET", "/")[1])
        self.client.cookie = ""
        self.assertEqual(self.client.request("GET", "/setup")[0], 302)
        self.client.json("POST", "/api/login", {"email": "owner@example.com", "password": "correct-horse"})
        self.started.reset_mock()
        with patch(
            "immich_backup.restore.list_archives",
            return_value=[{"name": "immich-2026-10-27_020011", "time": "2026-10-27T02:00:11"}],
        ) as listed:
            status, rows = self.client.json("GET", "/api/restore/archives?mount=" + quote(self.backup))
            self.assertEqual(status, 200)
            self.assertEqual(rows[0]["name"], "immich-2026-10-27_020011")
            listed.assert_called_once()
            status, body = self.client.json("GET", "/api/restore/archives?mount=/")
            self.assertEqual(status, 400)
        status, body = self.client.json("POST", "/api/setup/restore", {
            "photos": self.photos,
            "backup": self.backup,
            "archive": "immich-2026-10-27_020011",
            "email": "alerts@example.com",
            "confirm": "no",
        })
        self.assertEqual(status, 400)
        self.started.assert_not_called()
        self.assertEqual(load(self.cfg_path).restore_archive, "")
        self.assertFalse(os.path.exists(os.path.join(self.backup, ".backup-disk")))
        status, rows = self.client.json("GET", "/api/disks")
        self.assertNotIn("has_repo", rows[1])
        status, body = self.client.json("POST", "/api/setup/restore", {
            "photos": self.photos,
            "backup": self.backup,
            "archive": "immich-2026-10-27_020011",
            "email": "alerts@example.com",
            "confirm": "restore",
        })
        self.assertEqual(status, 400)
        self.assertIn("no Immich backup", body["error"])
        self.started.assert_not_called()
        os.makedirs(os.path.join(self.backup, "immich-borg"))
        with open(os.path.join(self.backup, "immich-borg", "config"), "w", encoding="utf-8") as fh:
            fh.write("[repository]\n")
        status, rows = self.client.json("GET", "/api/disks?repo=1")
        self.assertEqual({row["mount"]: row["has_repo"] for row in rows}[self.backup], True)
        self.assertEqual({row["mount"]: row["has_repo"] for row in rows}[self.photos], False)
        with patch("immich_backup.web.status.active_job", return_value="backup"):
            status, body = self.client.json("POST", "/api/setup/restore", {
                "photos": self.photos,
                "backup": self.backup,
                "archive": "immich-2026-10-27_020011",
                "email": "alerts@example.com",
                "confirm": "restore",
            })
        self.assertEqual(status, 400)
        self.assertIn("backup finishes", body["error"])
        self.started.assert_not_called()
        status, body = self.client.json("POST", "/api/setup/restore", {
            "photos": self.photos,
            "backup": self.backup,
            "archive": "immich-2026-10-27_020011",
            "email": "alerts@example.com",
            "confirm": "restore",
        })
        self.assertEqual(status, 200)
        self.assertIn("Docker and Immich", body["message"])
        self.assertEqual(self.started.call_args[0][0][3], "restore")
        self.assertTrue(self.cfg_path.startswith(self.root))
        saved = load(self.cfg_path)
        self.assertEqual(saved.restore_archive, "immich-2026-10-27_020011")
        self.assertEqual(saved.email, "alerts@example.com")
        self.assertTrue(os.path.join(self.backup, ".backup-disk").startswith(self.root))
        with patch("immich_backup.web.status.active_job", return_value="restore"):
            for path in ("/api/actions/backup", "/api/actions/check"):
                self.started.reset_mock()
                status, body = self.client.json("POST", path, {})
                self.assertEqual(status, 400)
                self.assertIn("restore finishes", body["error"])
                self.started.assert_not_called()
            status, body = self.client.json("POST", "/api/settings/drives", {"photos": self.photos, "backup": self.backup})
            self.assertEqual(status, 400)
            status, settings = self.client.json("GET", "/api/settings")
            self.assertEqual(settings["job"], "restore")

    def test_sleep_settings_are_only_for_a_hard_drive(self):
        self.client.json("POST", "/api/setup/account", {"email": "owner@example.com", "password": "correct-horse"})
        status, settings = self.client.json("GET", "/api/settings")
        self.assertFalse(settings["hdd"])
        status, body = self.client.json("POST", "/api/settings/sleep", {"enabled": False, "idle_minutes": 15})
        self.assertEqual(status, 400)
        self.rows[2]["rotational"] = True
        status, body = self.client.json("POST", "/api/setup/drives", {"photos": self.photos, "backup": self.backup})
        self.assertEqual(status, 200)
        status, settings = self.client.json("GET", "/api/settings")
        self.assertTrue(settings["hdd"])
        self.assertTrue(settings["spindown_enabled"])
        self.assertEqual(settings["spindown_idle_minutes"], 10)
        page = self.client.request("GET", "/settings")[1]
        self.assertIn(b"Sleep when idle", page)
        status, body = self.client.json("POST", "/api/settings/sleep", {"enabled": False, "idle_minutes": 15})
        self.assertEqual(status, 200)
        saved = load(self.cfg_path)
        self.assertFalse(saved.spindown_enabled)
        self.assertEqual(saved.spindown_idle_seconds, 900)
        status, body = self.client.json("POST", "/api/settings/sleep", {"enabled": True, "idle_minutes": 0})
        self.assertEqual(status, 400)

    def test_settings_can_point_the_backup_at_another_mounted_drive(self):
        self.client.json("POST", "/api/setup/account", {"email": "owner@example.com", "password": "correct-horse"})
        self.client.json("POST", "/api/setup/drives", {"photos": self.photos, "backup": self.backup})
        other = os.path.join(self.root, "other-backup")
        os.makedirs(other)
        self.rows.append({
            "mount": other,
            "uuid": "other-uuid",
            "fstype": "ext4",
            "label": "other",
            "size": 300,
            "serial": "other",
            "rotational": True,
        })
        page = self.client.request("GET", "/settings")[1]
        self.assertIn(b"Save drives", page)
        self.assertIn(b"Photo drive", page)
        with patch("immich_backup.web.status.borg_running", return_value={"elapsed": "1"}):
            status, body = self.client.json("POST", "/api/settings/drives", {"photos": self.photos, "backup": other})
        self.assertEqual(status, 400)
        self.assertIn("backup finishes", body["error"])
        status, body = self.client.json("POST", "/api/settings/drives", {"photos": self.photos, "backup": other})
        self.assertEqual(status, 200)
        saved = load(self.cfg_path)
        self.assertEqual(saved.media_mount, self.photos)
        self.assertEqual(saved.backup_mount, other)
        self.assertEqual(saved.backup_uuid, "other-uuid")
        self.assertTrue(saved.backup_rotational)
        self.assertEqual(saved.repo, other + "/immich-borg")
        marker = os.path.join(other, ".backup-disk")
        with open(marker, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "other-uuid")
        self.assertTrue(marker.startswith(self.root))
        status, settings = self.client.json("GET", "/api/settings")
        self.assertEqual(settings["backup"], other)
        self.assertTrue(settings["hdd"])


class DiskFixtureTests(unittest.TestCase):
    def test_fixture_replaces_the_host_disk_list(self):
        with tempfile.TemporaryDirectory(prefix="immich-backup-test-") as tmp:
            fixture = os.path.join(tmp, "disks.json")
            rows = [{
                "mount": os.path.join(tmp, "photos"),
                "uuid": "photo-uuid",
                "fstype": "ext4",
                "label": "photos",
                "size": 1,
                "serial": "photo",
            }]
            with open(fixture, "w", encoding="utf-8") as fh:
                json.dump(rows, fh)
            with patch.dict(os.environ, {"IMMICH_BACKUP_DISK_FIXTURE": fixture}):
                with patch("immich_backup.disks.subprocess.run", side_effect=forbid_host):
                    listed = disks.list_disks()
            self.assertEqual(listed, rows)
        self.assertFalse(os.path.exists(tmp))


if __name__ == "__main__":
    unittest.main()
