from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finalize_transcript import finalize_transcript, timed_blocks


class FinalizeTranscriptTests(unittest.TestCase):
    def test_groups_segments_and_keeps_readable_english_spacing(self) -> None:
        blocks = timed_blocks(
            [
                {"start": 0, "end": 2, "text": "Hello"},
                {"start": 3, "end": 5, "text": "world"},
                {"start": 31, "end": 34, "text": "第二段"},
            ]
        )
        self.assertEqual(blocks, ["[00:00:00] Hello world", "[00:00:31] 第二段"])

    def test_writes_only_validated_timed_final_text(self) -> None:
        segments = [{"start": 2, "end": 6, "text": "这是测试"}]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "transcript.txt"
            with (
                patch("finalize_transcript.create_text_client", return_value=(object(), "test-model", "test")),
                patch("finalize_transcript.call_model", return_value="[00:00:02] 这是测试。"),
            ):
                finalize_transcript(segments, output, "sample.m4a")
            self.assertEqual(output.read_text(encoding="utf-8"), "[00:00:02] 这是测试。\n")

    def test_rejects_changed_timestamp_markers(self) -> None:
        segments = [{"start": 2, "end": 6, "text": "这是测试"}]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "transcript.txt"
            with (
                patch("finalize_transcript.create_text_client", return_value=(object(), "test-model", "test")),
                patch("finalize_transcript.call_model", return_value="[00:00:03] 这是测试。"),
            ):
                with self.assertRaises(RuntimeError):
                    finalize_transcript(segments, output, "sample.m4a")
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
