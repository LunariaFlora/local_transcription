from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from polish_transcript import (
    call_model,
    create_keywords,
    create_summary,
    create_text_client,
    load_dotenv,
    polish_cleaned,
)


def safe_slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:80] or "untitled"


def create_english_title(client, model: str, text: str) -> str:
    instruction = """Create one concise English filename title for this transcript.

Rules:
- 4 to 10 words.
- English only.
- No punctuation except spaces.
- Do not include quotes.
- Preserve the topic accurately.

Only output the title."""
    title = call_model(client, model, instruction, text[:30_000])
    return re.sub(r"[^A-Za-z0-9 ]+", "", title).strip() or "Untitled Transcript"


def unique_folder(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create unique folder for: {path}")


def rename_output_folder(folder: Path, title: str) -> Path:
    slug = safe_slug(title)
    target_name = f"{folder.name}--{slug}"
    target = unique_folder(folder.with_name(target_name))
    if folder.resolve() == target.resolve():
        return folder
    folder.rename(target)
    return target


def process_transcript(transcript: Path, mode: str, model: str | None, rename_folder: bool) -> Path:
    load_dotenv(Path(".env"))
    client, configured_model, provider = create_text_client()
    model = model or configured_model
    print(f"Using {provider} model: {model}")

    folder = transcript.parent
    text = transcript.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Transcript is empty: {transcript}")

    if mode == "polish":
        polished = polish_cleaned(client, model, text)
        output = folder / "elegant_transcript.md"
        output.write_text(polished, encoding="utf-8")
        print(f"Elegant transcript: {output}")
        return folder

    source = (folder / "elegant_transcript.md").read_text(encoding="utf-8") if (folder / "elegant_transcript.md").exists() else text
    if mode == "title":
        title = create_english_title(client, model, source)
        print(f"Title: {title}")
        if rename_folder:
            renamed = rename_output_folder(folder, title)
            print(f"Renamed folder: {renamed}")
            return renamed
        return folder

    summary = create_summary(client, model, source)

    (folder / "summary.md").write_text(summary, encoding="utf-8")
    print(f"Summary: {folder / 'summary.md'}")
    return folder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Text-only DeepSeek processing for transcripts.")
    parser.add_argument("mode", choices=["polish", "summary", "title"])
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--model")
    parser.add_argument("--rename-folder", action="store_true")
    parser.add_argument("--folders", nargs="*", default=[], help="Only process these output folder names.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = set(args.folders)
    transcripts = [
        path
        for path in sorted(args.output_dir.glob("*/transcript.txt"))
        if not selected or path.parent.name in selected
    ]
    if not transcripts:
        print(f"No transcript.txt files found under: {args.output_dir.resolve()}")
        return 0

    failed: list[Path] = []
    for index, transcript in enumerate(transcripts, start=1):
        print("")
        print(f"===== Text [{index}/{len(transcripts)}] {transcript.parent.name} =====")
        try:
            process_transcript(transcript, args.mode, args.model, args.rename_folder)
        except Exception as exc:
            failed.append(transcript)
            print(f"Failed: {transcript}: {exc}", file=sys.stderr)

    if failed:
        print("")
        print("Finished with text processing failures:")
        for path in failed:
            print(f"- {path}")
        return 1

    print("")
    print("Text processing completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
