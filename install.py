#!/usr/bin/env python3
"""Install Immich backup on this Debian or Ubuntu machine."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from immich_backup.system_install import main


if __name__ == "__main__":
    raise SystemExit(main())
