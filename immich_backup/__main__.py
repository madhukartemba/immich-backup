"""Command line for the backup, status page, and health checks."""

import argparse
import sys

from immich_backup import __version__
from immich_backup.config import load


def main(argv=None):
    parser = argparse.ArgumentParser(prog="immich-backup")
    parser.add_argument("--config", required=True, help="path to the ini config")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "command",
        choices=(
            "backup", "check", "restore", "archives", "serve", "health", "publish",
            "store", "spindown", "notify", "install", "check-config",
        ),
    )
    parser.add_argument("--output", help="directory for install -- write systemd units here")
    args = parser.parse_args(argv)
    cfg = load(args.config)
    if args.command == "check-config":
        print("config ok")
        print("roles:", ", ".join(cfg.roles))
        print("repo:", cfg.repo)
        return 0
    if args.command == "backup":
        from immich_backup.backup import run
        return run(cfg)
    if args.command == "restore":
        from immich_backup.restore import run
        return run(cfg)
    if args.command == "check":
        from immich_backup.check import run
        return run(cfg)
    if args.command == "archives":
        from immich_backup.archives import refresh
        refresh(cfg)
        return 0
    if args.command == "serve":
        from immich_backup.status import serve
        serve(cfg)
        return 0
    if args.command == "health":
        from immich_backup.health import check
        return check(cfg)
    if args.command == "publish":
        from immich_backup.health import publish
        publish(cfg)
        return 0
    if args.command == "store":
        from immich_backup.health import store
        store(cfg)
        return 0
    if args.command == "spindown":
        from immich_backup.spindown import run
        run(cfg)
        return 0
    if args.command == "notify":
        from immich_backup.notify import run
        return run(cfg)
    if args.command == "install":
        from immich_backup.install import write_units
        write_units(cfg, args.output)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
