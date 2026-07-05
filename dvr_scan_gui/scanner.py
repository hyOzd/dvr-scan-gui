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

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QObject, QProcess, Signal

from .dependencies import find_tool, no_window_kwargs


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


def read_recording_info(path: str) -> tuple[datetime | None, int | None]:
    """Return ``(recording_start, duration_ms)`` for a video, either ``None``.

    Reads the container's *creation_time* tag and *duration* in a single
    ``ffprobe`` call. MP4 / QuickTime creation times are stored in UTC (ffprobe
    emits them as ISO-8601 with a trailing ``Z``), so the value is converted to
    local time, giving the actual moment the clip began. The start is ``None``
    when ffprobe is missing, the tag is absent, or it holds the QuickTime
    zero-date sentinel (1904); the duration is ``None`` when unreadable.
    """
    exe = find_tool("ffprobe")
    if not exe:
        return None, None
    try:
        proc = subprocess.run(
            [exe, "-v", "error",
             "-show_entries", "format=duration:format_tags=creation_time",
             "-of", "json", path],
            capture_output=True, text=True, timeout=10,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    try:
        data = json.loads(proc.stdout)
    except (ValueError, json.JSONDecodeError):
        return None, None
    fmt = data.get("format", {}) if isinstance(data, dict) else {}
    tags = fmt.get("tags", {}) if isinstance(fmt, dict) else {}
    start = _parse_create_date(tags.get("creation_time"))
    duration_ms = _parse_duration(fmt.get("duration"))
    # Prefer the SMPTE start timecode's time-of-day when present. Split-clip
    # cameras (notably GoPro) stamp every part of one recording with the *same*
    # creation_time but a timecode that advances per part, so the timecode
    # pinpoints each part's real start where the create date cannot. We keep the
    # create date's calendar day and replace only the time-of-day.
    if start is not None:
        timecode = read_timecode(path)
        if timecode is not None:
            hour, minute, second = timecode
            start = start.replace(hour=hour, minute=minute, second=second, microsecond=0)
    return start, duration_ms


def read_timecode(path: str) -> tuple[int, int, int] | None:
    """Return ``(hour, minute, second)`` from the file's SMPTE start timecode.

    Read via ``ffprobe`` from the format/stream ``timecode`` tags. Returns
    ``None`` when ffprobe is missing, there is no timecode, or it can't be
    parsed."""
    exe = find_tool("ffprobe")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-v", "error",
             "-show_entries", "format_tags=timecode:stream_tags=timecode",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_timecode(proc.stdout)


def _parse_timecode(text: str) -> tuple[int, int, int] | None:
    """Parse an ``HH:MM:SS:FF`` (or drop-frame ``HH:MM:SS;FF``) timecode into a
    ``(hour, minute, second)`` time-of-day (the frame field is ignored)."""
    match = re.search(r"(\d{2}):(\d{2}):(\d{2})[:;]\d{2}", text or "")
    if not match:
        return None
    hour, minute, second = (int(group) for group in match.groups())
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour, minute, second


def read_recording_start(path: str) -> datetime | None:
    """Return the real wall-clock time the recording started, or ``None``."""
    return read_recording_info(path)[0]


def _parse_create_date(value) -> datetime | None:
    """Parse ffprobe's ``creation_time`` (ISO-8601 UTC) into naive local time.

    ffprobe emits e.g. ``2025-06-01T11:18:54.000000Z``; the instant is in UTC,
    so it is converted to the local wall-clock time the recording began and the
    tzinfo dropped (the rest of the app works in naive-local datetimes).
    """
    if not value:
        return None
    s = str(value).strip()
    if not s or s.upper() == "N/A":
        return None
    start = None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            start = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    if start is None:
        return None
    start = start.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    # 1904-01-01 is QuickTime's epoch, written when no real date is set.
    if start.year < 1990:
        return None
    return start


def _parse_duration(value) -> int | None:
    """Parse an exiftool Duration value into milliseconds.

    Handles both forms exiftool emits: ``"14.72 s"`` (seconds with a unit) and
    ``"H:MM:SS"`` / ``"MM:SS"`` (clock form), plus a bare numeric of seconds.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("s") and ":" not in s:
        try:
            return int(round(float(s[:-1].strip()) * 1000))
        except ValueError:
            return None
    try:
        if ":" in s:
            total = 0.0
            for part in s.split(":"):
                total = total * 60 + float(part)
            return int(round(total * 1000))
        return int(round(float(s) * 1000))
    except ValueError:
        return None


def ms_to_realtime(start: datetime, ms: int) -> str:
    """Format a playback offset as a real ``YYYY-MM-DD HH:MM:SS.mmm`` clock
    time, by adding the offset to the recording start."""
    t = start + timedelta(milliseconds=max(0, int(ms)))
    return t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d}"


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


def find_executable() -> str | None:
    """Locate the dvr-scan executable, or None if it is not installed."""
    return find_tool("dvr-scan")


class ScanWorker(QObject):
    """Runs ``dvr-scan`` for a *single* file asynchronously via :class:`QProcess`.

    One worker owns one process and is used once. Signals:
        progress(int):    0-100 percentage parsed from stderr.
        log(str):         informative dvr-scan lines.
        finished(list):   list[MotionEvent] on success.
        failed(str):      human-readable error message.
        cancelled():      the worker was cancelled by the caller.
    """

    progress = Signal(int)
    log = Signal(str)
    finished = Signal(list)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, options: ScanOptions, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._options = options
        self._process: QProcess | None = None
        self._stdout = ""
        self._cancelled = False

    @property
    def is_running(self) -> bool:
        return self._process is not None

    def start(self) -> None:
        exe = find_executable()
        if not exe:
            self.failed.emit(
                "Could not find the 'dvr-scan' executable on PATH. "
                "Install it with 'pip install dvr-scan'."
            )
            return

        self._stdout = ""
        self._process = QProcess(self)
        self._process.setProgram(exe)
        self._process.setArguments(self._options.to_args())
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._process.start()

    def cancel(self) -> None:
        self._cancelled = True
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
        if self._cancelled:
            return
        if self._process is not None:
            self.failed.emit(f"Failed to run dvr-scan: {self._process.errorString()}")

    def _on_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        process = self._process
        self._process = None
        if self._cancelled:
            self.cancelled.emit()
            return
        if status == QProcess.ExitStatus.CrashExit:
            self.failed.emit("The scan process crashed.")
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


class ScanManager(QObject):
    """Queues per-file scans and runs up to ``max_concurrent`` at a time.

    Files are started in the order they are enqueued, but several may run
    concurrently to use multiple CPU cores. Every signal is keyed by the file
    path so the UI can route updates to the right row.

    Signals:
        started(str):          a file's scan began.
        progress(str, int):    0-100 progress for a file.
        log(str, str):         an informative line for a file.
        finished(str, list):   a file finished with list[MotionEvent].
        failed(str, str):      a file failed with an error message.
        cancelled(str):        a file's scan was cancelled.
        queueChanged():        the queue/running set changed.
        idle():                nothing is running or queued any more.
    """

    started = Signal(str)
    progress = Signal(str, int)
    log = Signal(str, str)
    finished = Signal(str, list)
    failed = Signal(str, str)
    cancelled = Signal(str)
    queueChanged = Signal()
    idle = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._max_concurrent = 1
        self._queue: list[tuple[str, ScanOptions]] = []
        self._workers: dict[str, ScanWorker] = {}

    @staticmethod
    def executable() -> str | None:
        return find_executable()

    def set_max_concurrent(self, n: int) -> None:
        self._max_concurrent = max(1, int(n))
        self._pump()

    def max_concurrent(self) -> int:
        return self._max_concurrent

    def is_active(self) -> bool:
        return bool(self._workers or self._queue)

    def is_running(self, key: str) -> bool:
        return key in self._workers

    def is_queued(self, key: str) -> bool:
        return any(k == key for k, _ in self._queue)

    def running_keys(self) -> list[str]:
        return list(self._workers)

    def queued_keys(self) -> list[str]:
        return [k for k, _ in self._queue]

    def enqueue(self, key: str, options: ScanOptions) -> None:
        """Queue (or re-queue) a file for scanning, preserving order."""
        if key in self._workers:
            return  # already running; ignore
        self._queue = [(k, o) for k, o in self._queue if k != key]
        self._queue.append((key, options))
        self.queueChanged.emit()
        self._pump()

    def cancel_all(self) -> None:
        """Drop all queued scans and kill every running one."""
        cancelled = self.queued_keys()
        self._queue.clear()
        for key in cancelled:
            self.cancelled.emit(key)
        for worker in list(self._workers.values()):
            worker.cancel()  # each emits cancelled() -> _on_cancelled
        self.queueChanged.emit()
        if not self._workers and not self._queue:
            self.idle.emit()

    def _pump(self) -> None:
        while self._queue and len(self._workers) < self._max_concurrent:
            key, options = self._queue.pop(0)
            worker = ScanWorker(options, self)
            self._workers[key] = worker
            worker.progress.connect(lambda v, k=key: self.progress.emit(k, v))
            worker.log.connect(lambda m, k=key: self.log.emit(k, m))
            worker.finished.connect(lambda ev, k=key: self._on_finished(k, ev))
            worker.failed.connect(lambda msg, k=key: self._on_failed(k, msg))
            worker.cancelled.connect(lambda k=key: self._on_cancelled(k))
            self.started.emit(key)
            self.queueChanged.emit()
            worker.start()

    def _retire(self, key: str) -> None:
        worker = self._workers.pop(key, None)
        if worker is not None:
            worker.deleteLater()

    def _on_finished(self, key: str, events: list) -> None:
        self._retire(key)
        self.finished.emit(key, events)
        self._after_worker_done()

    def _on_failed(self, key: str, message: str) -> None:
        self._retire(key)
        self.failed.emit(key, message)
        self._after_worker_done()

    def _on_cancelled(self, key: str) -> None:
        self._retire(key)
        self.cancelled.emit(key)
        self._after_worker_done()

    def _after_worker_done(self) -> None:
        self.queueChanged.emit()
        self._pump()
        if not self._workers and not self._queue:
            self.idle.emit()
