from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from openai import OpenAI

from finalize_transcript import finalize_transcript
from polish_transcript import load_dotenv
from transcribe_local import is_supported_media_path, validate_audio_stream


DEFAULT_GROQ_TRANSCRIBE_MODEL = "whisper-large-v3-turbo"
MAX_API_UPLOAD_BYTES = 24 * 1024 * 1024
DEFAULT_AUDIO_BITRATE = "64k"
MIN_SEGMENT_SECONDS = 30


def create_groq_client() -> tuple[OpenAI, str]:
    load_dotenv(Path(".env"))
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is required for cloud transcription. Add it to .env.")

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
    )
    model = os.getenv("GROQ_TRANSCRIBE_MODEL", DEFAULT_GROQ_TRANSCRIBE_MODEL)
    return client, model


def split_for_api(
    input_path: Path,
    chunk_dir: Path,
    segment_seconds: int,
    audio_bitrate: str,
    max_bytes: int,
) -> list[Path]:
    if chunk_dir.exists():
        shutil.rmtree(chunk_dir)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        audio_bitrate,
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-reset_timestamps",
        "1",
        str(chunk_dir / "chunk_%03d.mp3"),
    ]
    subprocess.run(command, check=True)
    return sorted(chunk_dir.glob("chunk_*.mp3"))


def oversized_chunks(chunks: list[Path], max_bytes: int) -> list[Path]:
    return [chunk for chunk in chunks if chunk.stat().st_size > max_bytes]


