from __future__ import annotations

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from transcribe_local import load_whisper_model


def run_nvidia_smi() -> None:
    command = [
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu",
        "--format=csv",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    print("nvidia-smi:")
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    if result.returncode != 0:
        raise RuntimeError("nvidia-smi failed")


def write_silence_wav(path: Path, seconds: int = 3) -> None:
    sample_rate = 16_000
    frames = b"\x00\x00" * sample_rate * seconds
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)


def main() -> int:
    print(f"Python: {sys.executable}")
    run_nvidia_smi()

    print("")
    print("Loading faster-whisper tiny on CUDA...")
    model = load_whisper_model("tiny", device="cuda", compute_type="float16")

    with tempfile.TemporaryDirectory() as temp_dir:
        wav_path = Path(temp_dir) / "silence.wav"
        write_silence_wav(wav_path)

        print("Running a tiny CUDA transcription smoke test...")
        segments, info = model.transcribe(str(wav_path), beam_size=1, vad_filter=False)
        list(segments)
        print(f"CUDA smoke test completed. Detected language: {info.language}")

    print("")
    print("GPU path is usable for faster-whisper/CTranslate2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
