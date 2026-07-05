"""Locates the external command-line tools the GUI relies on, and describes
their status for the user.

Two tools are invoked at runtime:

* ``dvr-scan`` — **required**; the motion-detection engine.
* ``ffprobe``  — optional; reads each recording's start time and duration for
  the clock-time / global-timeline features. When absent, those features are
  simply unavailable.

Resolution order for every tool is *bundle first, then PATH*:

1. A binary shipped inside / beside the frozen executable. On a PyInstaller
   onefile build these are unpacked to ``sys._MEIPASS``; onedir/portable builds
   keep them next to ``sys.executable``. This is how the Windows build ships
   ffmpeg/ffprobe so users only need to install DVR-Scan themselves.
2. The system ``PATH`` (``shutil.which``) — the normal case on Linux.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

# Where users can get each tool.
DVR_SCAN_URL = "https://www.dvr-scan.com/download/"
FFMPEG_URL = "https://ffmpeg.org/download.html"

IS_WINDOWS = sys.platform.startswith("win")


def _bundle_dirs() -> list[str]:
    """Directories that may hold binaries shipped with a frozen build."""
    dirs: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)  # PyInstaller onefile unpack dir
    if meipass:
        dirs.append(meipass)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
    return dirs


def _exe_name(name: str) -> str:
    return f"{name}.exe" if IS_WINDOWS else name


def find_tool(name: str) -> str | None:
    """Return the path to *name*, preferring a bundled copy, or ``None``."""
    exe = _exe_name(name)
    for directory in _bundle_dirs():
        candidate = os.path.join(directory, exe)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which(name)


def is_bundled(path: str | None) -> bool:
    """True if *path* resolves inside a frozen build's bundle directory."""
    if not path:
        return False
    path = os.path.abspath(path)
    return any(path.startswith(os.path.abspath(d) + os.sep) for d in _bundle_dirs())


def no_window_kwargs() -> dict:
    """subprocess kwargs that stop a console window flashing on Windows.

    A ``--windowed`` PyInstaller app has no console, so each ``subprocess`` call
    would otherwise pop a black console window. ``CREATE_NO_WINDOW`` suppresses
    it. No-op on other platforms.
    """
    if IS_WINDOWS:
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


@dataclass(frozen=True)
class Tool:
    """A external tool the GUI depends on, with user-facing guidance."""

    key: str          # executable base name, e.g. "dvr-scan"
    label: str        # display name, e.g. "DVR-Scan"
    required: bool
    purpose: str      # one line: what the app uses it for
    url: str          # where to get it
    linux_hint: str   # how to install on Linux

    def install_hint(self) -> str:
        """Platform-appropriate install guidance for when the tool is missing."""
        if IS_WINDOWS:
            return f"Download and install it, then restart the app:\n{self.url}"
        return self.linux_hint


TOOLS: tuple[Tool, ...] = (
    Tool(
        key="dvr-scan",
        label="DVR-Scan",
        required=True,
        purpose="Motion detection — the core scan engine.",
        url=DVR_SCAN_URL,
        linux_hint="Install with 'pip install dvr-scan' (see dvr-scan.com).",
    ),
    Tool(
        key="ffprobe",
        label="FFprobe",
        required=False,
        purpose="Reads recording start time & duration for the clock-time timeline.",
        url=FFMPEG_URL,
        linux_hint="Install the 'ffmpeg' package (e.g. 'sudo apt install ffmpeg').",
    ),
)


@dataclass(frozen=True)
class ToolStatus:
    tool: Tool
    path: str | None

    @property
    def found(self) -> bool:
        return self.path is not None

    @property
    def bundled(self) -> bool:
        return is_bundled(self.path)


def check_all() -> list[ToolStatus]:
    """Resolve every known tool and report where (if anywhere) it was found."""
    return [ToolStatus(tool, find_tool(tool.key)) for tool in TOOLS]


def missing_required(statuses: list[ToolStatus] | None = None) -> list[ToolStatus]:
    statuses = statuses if statuses is not None else check_all()
    return [s for s in statuses if s.tool.required and not s.found]


def missing_optional(statuses: list[ToolStatus] | None = None) -> list[ToolStatus]:
    statuses = statuses if statuses is not None else check_all()
    return [s for s in statuses if not s.tool.required and not s.found]