def split_for_api_size_safe(
    input_path: Path,
    chunk_dir: Path,
    initial_segment_seconds: int,
    audio_bitrate: str,
    max_bytes: int,
) -> tuple[list[Path], int, str]:
    segment_seconds = initial_segment_seconds

    while True:
        chunks = split_for_api(
            input_path=input_path,
            chunk_dir=chunk_dir,
            segment_seconds=segment_seconds,
            audio_bitrate=audio_bitrate,
            max_bytes=max_bytes,
        )
        too_large = oversized_chunks(chunks, max_bytes)
        if not too_large:
            return chunks, segment_seconds, audio_bitrate

        if segment_seconds <= MIN_SEGMENT_SECONDS:
            names = ", ".join(f"{path.name}={path.stat().st_size / 1024 / 1024:.1f}MiB" for path in too_large)
            raise RuntimeError(
                f"API chunks still exceed {max_bytes / 1024 / 1024:.0f}MiB at "
                f"{segment_seconds}s segments: {names}"
            )

        next_segment_seconds = max(MIN_SEGMENT_SECONDS, segment_seconds // 2)
        print(
            f"Chunk exceeds {max_bytes / 1024 / 1024:.0f}MiB. "
            f"Retrying with {next_segment_seconds}s chunks."
        )
        segment_seconds = next_segment_seconds


def transcribe_chunk(
    client: OpenAI,
    model: str,
    chunk_path: Path,
    language: str | None,
) -> tuple[str, list[dict[str, object]]]:
    with chunk_path.open("rb") as handle:
        result = client.audio.transcriptions.create(
            model=model,
            file=handle,
            language=language,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
    text = str(getattr(result, "text", "") or "").strip()
    segments: list[dict[str, object]] = []
    for item in getattr(result, "segments", None) or []:
        if hasattr(item, "model_dump"):
            raw = item.model_dump()
        elif isinstance(item, dict):
            raw = item
        else:
            raw = {
                "start": getattr(item, "start", 0.0),
                "end": getattr(item, "end", 0.0),
                "text": getattr(item, "text", ""),
            }
        segments.append(
            {
                "start": float(raw.get("start") or 0.0),
                "end": float(raw.get("end") or 0.0),
                "text": str(raw.get("text") or ""),
            }
        )
    return text, segments


def transcribe_file_api(
    input_path: Path,
    output_dir: Path,
    client: OpenAI,
    model: str,
    language: str | None,
    segment_seconds: int,
    audio_bitrate: str,
    max_bytes: int,
    keep_chunks: bool,
    force: bool,
    request_delay_seconds: float,
) -> None:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")
    if not is_supported_media_path(input_path):
        raise ValueError(f"Unsupported file type: {input_path.suffix}")

    validate_audio_stream(input_path)

    file_output_dir = output_dir / input_path.stem
    transcript_path = file_output_dir / "transcript.txt"
    metadata_path = file_output_dir / "transcript_api.json"

    if not force and transcript_path.exists():
        print(f"Skipping existing transcript: {transcript_path}")
        return

    file_output_dir.mkdir(parents=True, exist_ok=True)
    chunk_dir = file_output_dir / "api_chunks"

    print(f"[1/3] Compressing and splitting for API: {input_path}")
    chunks, actual_segment_seconds, actual_bitrate = split_for_api_size_safe(
        input_path=input_path,
        chunk_dir=chunk_dir,
        initial_segment_seconds=segment_seconds,
        audio_bitrate=audio_bitrate,
        max_bytes=max_bytes,
    )
    if not chunks:
        raise RuntimeError(f"No API chunks were created for: {input_path}")

    print(
        f"[2/3] API transcribing {len(chunks)} chunk(s) with {model} "
        f"({actual_bitrate}, {actual_segment_seconds}s, max {max_bytes / 1024 / 1024:.0f}MiB)..."
    )
    all_segments: list[dict[str, object]] = []
    chunk_records: list[dict[str, object]] = []
    for index, chunk in enumerate(chunks, start=1):
        if index > 1 and request_delay_seconds > 0:
            print(f"Waiting {request_delay_seconds:g}s for Groq rate limit...")
            time.sleep(request_delay_seconds)
        print(f"API chunk {index}/{len(chunks)}: {chunk.name}")
        text, chunk_segments = transcribe_chunk(client, model, chunk, language)
        offset = (index - 1) * actual_segment_seconds
        absolute_segments = [
            {
                **segment,
                "start": float(segment["start"]) + offset,
                "end": float(segment["end"]) + offset,
            }
            for segment in chunk_segments
        ]
        all_segments.extend(absolute_segments)
        chunk_records.append(
            {
                "index": index,
                "file": chunk.name,
                "size_bytes": chunk.stat().st_size,
                "text": text,
                "segments": absolute_segments,
            }
        )

    metadata_path.write_text(
        json.dumps(
            {
                "provider": "groq",
                "model": model,
                "source": str(input_path),
                "segment_seconds": actual_segment_seconds,
                "audio_bitrate": actual_bitrate,
                "max_upload_bytes": max_bytes,
                "chunks": chunk_records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    finalize_transcript(all_segments, transcript_path, input_path.name)

    if not keep_chunks:
        shutil.rmtree(chunk_dir, ignore_errors=True)

    print("[3/3] Done")
    print(f"TXT : {transcript_path}")
    print(f"JSON: {metadata_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe inputs with Groq Whisper API.")
    parser.add_argument("--input-dir", type=Path, default=Path("inputs"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--model")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--segment-seconds", type=int, default=600)
    parser.add_argument("--audio-bitrate", default=DEFAULT_AUDIO_BITRATE)
    parser.add_argument("--max-mib", type=float, default=24.0)
    parser.add_argument("--request-delay-seconds", type=float, default=20.0)
    parser.add_argument("--files", nargs="*", default=[], help="Only process these file names from input-dir.")
    parser.add_argument("--keep-chunks", action="store_true")
    parser.add_argument("--force", action="store_true")
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
        return 0

    client, configured_model = create_groq_client()
    model = args.model or configured_model
    language = None if args.language.lower() == "auto" else args.language

    failed: list[Path] = []
    print(f"Found {len(files)} file(s). Groq model: {model}")
    for index, input_path in enumerate(files, start=1):
        print("")
        print(f"===== API [{index}/{len(files)}] {input_path.name} =====")
        try:
            transcribe_file_api(
                input_path=input_path,
                output_dir=output_dir,
                client=client,
                model=model,
                language=language,
                segment_seconds=args.segment_seconds,
                audio_bitrate=args.audio_bitrate,
                max_bytes=int(args.max_mib * 1024 * 1024),
                keep_chunks=args.keep_chunks,
                force=args.force,
                request_delay_seconds=args.request_delay_seconds,
            )
        except Exception as exc:
            failed.append(input_path)
            print(f"Failed: {input_path.name}: {exc}", file=sys.stderr)

    if failed:
        print("")
        print("Finished with API transcription failures:")
        for path in failed:
            print(f"- {path}")
        return 1

    print("")
    print("All API transcriptions completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
