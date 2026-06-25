from __future__ import annotations

import argparse
import json
from pathlib import Path

from transcribe_local import probe_media


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect audio/video streams with ffprobe.")
    parser.add_argument("input", type=Path, help="Media file to inspect.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    probe = probe_media(args.input)
    media_format = probe.get("format", {})
    streams = probe.get("streams", [])

    print(f"File: {args.input}")
    print(f"Format: {media_format.get('format_name', 'unknown')}")
    print(f"Duration: {media_format.get('duration', 'unknown')} seconds")
    print(f"Size: {media_format.get('size', 'unknown')} bytes")
    print("")

    if not streams:
        print("No streams found.")
        return 0

    for stream in streams:
        index = stream.get("index", "?")
        codec_type = stream.get("codec_type", "unknown")
        codec_name = stream.get("codec_name", "unknown")
        duration = stream.get("duration", "unknown")
        channels = stream.get("channels")
        sample_rate = stream.get("sample_rate")
        width = stream.get("width")
        height = stream.get("height")

        details = []
        if width and height:
            details.append(f"{width}x{height}")
        if sample_rate:
            details.append(f"{sample_rate} Hz")
        if channels:
            details.append(f"{channels} channel(s)")

        suffix = f" ({', '.join(details)})" if details else ""
        print(f"Stream #{index}: {codec_type}, {codec_name}, duration={duration}{suffix}")

    print("")
    print("Raw ffprobe JSON:")
    print(json.dumps(probe, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
