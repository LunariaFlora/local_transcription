from __future__ import annotations

import argparse
from typing import Any

from settings import DEFAULT_SETTINGS, load_settings, save_settings


def parse_value(key: str, value: str) -> Any:
    default = DEFAULT_SETTINGS[key]
    if isinstance(default, bool):
        return value.lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(default, int):
        return int(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Update persistent tool settings.")
    parser.add_argument("key", choices=sorted(DEFAULT_SETTINGS))
    parser.add_argument("value")
    args = parser.parse_args()

    settings = load_settings()
    settings[args.key] = parse_value(args.key, args.value)
    save_settings(settings)
    print(f"{args.key} = {settings[args.key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
