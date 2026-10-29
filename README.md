# Immich backup

This is the backup program used for an Immich server: the nightly Borg backup, the status page, and the disk health checks. Paths, disk identities, the schedule, and mail are in one config file, so another Immich install can use the same program.

The running virtual machine has not been switched to this copy.

## Commands

Run from this directory:

```bash
PYTHONPATH=. python3 -m immich_backup check-config --config config.vm.example.ini
PYTHONPATH=. python3 -m immich_backup install --config config.vm.example.ini --output dist/vm
PYTHONPATH=. python3 -m immich_backup install --config config.host.example.ini --output dist/host
```

On the Immich machine the useful commands are `backup`, `check`, `archives`, `serve`, and `store`.

On the Proxmox host the useful commands are `health`, `publish`, `spindown`, and `notify`. `health` reads the backup disk only when that disk is already awake, and skips it when it is asleep. `publish` and `store` only read saved files.

The program is Python 3.10 or newer and uses the standard library only. Copy `config.vm.example.ini` or `config.host.example.ini` to `config.ini` for a real machine. `config.ini` stays on that machine.

## Tests

The tests use temporary directories and stand-ins for disks, Borg, and Docker.

```bash
PYTHONPATH=. python3 -m unittest tests.test_flow tests.test_restore tests.test_setup tests.test_app
```

## What a future install needs

Copy this directory to `/opt/immich-backup`. Copy the matching example config to `/etc/immich-backup/config.ini` and change the disk UUIDs, serial, paths, and mail address. Create the Borg repository separately. Put the backup filesystem UUID in `/mnt/immich-backup/.backup-disk`. Then install the systemd units from `install` and enable the timers.

The status page asks for an account the first time it is opened. After the account, choose a new backup or restore this machine. Restore installs Docker and Immich, copies the library onto the photo drive, and loads the database from the archive. The backup drive is only read, and nothing is formatted. A new backup asks for the photo drive and the backup drive, then the alert email, backup time, retention, and password. When the backup drive is a hard disk, Settings also has the idle time before it sleeps. Run backup now and Check repository are on the status page after signing in. A successful backup still does not send mail.

## Trying it on this virtual machine

Keep the current scripts until one night with this program has finished. Copy the app to `/opt/immich-backup` on the Immich VM, install `config.vm.example.ini` as `/etc/immich-backup/config.ini`, and point the backup, check, and status services at `python3 -m immich_backup`. Do that while the backup service is inactive. The host health checks can stay as they are until the VM copy has run once.
