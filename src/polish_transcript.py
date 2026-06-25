from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from openai import OpenAI


DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
CHUNK_SIZE = 12_000


SYSTEM_PROMPT = """你是中文内容编辑。你的任务是整理音频转写稿。

要求：
- 保留原意，不编造事实。
- 重点修正转写错误，把不准确的词、人名、地名、机构名、专业术语、同音误识别词改成更准确的表达。
- 润色力度要轻，只做必要的通顺化处理，不要大幅改写句式和表达风格。
- 去掉明显口误、重复词、无意义语气词，但不要删除有效信息。
- 修正常见断句和标点问题。
- 保留重要数字、人名、地名、时间、专业术语。
- 不要把内容改写成宣传文案。
- 如果原文信息不足，不要补充外部信息。
"""


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def split_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    text = text.strip()
    if len(text) <= chunk_size:
        return [text] if text else []

    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        if len(paragraph) > chunk_size:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            for start in range(0, len(paragraph), chunk_size):
                chunks.append(paragraph[start : start + chunk_size])
            continue

        projected = current_len + len(paragraph) + 2
        if current and projected > chunk_size:
            chunks.append("\n\n".join(current))
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len = projected

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def create_text_client() -> tuple[OpenAI, str, str]:
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
    if deepseek_api_key:
        client = OpenAI(
            api_key=deepseek_api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        model = os.getenv("DEEPSEEK_TEXT_MODEL", DEFAULT_DEEPSEEK_MODEL)
        return client, model, "DeepSeek"

    raise RuntimeError("DEEPSEEK_API_KEY is required for text polish. Add it to .env.")


def call_model(client: OpenAI, model: str, instruction: str, text: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{instruction}\n\n转写稿：\n{text}"},
        ],
    )
    content = response.choices[0].message.content
    return (content or "").strip()


def polish_cleaned(client: OpenAI, model: str, transcript: str) -> str:
    chunks = split_text(transcript)
    cleaned_parts: list[str] = []

    for index, chunk in enumerate(chunks, start=1):
        print(f"Polishing chunk {index}/{len(chunks)}...")
        instruction = """请整理下面这一段转写稿，输出一份轻度润色后的中文文稿。

重点：
- 修正明显转写错误和同音错词。
- 保持原始说法和信息顺序。
- 不要扩写、不要总结、不要加入原文没有的信息。
- 只做必要的断句、标点、去重复和轻微通顺化。

只输出整理后的文稿正文，不要输出摘要、标题或解释。"""
        cleaned_parts.append(call_model(client, model, instruction, chunk))

    return "\n\n".join(cleaned_parts).strip() + "\n"


def create_summary(client: OpenAI, model: str, cleaned_text: str) -> str:
    source = cleaned_text[:60_000]
    instruction = """请基于下面整理后的文稿输出 Markdown：

# 摘要
用 3-6 句话概括核心内容。

# 关键要点
用项目符号列出 5-12 条要点。

# 可执行事项
如果文中有明确行动项，请列出；没有则写“无明确行动项”。"""
    return call_model(client, model, instruction, source).strip() + "\n"


def create_keywords(client: OpenAI, model: str, cleaned_text: str) -> str:
    source = cleaned_text[:60_000]
    instruction = """请基于下面整理后的文稿输出 Markdown：

# 关键词
列出 10-30 个关键词或短语。

# 可能标题
给出 5 个适合这份内容的中文标题。"""
    return call_model(client, model, instruction, source).strip() + "\n"


def polish_transcript_file(transcript_path: Path, model: str | None, force: bool) -> None:
    load_dotenv(Path(".env"))

    output_dir = transcript_path.parent
    cleaned_path = output_dir / "cleaned.md"
    summary_path = output_dir / "summary.md"
    keywords_path = output_dir / "keywords.md"

    if not force and cleaned_path.exists() and summary_path.exists() and keywords_path.exists():
        print(f"Skipping already polished folder: {output_dir}")
        return

    transcript = transcript_path.read_text(encoding="utf-8").strip()
    if not transcript:
        raise ValueError(f"Transcript is empty: {transcript_path}")

    client, configured_model, provider = create_text_client()
    model = model or configured_model
    print(f"Using {provider} model: {model}")
    cleaned = polish_cleaned(client, model, transcript)
    cleaned_path.write_text(cleaned, encoding="utf-8")

    summary = create_summary(client, model, cleaned)
    summary_path.write_text(summary, encoding="utf-8")

    keywords = create_keywords(client, model, cleaned)
    keywords_path.write_text(keywords, encoding="utf-8")

    print(f"Cleaned : {cleaned_path}")
    print(f"Summary : {summary_path}")
    print(f"Keywords: {keywords_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polish one transcript.txt with an LLM API.")
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        polish_transcript_file(args.transcript, args.model, args.force)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
