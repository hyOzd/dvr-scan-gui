"""Tests for timecode handling and DVR-Scan output parsing.

Run with:  pipenv run python -m pytest   (or: python -m unittest)
"""

import os
import time
import unittest
from datetime import datetime

from dvr_scan_gui.scanner import (
    ScanManager,
    ScanOptions,
    _parse_create_date,
    _parse_duration,
    _parse_timecode,
    ms_to_timecode,
    parse_events,
    timecode_to_ms,
)


class TimecodeTests(unittest.TestCase):
    def test_roundtrip(self):
        for ms in (0, 533, 5067, 3_661_234):
            self.assertEqual(timecode_to_ms(ms_to_timecode(ms)), ms)


class DurationParseTests(unittest.TestCase):
    def test_seconds_with_unit(self):
        self.assertEqual(_parse_duration("14.72 s"), 14_720)
        self.assertEqual(_parse_duration("0.50 s"), 500)

    def test_clock_form(self):
        self.assertEqual(_parse_duration("0:00:14"), 14_000)
        self.assertEqual(_parse_duration("1:02:03"), 3_723_000)
        self.assertEqual(_parse_duration("02:30"), 150_000)

    def test_bare_seconds(self):
        self.assertEqual(_parse_duration("14.72"), 14_720)

    def test_ffprobe_float_seconds(self):
        # ffprobe emits format.duration as a bare float string.
        self.assertEqual(_parse_duration("14.719771"), 14_720)
        self.assertEqual(_parse_duration("707.712000"), 707_712)

    def test_unparseable_or_missing(self):
        self.assertIsNone(_parse_duration(None))
        self.assertIsNone(_parse_duration(""))
        self.assertIsNone(_parse_duration("n/a"))


class CreateDateParseTests(unittest.TestCase):
    """ffprobe emits creation_time as ISO-8601 UTC; we convert to local time."""

    def _with_tz(self, tz, value):
        if not hasattr(time, "tzset"):
            self.skipTest("time.tzset unavailable on this platform")
        old = os.environ.get("TZ")
        os.environ["TZ"] = tz
        time.tzset()
        try:
            return _parse_create_date(value)
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_utc_converted_to_local(self):
        # Istanbul is a fixed UTC+3 (no DST since 2016): 22:18Z -> 01:18 next day.
        self.assertEqual(
            self._with_tz("Europe/Istanbul", "2026-06-25T22:18:53.000000Z"),
            datetime(2026, 6, 26, 1, 18, 53),
        )

    def test_utc_zone_is_identity(self):
        self.assertEqual(
            self._with_tz("UTC", "2025-06-01T11:18:54.000000Z"),
            datetime(2025, 6, 1, 11, 18, 54),
        )

    def test_without_fractional_seconds(self):
        self.assertEqual(
            self._with_tz("UTC", "2025-06-01T11:18:54Z"),
            datetime(2025, 6, 1, 11, 18, 54),
        )

    def test_sentinel_and_missing_rejected(self):
        self.assertIsNone(_parse_create_date(None))
        self.assertIsNone(_parse_create_date(""))
        self.assertIsNone(_parse_create_date("N/A"))
        self.assertIsNone(_parse_create_date("not a date"))
        # QuickTime zero-date sentinel is discarded.
        self.assertIsNone(self._with_tz("UTC", "1904-01-01T00:00:00.000000Z"))


class TimecodeMetadataTests(unittest.TestCase):
    def test_smpte_non_drop(self):
        self.assertEqual(_parse_timecode("11:30:00:56"), (11, 30, 0))

    def test_smpte_drop_frame(self):
        self.assertEqual(_parse_timecode("01:02:03;15"), (1, 2, 3))

    def test_picks_first_in_multiline_output(self):
        self.assertEqual(_parse_timecode("11:18:13:56\n11:18:13:56\n"), (11, 18, 13))

    def test_rejects_garbage_and_out_of_range(self):
        self.assertIsNone(_parse_timecode(""))
        self.assertIsNone(_parse_timecode(None))
        self.assertIsNone(_parse_timecode("not a timecode"))
        self.assertIsNone(_parse_timecode("99:99:99:99"))

    def test_parse_known_timecodes(self):
        self.assertEqual(timecode_to_ms("00:00:00.533"), 533)
        self.assertEqual(timecode_to_ms("00:00:05.067"), 5067)
        self.assertEqual(timecode_to_ms("01:02:03.000"), 3_723_000)


