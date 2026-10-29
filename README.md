# Immich backup

Nightly, versioned backups for an Immich library, and a status page on the same machine.

Each archive holds the photos and the database together, so a restored library still has its albums, people, and places. Open the page to see the last backup, start one now, or rebuild Immich on another computer from the backup disk. A signed-in user can download one archive as a tar file; the browser saves it, and asks where to put it when that is available.

A successful backup stays quiet. Failures and low-disk warnings are mailed to you.

## Install

Debian or Ubuntu, with Python 3.10 or newer. The photo disk and the backup disk should already be mounted.

```bash
git clone https://github.com/madhukartemba/immich-backup.git
cd immich-backup
sudo python3 install.py
```

The installer adds Borg, rsync, and Docker when Docker is not already installed. It copies the program to `/opt/immich-backup`, writes `/etc/immich-backup/config.ini` when that file is missing, and starts the status page on port **8090**.

```text
http://<this-machine>:8090
```

Disks are left as they are. Running the installer again refreshes the program and keeps the config you already have.

## First visit

Create an account. Then choose one of the two paths.

**Set up a new backup.** For a machine that already runs Immich. Choose the photo drive and the backup drive, then the alert email, the backup time, and how many days to keep. The first backup creates the repository. Seven days are kept, the backup runs at 01:00, and the repository is checked on Sunday at 10:00. Settings can change the photo drive, the backup drive, the alert email, the backup time, and how many days to keep. Archives already stored on a previous backup drive stay on that drive.

**Restore this machine.** For an empty computer. Choose the backup drive, a different photo drive, and one archive. Restore installs Immich, copies the library onto the photo drive, loads the database from that same archive, and starts Immich. It shows each step on the status page and finishes only once Immich answers. Backups, checks, and drive changes wait until it is done. The backup drive is only read.

After you sign in, the status page has **Run backup now** and **Check repository**.

When the backup drive is a hard disk, Settings shows **Sleep when idle** and the number of minutes to wait. The check does not wake a drive that is already asleep.

## Disk health on another computer

Use this when the backup disk is plugged into a different machine, such as the Proxmox host:

```bash
sudo python3 install.py --host
```

That installs hard-drive sleep and the disk-health check. The nightly backup stays on the Immich machine. Set the disk paths in `/etc/immich-backup/config.ini` on the host before expecting sleep or health mail.

## Tests

```bash
PYTHONPATH=. python3 -m unittest tests.test_flow tests.test_restore tests.test_setup tests.test_app tests.test_install
```

The tests use temporary directories and stand-ins for disks, Borg, Docker, and apt.

## This server

The Immich virtual machine is still on the earlier scripts. Move it to this program after one night from the installer has finished, and do that while the backup service is inactive.
