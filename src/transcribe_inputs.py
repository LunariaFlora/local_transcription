from __future__ import annotations

import argparse
import sys
from pathlib import Path

from transcribe_local import is_supported_media_path
from transcribe_local import load_whisper_model
from transcribe_local import transcribe_preloaded_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe every media file in the inputs folder.")
    parser.add_argument("--input-dir", type=Path, default=Path("inputs"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("-m", "--model", default="small")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--files", nargs="*", default=[], help="Only process these file names from input-dir.")
    parser.add_argument("--fast", action="store_true", help="Use beam size 1 for faster transcription.")
    parser.add_argument("--no-vad", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-transcribe files even when outputs already exist.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir
    output_dir = args.output_dir
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    selected = set(args.files)
    files = [
        path
        for path in sorted(input_dir.iterdir())
        if path.is_file() and is_supported_media_path(path)
        and (not selected or path.name in selected)
    ]

    if not files:
        print(f"No audio/video files found in: {input_dir.resolve()}")
        print("Put files into the inputs folder, then run this command again.")
        return 0

    print(f"Found {len(files)} file(s) in {input_dir.resolve()}")
    failed: list[Path] = []
    skipped: list[Path] = []
    language = None if args.language.lower() == "auto" else args.language
    pending_files: list[Path] = []

    for input_path in files:
        file_output_dir = output_dir / input_path.stem
        expected_outputs = [
            file_output_dir / "transcript.txt",
            file_output_dir / "transcript.srt",
            file_output_dir / "transcript.json",
        ]
        if not args.force and all(path.exists() for path in expected_outputs):
            skipped.append(input_path)
        else:
            pending_files.append(input_path)

    if skipped:
        print(f"Skipping {len(skipped)} already processed file(s). Use --force to re-run.")

    if not pending_files:
        print("No pending files to transcribe.")
        return 0

    beam_size = 1 if args.fast else args.beam_size

    print(f"Loading model once: {args.model} ({args.device}, {args.compute_type}, beam={beam_size})")
    model = load_whisper_model(args.model, device=args.device, compute_type=args.compute_type)

    for index, input_path in enumerate(pending_files, start=1):
        print("")
        print(f"===== [{index}/{len(pending_files)}] {input_path.name} =====")
        try:
            transcribe_preloaded_model(
                input_path=input_path,
                output_dir=output_dir,
                model=model,
                language=language,
                beam_size=beam_size,
                vad_filter=not args.no_vad,
            )
        except Exception as exc:
            failed.append(input_path)
            print(f"Failed: {input_path.name}: {exc}", file=sys.stderr)

    print("")
    if failed:
        print("Finished with failures:")
        for path in failed:
            print(f"- {path}")
        return 1

    print("All pending files transcribed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