class ParseEventsTests(unittest.TestCase):
    def test_pairs_into_events(self):
        line = "00:00:00.533,00:00:05.067,00:00:05.133,00:00:09.067"
        events = parse_events(line)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].start_ms, 533)
        self.assertEqual(events[0].end_ms, 5067)
        self.assertEqual(events[0].duration_ms, 4534)
        self.assertEqual(events[1].index, 2)
        self.assertEqual(events[1].start_ms, 5133)

    def test_empty(self):
        self.assertEqual(parse_events(""), [])
        self.assertEqual(parse_events("\n"), [])

    def test_odd_trailing_token_ignored(self):
        events = parse_events("00:00:01.000,00:00:02.000,00:00:03.000")
        self.assertEqual(len(events), 1)


class ScanOptionsTests(unittest.TestCase):
    def test_scan_only_present_and_not_quiet(self):
        # scan-only (no clip output) is required; --quiet must NOT be passed,
        # since it would suppress the progress bar we parse from stderr.
        args = ScanOptions(input_path="x.mp4").to_args()
        self.assertIn("--scan-only", args)
        self.assertNotIn("--quiet", args)
        self.assertNotIn("-q", args)
        self.assertEqual(args[args.index("-i") + 1], "x.mp4")

    def test_duration_overrides_end_time(self):
        args = ScanOptions(
            input_path="x.mp4", end_time="00:01:00", duration="30s"
        ).to_args()
        self.assertIn("-dt", args)
        self.assertNotIn("-et", args)

    def test_region_points_flattened_after_a_flag(self):
        args = ScanOptions(
            input_path="x.mp4",
            regions=[[(10, 20), (110, 20), (110, 220), (10, 220)]],
        ).to_args()
        i = args.index("-a")
        self.assertEqual(
            args[i + 1 : i + 9],
            ["10", "20", "110", "20", "110", "220", "10", "220"],
        )

    def test_multiple_regions_each_get_their_own_a_flag(self):
        args = ScanOptions(
            input_path="x.mp4",
            regions=[
                [(0, 0), (10, 0), (10, 10)],
                [(20, 20), (30, 20), (30, 30), (20, 30)],
            ],
        ).to_args()
        self.assertEqual(args.count("-a"), 2)

    def test_no_region_flag_without_points(self):
        self.assertNotIn("-a", ScanOptions(input_path="x.mp4").to_args())
        # Fewer than 3 points is not a valid polygon and must be skipped.
        self.assertNotIn(
            "-a",
            ScanOptions(input_path="x.mp4", regions=[[(1, 2), (3, 4)]]).to_args(),
        )


class ScanManagerQueueTests(unittest.TestCase):
    """Queue ordering/dedup logic, with process launching stubbed out."""

    def _manager(self) -> ScanManager:
        manager = ScanManager()
        manager._pump = lambda: None  # don't actually launch dvr-scan
        return manager

    def test_enqueue_preserves_order(self):
        m = self._manager()
        m.enqueue("a.mp4", ScanOptions(input_path="a.mp4"))
        m.enqueue("b.mp4", ScanOptions(input_path="b.mp4"))
        m.enqueue("c.mp4", ScanOptions(input_path="c.mp4"))
        self.assertEqual(m.queued_keys(), ["a.mp4", "b.mp4", "c.mp4"])
        self.assertTrue(m.is_active())

    def test_requeue_moves_to_end_without_duplicating(self):
        m = self._manager()
        m.enqueue("a.mp4", ScanOptions(input_path="a.mp4"))
        m.enqueue("b.mp4", ScanOptions(input_path="b.mp4"))
        m.enqueue("a.mp4", ScanOptions(input_path="a.mp4"))
        self.assertEqual(m.queued_keys(), ["b.mp4", "a.mp4"])

    def test_cancel_all_clears_queue(self):
        m = self._manager()
        m.enqueue("a.mp4", ScanOptions(input_path="a.mp4"))
        m.cancel_all()
        self.assertEqual(m.queued_keys(), [])
        self.assertFalse(m.is_active())


if __name__ == "__main__":
    unittest.main()
