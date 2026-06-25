from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from transcribe_local import is_supported_media_path, transcribe_preloaded_model
from transcribe_local import load_whisper_model


STATE_FILE = ".download_ingest_state.json"
PARTIAL_SUFFIXES = {
    ".crdownload",
    ".download",
    ".part",
    ".tmp",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch a download folder and transcribe new media files.")
    parser.add_argument("--source-dir", type=Path, default=Path.home() / "Downloads")
    parser.add_argument("--input-dir", type=Path, default=Path("inputs"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("-m", "--model", default="small")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--fast", action="store_true", help="Use beam size 1 for faster transcription.")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="Scan once and exit.")
    parser.add_argument("--one-file", action="store_true", help="Transcribe one new file, then exit.")
    parser.add_argument("--max-files", type=int, help="Transcribe up to this many new files, then exit.")
    parser.add_argument("--max-minutes", type=float, help="Stop watching after this many minutes.")
    parser.add_argument("--pause-between-files", action="store_true", help="Pause after each processed file.")
    parser.add_argument("--ignore-existing", action="store_true", help="Only process files created or changed after startup.")
    parser.add_argument("--force", action="store_true", help="Copy/transcribe files even if they were seen before.")
    return parser.parse_args()


def load_state(path: Path) -> dict[str, dict[str, float | int]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, state: dict[str, dict[str, float | int]]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def is_partial_download(path: Path) -> bool:
    return path.suffix.lower() in PARTIAL_SUFFIXES


def is_stable_file(path: Path, wait_seconds: float = 1.0) -> bool:
    try:
        first = path.stat().st_size
        time.sleep(wait_seconds)
        second = path.stat().st_size
    except OSError:
        return False
    return first > 0 and first == second


def state_key(path: Path) -> str:
    return str(path.resolve()).lower()


def file_signature(path: Path) -> dict[str, float | int]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime": stat.st_mtime}


def already_seen(
    state: dict[str, dict[str, float | int]],
    path: Path,
    force: bool,
) -> bool:
    if force:
        return False
    key = state_key(path)
    return state.get(key) == file_signature(path)


def unique_destination(input_dir: Path, source: Path) -> Path:
    candidate = input_dir / source.name
    if not candidate.exists():
        return candidate

    suffix = source.suffix
    stem = source.stem if suffix else source.name
    for index in range(2, 10_000):
        if suffix:
            candidate = input_dir / f"{stem} ({index}){suffix}"
        else:
            candidate = input_dir / f"{stem} ({index})"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not create a unique destination for: {source.name}")


def expected_outputs_exist(output_dir: Path, input_path: Path) -> bool:
    file_output_dir = output_dir / input_path.stem
    return all(
        path.exists()
        for path in [
            file_output_dir / "transcript.txt",
            file_output_dir / "transcript.srt",
            file_output_dir / "transcript.json",
        ]
    )


def iter_candidate_files(source_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(source_dir.iterdir())
        if path.is_file()
        and not is_partial_download(path)
        and is_supported_media_path(path)
    ]


def main() -> int:
    args = parse_args()
    source_dir = args.source_dir
    input_dir = args.input_dir
    output_dir = args.output_dir
    state_path = Path(STATE_FILE)

    if not source_dir.exists():
        print(f"Source folder does not exist: {source_dir}", file=sys.stderr)
        return 1

    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    state = load_state(state_path)
    language = None if args.language.lower() == "auto" else args.language
    started_at = time.time()
    deadline = started_at + (args.max_minutes * 60) if args.max_minutes else None
    processed_count = 0

    print(f"Watching: {source_dir.resolve()}")
    print(f"Copying to: {input_dir.resolve()}")
    print(f"Writing outputs to: {output_dir.resolve()}")
    print("Press Ctrl+C to stop watching manually.")
    if args.max_minutes:
        print(f"Auto-stop after {args.max_minutes:g} minute(s).")
    beam_size = 1 if args.fast else args.beam_size

    print(f"Loading model once: {args.model} ({args.device}, {args.compute_type}, beam={beam_size})")
    model = load_whisper_model(args.model, device=args.device, compute_type=args.compute_type)

    while True:
        if deadline is not None and time.time() >= deadline:
            print("")
            print(f"Time limit reached: {args.max_minutes:g} minute(s). Exiting.")
            return 0

        for source in iter_candidate_files(source_dir):
            if deadline is not None and time.time() >= deadline:
                print("")
                print(f"Time limit reached: {args.max_minutes:g} minute(s). Exiting.")
                return 0

            if args.ignore_existing and source.stat().st_mtime < started_at:
                continue

            if already_seen(state, source, args.force):
                continue

            if not is_stable_file(source):
                continue

            destination = unique_destination(input_dir, source)
            print("")
            print(f"New media: {source}")
            print(f"Copying: {destination}")
            shutil.copy2(source, destination)

            if expected_outputs_exist(output_dir, destination) and not args.force:
                print("Output already exists, skipping transcription.")
            else:
                try:
                    transcribe_preloaded_model(
                        input_path=destination,
                        output_dir=output_dir,
                        model=model,
                        language=language,
                        beam_size=beam_size,
                        vad_filter=True,
                    )
                except Exception as exc:
                    print(f"Failed to transcribe {destination.name}: {exc}", file=sys.stderr)

            state[state_key(source)] = file_signature(source)
            save_state(state_path, state)
            processed_count += 1

            if args.one_file:
                print("")
                print("One-file mode complete. Exiting.")
                return 0

            if args.max_files is not None and processed_count >= args.max_files:
                print("")
                print(f"Batch limit reached: {processed_count} file(s). Exiting.")
                return 0

            if args.pause_between_files:
                answer = input("Paused after file. Press Enter to continue, or type Q to stop: ").strip().lower()
                if answer == "q":
                    print("Stopped by user.")
                    return 0

        if args.once:
            break

        time.sleep(args.poll_seconds)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
