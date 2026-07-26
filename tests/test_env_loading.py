from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.polish_transcript import load_dotenv


class LoadDotenvTests(unittest.TestCase):
    def test_accepts_plain_and_export_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "CODEX_TEST_PLAIN=plain-value\n"
                "export CODEX_TEST_EXPORTED=exported-value\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CODEX_TEST_PLAIN", None)
                os.environ.pop("CODEX_TEST_EXPORTED", None)
                load_dotenv(env_path)
                self.assertEqual(os.environ["CODEX_TEST_PLAIN"], "plain-value")
                self.assertEqual(os.environ["CODEX_TEST_EXPORTED"], "exported-value")


if __name__ == "__main__":
    unittest.main()
