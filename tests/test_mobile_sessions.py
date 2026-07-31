from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src import app_server


class MobileSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        app_server.MOBILE_SESSIONS.clear()

    def tearDown(self) -> None:
        app_server.MOBILE_SESSIONS.clear()

    def test_new_mobile_session_does_not_expose_desktop_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inputs = root / "inputs"
            outputs = root / "outputs"
            inputs.mkdir()
            (outputs / "old-recording").mkdir(parents=True)
            (inputs / "old-recording.m4a").write_bytes(b"old")
            (outputs / "old-recording" / "transcript.txt").write_text("old", encoding="utf-8")

            with (
                patch.object(app_server, "INPUTS_DIR", inputs),
                patch.object(app_server, "OUTPUTS_DIR", outputs),
            ):
                state = app_server.mobile_session_state("session_1234567890")

            self.assertEqual(state["inputs"], [])
            self.assertEqual(state["outputs"], [])

    def test_mobile_session_only_returns_owned_upload_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inputs = root / "inputs"
            outputs = root / "outputs"
            inputs.mkdir()
            outputs.mkdir()
            session_id = "session_abcdefghij"
            session = app_server.mobile_session(session_id)
            session["created_at"] = time.time() - 1
            session["inputs"] = {"phone-audio.m4a": "手机录音.m4a"}

            (inputs / "phone-audio.m4a").write_bytes(b"audio")
            result = outputs / "phone-audio"
            result.mkdir()
            (result / "transcript.txt").write_text("文字稿", encoding="utf-8")

            with (
                patch.object(app_server, "INPUTS_DIR", inputs),
                patch.object(app_server, "OUTPUTS_DIR", outputs),
            ):
                state = app_server.mobile_session_state(session_id)

            self.assertEqual([row["name"] for row in state["inputs"]], ["phone-audio.m4a"])
            self.assertEqual(state["inputs"][0]["display_name"], "手机录音.m4a")
            self.assertEqual([row["name"] for row in state["outputs"]], ["phone-audio"])
            self.assertEqual(state["outputs"][0]["display_name"], "手机录音")

    def test_mobile_upload_uses_unique_internal_name_on_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            inputs = root / "inputs"
            outputs = root / "outputs"
            inputs.mkdir()
            outputs.mkdir()
            (inputs / "recording.m4a").write_bytes(b"existing")

            with (
                patch.object(app_server, "INPUTS_DIR", inputs),
                patch.object(app_server, "OUTPUTS_DIR", outputs),
            ):
                name = app_server.mobile_upload_name("recording.m4a", "session_abcdefghij")

            self.assertEqual(name, "recording--phone-session_.m4a")


if __name__ == "__main__":
    unittest.main()
