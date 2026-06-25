from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from transcribe_api import DEFAULT_AUDIO_BITRATE, MAX_API_UPLOAD_BYTES, split_for_api_size_safe
from transcribe_local import is_supported_media_path, validate_audio_stream


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create unique path for: {path}")


def iter_inputs(input_dir: Path, selected: set[str] | None = None) -> list[Path]:
    return [
        path
        for path in sorted(input_dir.iterdir())
        if path.is_file() and is_supported_media_path(path)
        and (not selected or path.name in selected)
    ]


def export_high_quality_mp3(input_path: Path, output_dir: Path) -> Path:
    validate_audio_stream(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = unique_path(output_dir / f"{input_path.stem}.mp3")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-map",
        "0:a:0",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "0",
        str(output_path),
    ]
    run(command)
    return output_path


def compress_for_groq(input_path: Path, output_dir: Path, segment_seconds: int, max_mib: float) -> Path:
    validate_audio_stream(input_path)
    file_output_dir = output_dir / input_path.stem
    chunks_dir = file_output_dir / "chunks"
    file_output_dir.mkdir(parents=True, exist_ok=True)
    chunks, actual_seconds, actual_bitrate = split_for_api_size_safe(
        input_path=input_path,
        chunk_dir=chunks_dir,
        initial_segment_seconds=segment_seconds,
        audio_bitrate=DEFAULT_AUDIO_BITRATE,
        max_bytes=int(max_mib * 1024 * 1024),
    )
    manifest = file_output_dir / "manifest.txt"
    manifest.write_text(
        "\n".join(
            [
                f"source={input_path}",
                f"audio_bitrate={actual_bitrate}",
                f"segment_seconds={actual_seconds}",
                f"max_mib={max_mib}",
                "",
                *[f"{chunk.name}\t{chunk.stat().st_size}" for chunk in chunks],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return file_output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Video/audio helper tools.")
    parser.add_argument("mode", choices=["high-quality-mp3", "compress-for-groq"])
    parser.add_argument("--input-dir", type=Path, default=Path("inputs"))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--segment-seconds", type=int, default=600)
    parser.add_argument("--max-mib", type=float, default=24.0)
    parser.add_argument("--files", nargs="*", default=[], help="Only process these file names from input-dir.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir
    if args.output_dir is None:
        output_dir = Path("audio_exports" if args.mode == "high-quality-mp3" else "compressed_audio")
    else:
        output_dir = args.output_dir

    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = set(args.files)
    files = iter_inputs(input_dir, selected)
    if not files:
        print(f"No media files found in: {input_dir.resolve()}")
        return 0

    failed: list[Path] = []
    for index, input_path in enumerate(files, start=1):
        print("")
        print(f"===== Video tool [{index}/{len(files)}] {input_path.name} =====")
        try:
            if args.mode == "high-quality-mp3":
                result = export_high_quality_mp3(input_path, output_dir)
                print(f"MP3: {result}")
            else:
                result = compress_for_groq(input_path, output_dir, args.segment_seconds, args.max_mib)
                print(f"Folder: {result}")
        except Exception as exc:
            failed.append(input_path)
            print(f"Failed: {input_path.name}: {exc}", file=sys.stderr)

    if failed:
        print("")
        print("Finished with failures:")
        for path in failed:
            print(f"- {path}")
        return 1

    print("")
    print("Video tool completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
