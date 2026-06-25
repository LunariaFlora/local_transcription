from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

SUPPORTED_MEDIA = {
    ".aac",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".ts",
    ".wav",
    ".webm",
    ".wma",
}


def configure_nvidia_dll_paths() -> None:
    if os.name != "nt":
        return

    candidates = [
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "bin",
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cudnn" / "bin",
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cublas" / "lib",
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / "cudnn" / "lib",
    ]

    existing_paths = []
    for candidate in candidates:
        if candidate.exists():
            existing_paths.append(str(candidate))
            os.add_dll_directory(str(candidate))

    if existing_paths:
        os.environ["PATH"] = os.pathsep.join(existing_paths + [os.environ.get("PATH", "")])


def load_whisper_model(model_name: str, device: str, compute_type: str):
    if device == "cuda":
        configure_nvidia_dll_paths()

    from faster_whisper import WhisperModel

    return WhisperModel(model_name, device=device, compute_type=compute_type)


def is_supported_media_path(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_MEDIA or path.suffix == ""


def probe_media(input_path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(input_path),
    ]
    completed = subprocess.run(command, check=True, capture_output=True)
    output = completed.stdout.decode("utf-8", errors="replace")
    return json.loads(output)


def get_audio_streams(input_path: Path) -> list[dict]:
    probe = probe_media(input_path)
    return [
        stream
        for stream in probe.get("streams", [])
        if stream.get("codec_type") == "audio"
    ]


def validate_audio_stream(input_path: Path) -> None:
    audio_streams = get_audio_streams(input_path)
    if not audio_streams:
        raise ValueError(
            "No audio stream was found. This may be a video-only stream, "
            "a browser media segment, or an encrypted/incomplete cache file."
        )


@dataclass
class Segment:
    start: float
    end: float
    text: str


def to_simplified_chinese(text: str) -> str:
    try:
        from opencc import OpenCC
    except Exception:
        return text

    converter = OpenCC("t2s")
    return converter.convert(text)


def normalize_segments(segments: list[Segment]) -> list[Segment]:
    return [
        Segment(start=segment.start, end=segment.end, text=to_simplified_chinese(segment.text))
        for segment in segments
    ]


def run_ffmpeg_extract(input_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    subprocess.run(command, check=True)
    return output_path


def format_timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_txt(path: Path, segments: list[Segment]) -> None:
    text = "\n".join(segment.text.strip() for segment in segments if segment.text.strip())
    path.write_text(text + "\n", encoding="utf-8")


def write_srt(path: Path, segments: list[Segment]) -> None:
    lines: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = segment.text.strip()
        if not text:
            continue
        lines.extend(
            [
                str(index),
                f"{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}",
                text,
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_json(path: Path, segments: list[Segment], info: object) -> None:
    payload = {
        "language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "segments": [asdict(segment) for segment in segments],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def transcribe_file(
    input_path: Path,
    output_dir: Path,
    model_name: str,
    device: str,
    compute_type: str,
    language: str | None,
    beam_size: int,
    vad_filter: bool,
) -> None:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    if not is_supported_media_path(input_path):
        raise ValueError(
            f"Unsupported file type: {input_path.suffix}. "
            "Supported files include audio/video files and extensionless cache files."
        )
    validate_audio_stream(input_path)

    file_output_dir = output_dir / input_path.stem
    file_output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = file_output_dir / "audio.16k.wav"

    print(f"[1/4] Extracting audio: {input_path}")
    run_ffmpeg_extract(input_path, wav_path)

    print(f"[2/4] Loading model: {model_name} ({device}, {compute_type})")
    model = load_whisper_model(model_name, device=device, compute_type=compute_type)

    print("[3/4] Transcribing...")
    raw_segments, info = model.transcribe(
        str(wav_path),
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )
    segments = [
        Segment(start=item.start, end=item.end, text=item.text)
        for item in raw_segments
    ]
    segments = normalize_segments(segments)

    txt_path = file_output_dir / "transcript.txt"
    srt_path = file_output_dir / "transcript.srt"
    json_path = file_output_dir / "transcript.json"

    write_txt(txt_path, segments)
    write_srt(srt_path, segments)
    write_json(json_path, segments, info)
    wav_path.unlink(missing_ok=True)

    print("[4/4] Done")
    print(f"Folder: {file_output_dir}")
    print(f"TXT   : {txt_path}")
    print(f"SRT   : {srt_path}")
    print(f"JSON  : {json_path}")


def transcribe_preloaded_model(
    input_path: Path,
    output_dir: Path,
    model,
    language: str | None,
    beam_size: int,
    vad_filter: bool,
) -> Path:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    if not is_supported_media_path(input_path):
        raise ValueError(
            f"Unsupported file type: {input_path.suffix}. "
            "Supported files include audio/video files and extensionless cache files."
        )
    validate_audio_stream(input_path)

    file_output_dir = output_dir / input_path.stem
    file_output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = file_output_dir / "audio.16k.wav"

    print(f"[1/3] Extracting audio: {input_path}")
    run_ffmpeg_extract(input_path, wav_path)

    print("[2/3] Transcribing...")
    raw_segments, info = model.transcribe(
        str(wav_path),
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
    )
    segments = [
        Segment(start=item.start, end=item.end, text=item.text)
        for item in raw_segments
    ]
    segments = normalize_segments(segments)

    txt_path = file_output_dir / "transcript.txt"
    srt_path = file_output_dir / "transcript.srt"
    json_path = file_output_dir / "transcript.json"

    write_txt(txt_path, segments)
    write_srt(srt_path, segments)
    write_json(json_path, segments, info)
    wav_path.unlink(missing_ok=True)

    print("[3/3] Done")
    print(f"Folder: {file_output_dir}")
    return file_output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local audio/video transcription with faster-whisper.")
    parser.add_argument("input", type=Path, help="Audio or video file to transcribe.")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("-m", "--model", default="small", help="tiny, base, small, medium, large-v3, etc.")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--compute-type", default="int8", help="Use int8 for CPU, float16 for CUDA.")
    parser.add_argument("--language", default="zh", help="Use zh for Chinese, en for English, or auto for detection.")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--no-vad", action="store_true", help="Disable voice activity detection.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    language = None if args.language.lower() == "auto" else args.language
    try:
        transcribe_file(
            input_path=args.input,
            output_dir=args.output_dir,
            model_name=args.model,
            device=args.device,
            compute_type=args.compute_type,
            language=language,
            beam_size=args.beam_size,
            vad_filter=not args.no_vad,
        )
    except subprocess.CalledProcessError as exc:
        print(f"ffmpeg failed with exit code {exc.returncode}", file=sys.stderr)
        return exc.returncode
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
