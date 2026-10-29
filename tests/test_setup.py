import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from immich_backup.auth import change_password, create_account, has_account, verify_password
from immich_backup.config import load
from immich_backup.status import Handler, configure


def minimal_config(directory):
    path = os.path.join(directory, "config.ini")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "[paths]\nstate_dir = " + os.path.join(directory, "state") + "\n"
            "lock = " + os.path.join(directory, "backup.lock") + "\n"
            "log = " + os.path.join(directory, "backup.log") + "\n"
            "[identity]\n[backup]\nkeep_daily = 7\nwarn_gb = 50,20,10\n"
            "[schedule]\nlabel = 01:00\nbackup = *-*-* 01:00:00\n"
            "[notify]\nemail =\n[install]\nroles = backup, status\n"
        )
    return path


class SetupTests(unittest.TestCase):
    def test_account_and_password_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load(minimal_config(tmp))
            os.makedirs(cfg.state_dir)
            configure(cfg)
            self.assertFalse(has_account(cfg))
            create_account(cfg, "owner@example.com", "correct-horse")
            self.assertTrue(verify_password(cfg, "owner@example.com", "correct-horse"))
            self.assertFalse(verify_password(cfg, "other@example.com", "correct-horse"))
            change_password(cfg, "correct-horse", "new-password")
            self.assertTrue(verify_password(cfg, "owner@example.com", "new-password"))

    def test_drive_choice_rejects_the_same_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            photos = os.path.join(tmp, "photos")
            backup = os.path.join(tmp, "backup")
            os.makedirs(photos)
            os.makedirs(backup)
            fixture = os.path.join(tmp, "disks.json")
            with open(fixture, "w", encoding="utf-8") as fh:
                json.dump([
                    {"mount": photos, "uuid": "photo-uuid", "fstype": "ext4", "label": "photos", "size": 10, "serial": "photo"},
                    {"mount": backup, "uuid": "backup-uuid", "fstype": "ext4", "label": "backup", "size": 20, "serial": "backup"},
                ], fh)
            os.environ["IMMICH_BACKUP_DISK_FIXTURE"] = fixture
            try:
                cfg = load(minimal_config(tmp))
                configure(cfg)
                create_account(cfg, "owner@example.com", "correct-horse")
                from immich_backup.web import save_drives
                with self.assertRaises(ValueError):
                    save_drives(photos, photos)
                save_drives(photos, backup)
                saved = load(cfg.config_path)
                self.assertEqual(saved.media_uuid, "photo-uuid")
                self.assertEqual(saved.backup_uuid, "backup-uuid")
                with open(os.path.join(backup, ".backup-disk"), encoding="utf-8") as marker:
                    self.assertEqual(marker.read().strip(), "backup-uuid")
            finally:
                os.environ.pop("IMMICH_BACKUP_DISK_FIXTURE", None)

    def test_pages_require_login_after_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load(minimal_config(tmp))
            os.makedirs(cfg.state_dir)
            configure(cfg)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = "http://127.0.0.1:" + str(port)
                setup = urllib.request.urlopen(base + "/")
                self.assertIn(b"Set up this backup", setup.read())
                body = json.dumps({"email": "owner@example.com", "password": "correct-horse"}).encode()
                request = urllib.request.Request(
                    base + "/api/setup/account",
                    data=body,
                    headers={"Content-Type": "application/json", "X-Immich-Backup": "1"},
                )
                response = urllib.request.urlopen(request)
                cookie = response.headers.get("Set-Cookie").split(";", 1)[0]
                status = urllib.request.Request(base + "/api/status")
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(status)
                self.assertEqual(caught.exception.code, 401)
                authed = urllib.request.Request(base + "/", headers={"Cookie": cookie})
                page = urllib.request.urlopen(authed).read()
                self.assertIn(b"Run backup now", page)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
