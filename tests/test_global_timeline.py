"""Tests for the global-timeline day-segment layout and overlap detection."""

import unittest
from datetime import date, datetime

from dvr_scan_gui.file_list import FileEntry
from dvr_scan_gui.main_window import compute_day_segments

DAY = date(2026, 6, 26)


def _entry(path, start, duration_ms):
    return FileEntry(
        path=path, recording_start=start, duration_ms=duration_ms, start_probed=True
    )


class DaySegmentTests(unittest.TestCase):
    def test_sorted_by_start_and_clipped_to_day(self):
        entries = [
            _entry("b.mp4", datetime(2026, 6, 26, 2, 0, 0), 60_000),
            _entry("a.mp4", datetime(2026, 6, 26, 1, 0, 0), 60_000),
        ]
        segments, overlaps = compute_day_segments(entries, DAY)
        self.assertEqual([s[0] for s in segments], ["a.mp4", "b.mp4"])
        self.assertEqual(segments[0], ("a.mp4", 3_600_000, 3_660_000))
        self.assertEqual(overlaps, 0)

    def test_skips_files_without_clock_and_other_days(self):
        entries = [
            _entry("noclock.mp4", None, 60_000),
            _entry("otherday.mp4", datetime(2026, 6, 25, 1, 0, 0), 60_000),
            _entry("today.mp4", datetime(2026, 6, 26, 5, 0, 0), 60_000),
        ]
        segments, _ = compute_day_segments(entries, DAY)
        self.assertEqual([s[0] for s in segments], ["today.mp4"])

    def test_overlap_longer_than_5s_flagged(self):
        # a.mp4 runs 01:00:00–01:01:00; b.mp4 starts at 01:00:50 (10 s overlap).
        entries = [
            _entry("a.mp4", datetime(2026, 6, 26, 1, 0, 0), 60_000),
            _entry("b.mp4", datetime(2026, 6, 26, 1, 0, 50), 60_000),
        ]
        _, overlaps = compute_day_segments(entries, DAY)
        self.assertEqual(overlaps, 1)

    def test_short_overlap_ignored(self):
        # 2 s overlap — under the 5 s threshold, so not flagged.
        entries = [
            _entry("a.mp4", datetime(2026, 6, 26, 1, 0, 0), 60_000),
            _entry("b.mp4", datetime(2026, 6, 26, 1, 0, 58), 60_000),
        ]
        _, overlaps = compute_day_segments(entries, DAY)
        self.assertEqual(overlaps, 0)

    def test_manual_start_used_when_metadata_absent(self):
        entry = FileEntry(path="m.mp4", duration_ms=60_000, start_probed=True)
        entry.manual_start = datetime(2026, 6, 26, 3, 0, 0)
        segments, _ = compute_day_segments([entry], DAY)
        self.assertEqual(segments[0], ("m.mp4", 10_800_000, 10_860_000))

    def test_file_spanning_midnight_clipped(self):
        # Starts 23:59:00 the day before, runs 5 min → only the post-midnight
        # portion lands on DAY, clipped at 0.
        entry = _entry("x.mp4", datetime(2026, 6, 25, 23, 59, 0), 300_000)
        segments, _ = compute_day_segments([entry], DAY)
        self.assertEqual(segments[0][1], 0)
        self.assertEqual(segments[0][2], 240_000)  # 4 min into the day
