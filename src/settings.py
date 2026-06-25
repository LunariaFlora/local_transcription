from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SETTINGS_PATH = Path("settings.json")

DEFAULT_SETTINGS: dict[str, Any] = {
    "watch_downloads": False,
    "api_auto_polish": False,
    "auto_title_rename": True,
    "local_beam_size": 2,
    "auto_close_after_task": False,
    "auto_close_minutes": 30,
}


def load_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)

    try:
        loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        loaded = {}

    settings = dict(DEFAULT_SETTINGS)
    settings.update({key: value for key, value in loaded.items() if key in DEFAULT_SETTINGS})
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    SETTINGS_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def print_settings() -> None:
    settings = load_settings()
    print(json.dumps(settings, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    print_settings()
