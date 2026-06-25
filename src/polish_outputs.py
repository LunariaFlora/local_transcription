from __future__ import annotations

import argparse
import sys
from pathlib import Path

from polish_transcript import polish_transcript_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polish all transcript.txt files under outputs.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--model")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    transcripts = sorted(args.output_dir.glob("*/transcript.txt"))
    if not transcripts:
        print(f"No transcript.txt files found under: {args.output_dir.resolve()}")
        return 0

    failed: list[Path] = []
    for index, transcript in enumerate(transcripts, start=1):
        print("")
        print(f"===== AI polish [{index}/{len(transcripts)}] {transcript.parent.name} =====")
        try:
            polish_transcript_file(transcript, args.model, args.force)
        except Exception as exc:
            failed.append(transcript)
            print(f"Failed: {transcript}: {exc}", file=sys.stderr)

    if failed:
        print("")
        print("Finished with AI polish failures:")
        for path in failed:
            print(f"- {path}")
        return 1

    print("")
    print("All transcripts polished successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
