"""PyInstaller entry point for the DVR-Scan GUI.

Mirrors ``python -m dvr_scan_gui`` so the app can be frozen into a single
executable. Run directly with ``python run.py`` during development too.
"""

import sys

from dvr_scan_gui.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
