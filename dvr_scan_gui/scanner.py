"""Wraps the ``dvr-scan`` command-line tool.

The scan always runs in *scan-only* mode (``-so``): DVR-Scan only detects
motion events and never writes any extracted video clips to disk. Under the
default verbosity it prints its log lines plus, as the final stdout line, a
comma-separated list of timecodes — two timecodes (start, end) per detected
event, e.g.::

    00:00:00.533,00:00:05.067,00:00:05.133,00:00:09.067

This module turns that line into :class:`MotionEvent` objects and reports scan
progress (parsed from the tqdm progress bar DVR-Scan writes to stderr).

We intentionally avoid ``--quiet``: it would give us only the CSV but also
suppresses the progress bar, leaving the progress widget stuck until the end.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass

from PySide6.QtCore import QObject, QProcess, Signal


@dataclass(frozen=True)
class MotionEvent:
    """A single detected motion event, with millisecond bounds."""

    index: int
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


# DVR-Scan timecodes look like HH:MM:SS.mmm (the fractional part may vary).
_TIMECODE_RE = re.compile(r"^(\d+):(\d{2}):(\d{2}(?:\.\d+)?)$")
# tqdm progress lines on stderr contain e.g. "Progress:  53%|".
_PROGRESS_RE = re.compile(r"(\d{1,3})%")
# A results line is a comma-separated list that starts with a timecode.
_CSV_LINE_RE = re.compile(r"^\d+:\d{2}:\d{2}(?:\.\d+)?,")


def timecode_to_ms(timecode: str) -> int:
    """Convert an ``HH:MM:SS.mmm`` timecode to integer milliseconds."""
    match = _TIMECODE_RE.match(timecode.strip())
    if not match:
        raise ValueError(f"Unrecognized timecode: {timecode!r}")
    hours, minutes, seconds = match.groups()
    total = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return int(round(total * 1000))


def ms_to_timecode(ms: int) -> str:
    """Convert milliseconds to a ``HH:MM:SS.mmm`` timecode for display."""
    ms = max(0, int(ms))
    hours, ms = divmod(ms, 3600_000)
    minutes, ms = divmod(ms, 60_000)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}"


def parse_events(csv_line: str) -> list[MotionEvent]:
    """Parse DVR-Scan's comma-separated timecode output into events."""
    parts = [p.strip() for p in csv_line.split(",") if p.strip()]
    events: list[MotionEvent] = []
    # Timecodes come in (start, end) pairs, one pair per event.
    for i in range(0, len(parts) - 1, 2):
        try:
            start_ms = timecode_to_ms(parts[i])
            end_ms = timecode_to_ms(parts[i + 1])
        except ValueError:
            continue
        events.append(MotionEvent(len(events) + 1, start_ms, end_ms))
    return events


@dataclass
class ScanOptions:
    """User-configurable DVR-Scan parameters (scan-only; no output files)."""

    input_path: str
    threshold: float = 0.15
    min_event_length: str = "0.1s"
    time_before_event: str = "1.5s"
    time_post_event: str = "2.0s"
    bg_subtractor: str = "MOG2"
    kernel_size: int = -1
    downscale_factor: int = 0
    frame_skip: int = 0
    start_time: str = ""
    end_time: str = ""
    duration: str = ""
    regions: list[list[tuple[int, int]]] | None = None

    def to_args(self) -> list[str]:
        args = [
            "-i", self.input_path,
            "--scan-only",        # never write extracted clips
            # NOTE: we deliberately do NOT pass --quiet. Quiet mode suppresses
            # the tqdm progress bar (written to stderr), which we need to drive
            # the progress widget. The comma-separated results are still emitted
            # as the last stdout line under the default verbosity.
            "-t", str(self.threshold),
            "-l", self.min_event_length,
            "-tb", self.time_before_event,
            "-tp", self.time_post_event,
            "-b", self.bg_subtractor,
            "-k", str(self.kernel_size),
            "-df", str(self.downscale_factor),
            "-fs", str(self.frame_skip),
        ]
        if self.start_time.strip():
            args += ["-st", self.start_time.strip()]
        if self.duration.strip():
            args += ["-dt", self.duration.strip()]
        elif self.end_time.strip():
            args += ["-et", self.end_time.strip()]
        # Each region becomes its own -a polygon (a flat sequence of X Y points).
        for region in self.regions or []:
            if len(region) >= 3:
                args.append("-a")
                for x, y in region:
                    args += [str(x), str(y)]
        return args


class Scanner(QObject):
    """Runs ``dvr-scan`` asynchronously via :class:`QProcess`.

    Signals:
        progress(int):           0-100 percentage parsed from stderr.
        log(str):                raw stderr lines, for the log view.
        finished(list):          list[MotionEvent] on success.
        failed(str):             human-readable error message.
    """

    progress = Signal(int)
    log = Signal(str)
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._stdout = ""

    @staticmethod
    def executable() -> str | None:
        """Locate the dvr-scan executable, or None if not installed."""
        return shutil.which("dvr-scan")

    @property
    def is_running(self) -> bool:
        return self._process is not None

    def start(self, options: ScanOptions) -> None:
        if self.is_running:
            self.failed.emit("A scan is already in progress.")
            return

        exe = self.executable()
        if not exe:
            self.failed.emit(
                "Could not find the 'dvr-scan' executable on PATH. "
                "Install it with 'pip install dvr-scan'."
            )
            return

        self._stdout = ""
        self._process = QProcess(self)
        self._process.setProgram(exe)
        self._process.setArguments(options.to_args())
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._process.start()

    def cancel(self) -> None:
        if self._process is not None:
            self._process.kill()

    def _on_stdout(self) -> None:
        assert self._process is not None
        chunk = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        self._stdout += chunk
        # Surface DVR-Scan's informative log lines (e.g. "Scanning…") live.
        for line in chunk.splitlines():
            line = line.strip()
            if line.startswith("[DVR-Scan]"):
                self.log.emit(line)

    def _on_stderr(self) -> None:
        assert self._process is not None
        text = bytes(self._process.readAllStandardError()).decode("utf-8", "replace")
        # tqdm uses carriage returns to redraw the same line; split on both.
        for chunk in re.split(r"[\r\n]+", text):
            chunk = chunk.strip()
            if not chunk:
                continue
            match = _PROGRESS_RE.search(chunk)
            if match:
                self.progress.emit(int(match.group(1)))
            else:
                self.log.emit(chunk)

    def _on_error(self, _error: QProcess.ProcessError) -> None:
        if self._process is not None:
            self.failed.emit(f"Failed to run dvr-scan: {self._process.errorString()}")

    def _on_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        process = self._process
        self._process = None
        if status == QProcess.ExitStatus.CrashExit:
            self.failed.emit("The scan was cancelled or the process crashed.")
            return
        if exit_code != 0:
            tail = (process.errorString() if process else "") or "unknown error"
            self.failed.emit(f"dvr-scan exited with code {exit_code}: {tail}")
            return
        # The results are the last stdout line that looks like a timecode CSV.
        # (If no motion was found, DVR-Scan prints no such line and we report
        # an empty list.)
        csv_line = next(
            (
                ln.strip()
                for ln in reversed(self._stdout.splitlines())
                if _CSV_LINE_RE.match(ln.strip())
            ),
            "",
        )
        events = parse_events(csv_line)
        self.progress.emit(100)
        self.finished.emit(events)
