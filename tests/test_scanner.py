"""Tests for timecode handling and DVR-Scan output parsing.

Run with:  pipenv run python -m pytest   (or: python -m unittest)
"""

import unittest

from dvr_scan_gui.scanner import (
    ScanManager,
    ScanOptions,
    ms_to_timecode,
    parse_events,
    timecode_to_ms,
)


class TimecodeTests(unittest.TestCase):
    def test_roundtrip(self):
        for ms in (0, 533, 5067, 3_661_234):
            self.assertEqual(timecode_to_ms(ms_to_timecode(ms)), ms)

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
