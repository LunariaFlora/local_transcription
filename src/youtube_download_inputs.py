from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUTS_DIR = ROOT / "inputs"
INFO_SOURCE_PYTHON = Path(r"D:\Project\Codex\信息源系统\.venv\Scripts\python.exe")
YOUTUBE_COOKIE_FILE = ROOT / "youtube_cookies.txt"


def yt_dlp_python() -> str:
    candidates = [Path(sys.executable), INFO_SOURCE_PYTHON]
    for python in candidates:
        if not python.exists():
            continue
        result = subprocess.run(
            [str(python), "-m", "yt_dlp", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return str(python)
    raise RuntimeError("未找到可用 yt-dlp。请先在信息源系统 .venv 中安装 yt-dlp。")


def move_downloaded_file(source: Path) -> Path:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    target = INPUTS_DIR / source.name
    if target.exists():
        target = INPUTS_DIR / f"{target.stem}_{int(time.time())}{target.suffix}"
    shutil.move(str(source), str(target))
    return target


def download_one(url: str, audio_only: bool, cookies_from_chrome: bool) -> Path:
    python = yt_dlp_python()
    with tempfile.TemporaryDirectory(prefix="yt_download_") as temp_dir:
        temp_path = Path(temp_dir)
        output_template = str(temp_path / "%(title).180B [%(id)s].%(ext)s")
        command = [
            python,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--windows-filenames",
            "--paths",
            str(temp_path),
            "-o",
            output_template,
        ]
        if cookies_from_chrome:
            if YOUTUBE_COOKIE_FILE.exists():
                command.extend(["--cookies", str(YOUTUBE_COOKIE_FILE)])
            else:
                command.extend(["--cookies-from-browser", "chrome"])
        if audio_only:
            command.extend(["-f", "bestaudio[ext=m4a]/bestaudio/best", "-x", "--audio-format", "m4a"])
        else:
            command.extend(["-f", "bv*+ba/best", "--merge-output-format", "mp4"])
        command.append(url)

        result = subprocess.run(command, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp 下载失败：{url}")

        files = [path for path in temp_path.iterdir() if path.is_file() and not path.name.endswith(".part")]
        if not files:
            raise RuntimeError(f"yt-dlp 没有产出文件：{url}")
        return move_downloaded_file(max(files, key=lambda path: path.stat().st_mtime))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download YouTube videos into local_transcription inputs.")
    parser.add_argument("--urls", nargs="+", required=True)
    parser.add_argument("--audio-only", action="store_true")
    parser.add_argument("--cookies-from-chrome", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    failures: list[tuple[str, str]] = []
    for index, url in enumerate(args.urls, 1):
        print(f"[{index}/{len(args.urls)}] {url}", flush=True)
        try:
            target = download_one(url, audio_only=args.audio_only, cookies_from_chrome=args.cookies_from_chrome)
            print(f"saved: {target}", flush=True)
        except Exception as exc:  # noqa: BLE001 - batch should continue.
            failures.append((url, str(exc)))
            print(f"failed: {url} :: {exc}", flush=True)

    if failures:
        print("failures:", flush=True)
        for url, reason in failures:
            print(f"- {url} :: {reason}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
