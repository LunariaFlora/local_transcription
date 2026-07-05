from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from polish_transcript import call_model, create_text_client, load_dotenv


MARKER_RE = re.compile(r"\[(\d{2}:\d{2}:\d{2})\]")
MAX_CHUNK_CHARS = 10_000
BLOCK_SECONDS = 30
BLOCK_CHARS = 260


def display_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}"


def timed_blocks(segments: Iterable[dict[str, object]]) -> list[str]:
    blocks: list[str] = []
    block_start = 0.0
    block_texts: list[str] = []

    def flush() -> None:
        if not block_texts:
            return
        text = block_texts[0]
        for part in block_texts[1:]:
            separator = " " if text[-1:].isascii() and text[-1:].isalnum() and part[:1].isascii() and part[:1].isalnum() else ""
            text += separator + part
        text = text.strip()
        if text:
            blocks.append(f"[{display_timestamp(block_start)}] {text}")

    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0.0)
        current_chars = sum(len(part) for part in block_texts)
        if block_texts and (start - block_start >= BLOCK_SECONDS or current_chars + len(text) > BLOCK_CHARS):
            flush()
            block_texts = []
        if not block_texts:
            block_start = start
        block_texts.append(text)

    flush()
    return blocks


def split_timed_blocks(blocks: list[str]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for block in blocks:
        projected = current_size + len(block) + 1
        if current and projected > MAX_CHUNK_CHARS:
            chunks.append("\n".join(current))
            current = []
            current_size = 0
        current.append(block)
        current_size += len(block) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def normalize_result(text: str) -> str:
    cleaned = text.strip().removeprefix("```text").removeprefix("```markdown").removeprefix("```")
    cleaned = cleaned.removesuffix("```").strip()
    sections = re.split(r"(?=\[\d{2}:\d{2}:\d{2}\])", cleaned)
    return "\n\n".join(section.strip() for section in sections if section.strip())


def finalize_transcript(
    segments: Iterable[dict[str, object]],
    output_path: Path,
    source_name: str,
) -> None:
    blocks = timed_blocks(segments)
    if not blocks:
        raise ValueError("Transcription returned no timed text.")

    load_dotenv(Path(".env"))
    client, model, provider = create_text_client()
    print(f"Formatting transcript with {provider} model: {model}")

    polished_chunks: list[str] = []
    for index, chunk in enumerate(split_timed_blocks(blocks), start=1):
        expected_markers = MARKER_RE.findall(chunk)
        instruction = f"""请整理这份来自“{source_name}”的带时间标记转写稿。

要求：
- 修正明显错字、同音误识别、标点和断句。
- 根据上下文纠正明显错误的专有名词；不确定时保留原文。
- 删除无意义口头语和机械重复，但保留原意、信息顺序、语气和完整逻辑。
- 不总结、不扩写、不添加标题或解释。
- 每个 [时:分:秒] 标记必须原样保留，数量、内容和顺序都不能改变。
- 每个时间标记及其正文保持为一个段落，可在段落之间加入空行。

只输出整理后的带时间标记正文。"""
        print(f"Formatting timed text chunk {index}...")
        polished = normalize_result(call_model(client, model, instruction, chunk))
        if MARKER_RE.findall(polished) != expected_markers:
            raise RuntimeError(f"DeepSeek changed timestamp markers in chunk {index}.")
        polished_chunks.append(polished)

    final_text = "\n\n".join(polished_chunks).strip() + "\n"
    temporary = output_path.with_suffix(".tmp")
    temporary.write_text(final_text, encoding="utf-8")
    temporary.replace(output_path)
