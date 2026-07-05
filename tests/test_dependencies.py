"""Tests for external-tool resolution (bundle-first, then PATH)."""

import os
import stat
import tempfile
import unittest
from unittest import mock

from dvr_scan_gui import dependencies


class FindToolTests(unittest.TestCase):
    def test_falls_back_to_path(self):
        with mock.patch.object(dependencies, "_bundle_dirs", return_value=[]), \
             mock.patch("shutil.which", return_value="/usr/bin/ffprobe") as which:
            self.assertEqual(dependencies.find_tool("ffprobe"), "/usr/bin/ffprobe")
            which.assert_called_once_with("ffprobe")

    def test_returns_none_when_absent(self):
        with mock.patch.object(dependencies, "_bundle_dirs", return_value=[]), \
             mock.patch("shutil.which", return_value=None):
            self.assertIsNone(dependencies.find_tool("dvr-scan"))

    def test_bundle_is_preferred_over_path(self):
        with tempfile.TemporaryDirectory() as bundle:
            name = "ffprobe.exe" if dependencies.IS_WINDOWS else "ffprobe"
            path = os.path.join(bundle, name)
            with open(path, "w") as fh:
                fh.write("")
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
            with mock.patch.object(dependencies, "_bundle_dirs", return_value=[bundle]), \
                 mock.patch("shutil.which", return_value="/usr/bin/ffprobe"):
                self.assertEqual(dependencies.find_tool("ffprobe"), path)
                self.assertTrue(dependencies.is_bundled(path))


class StatusTests(unittest.TestCase):
    def test_missing_required_and_optional_partitioned(self):
        def fake_find(name):
            return "/usr/bin/ffprobe" if name == "ffprobe" else None

        with mock.patch.object(dependencies, "find_tool", side_effect=fake_find):
            statuses = dependencies.check_all()
            req = dependencies.missing_required(statuses)
            opt = dependencies.missing_optional(statuses)
        self.assertEqual([s.tool.key for s in req], ["dvr-scan"])
        self.assertEqual(opt, [])  # ffprobe was found

    def test_no_window_kwargs_shape(self):
        kwargs = dependencies.no_window_kwargs()
        if dependencies.IS_WINDOWS:
            self.assertIn("creationflags", kwargs)
        else:
            self.assertEqual(kwargs, {})


if __name__ == "__main__":
    unittest.main()
