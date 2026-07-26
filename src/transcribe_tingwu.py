from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import oss2
import requests

from polish_transcript import load_dotenv
from transcribe_local import is_supported_media_path, validate_audio_stream


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGION = "cn-beijing"
DEFAULT_DOMAIN = "tingwu.cn-beijing.aliyuncs.com"
DEFAULT_VERSION = "2023-09-30"
TERMINAL_STATUSES = {"COMPLETED", "SUCCESS", "FAILED", "INVALID"}


def env_value(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return ""


def require_env(*names: str) -> str:
    value = env_value(*names)
    if not value:
        joined = " / ".join(names)
        raise RuntimeError(f"Missing environment variable: {joined}")
    return value


def percent_encode(value: str) -> str:
    return quote(value, safe="-_.~")


def canonical_query(params: dict[str, str]) -> str:
    return "&".join(
        f"{percent_encode(key)}={percent_encode(value)}"
        for key, value in sorted(params.items())
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def signed_request(
    method: str,
    path: str,
    action: str,
    query: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    access_key_id = require_env("ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_ID")
    access_key_secret = require_env("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "ALIYUN_ACCESS_KEY_SECRET")
    domain = os.getenv("TINGWU_DOMAIN", DEFAULT_DOMAIN)
    version = os.getenv("TINGWU_API_VERSION", DEFAULT_VERSION)
    payload = b"" if method.upper() == "GET" else json.dumps(body or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_hash = sha256_hex(payload)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = uuid.uuid4().hex
    headers = {
        "host": domain,
        "x-acs-action": action,
        "x-acs-version": version,
        "x-acs-date": now,
        "x-acs-signature-nonce": nonce,
        "x-acs-content-sha256": payload_hash,
        "content-type": "application/json; charset=utf-8",
    }
    signed_header_names = [
        "host",
        "x-acs-action",
        "x-acs-content-sha256",
        "x-acs-date",
        "x-acs-signature-nonce",
        "x-acs-version",
    ]
    signed_headers = ";".join(signed_header_names)
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in signed_header_names)
    canonical = "\n".join(
        [
            method.upper(),
            path,
            canonical_query(query or {}),
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    string_to_sign = "ACS3-HMAC-SHA256\n" + sha256_hex(canonical.encode("utf-8"))
    signature = hmac.new(
        access_key_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers["authorization"] = (
        f"ACS3-HMAC-SHA256 Credential={access_key_id},"
        f"SignedHeaders={signed_headers},Signature={signature}"
    )
    url = f"https://{domain}{path}"
    response = requests.request(
        method,
        url,
        params=query,
        data=payload if method.upper() != "GET" else None,
        headers=headers,
        timeout=60,
    )
    try:
        data = response.json()
    except ValueError:
        response.raise_for_status()
        raise RuntimeError(f"Tingwu returned non-JSON response: {response.text[:500]}")
    if not response.ok or str(data.get("Code")) not in {"0", "Success", ""}:
        raise RuntimeError(f"Tingwu API error: HTTP {response.status_code} {json.dumps(data, ensure_ascii=False)}")
    return data


def safe_object_name(path: Path) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", path.name).strip("._")
    return cleaned or f"media{path.suffix.lower()}"


def signed_oss_url(input_path: Path) -> tuple[str, oss2.Bucket, str]:
    bucket_name = require_env("ALIYUN_OSS_BUCKET", "OSS_BUCKET")
    endpoint = require_env("ALIYUN_OSS_ENDPOINT", "OSS_ENDPOINT")
    access_key_id = require_env("ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_ID")
    access_key_secret = require_env("ALIBABA_CLOUD_ACCESS_KEY_SECRET", "ALIYUN_ACCESS_KEY_SECRET")
    prefix = os.getenv("TINGWU_OSS_PREFIX", "local-transcription/tingwu-inputs").strip("/")
    expires = int(os.getenv("TINGWU_OSS_URL_EXPIRES", "21600"))
    key = f"{prefix}/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_object_name(input_path)}"
    print(f"[1/4] Uploading to OSS: oss://{bucket_name}/{key}")
    bucket = oss2.Bucket(oss2.Auth(access_key_id, access_key_secret), endpoint, bucket_name)
    bucket.put_object_from_file(key, str(input_path))
    return bucket.sign_url("GET", key, expires, slash_safe=True), bucket, key


def source_url_for(input_path: Path) -> tuple[str, oss2.Bucket | None, str]:
    configured = os.getenv("TINGWU_FILE_URL")
    if configured:
        return configured, None, ""
    return signed_oss_url(input_path)


def should_delete_temporary_object() -> bool:
    value = os.getenv("TINGWU_DELETE_OSS_OBJECT", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def delete_temporary_object(bucket: oss2.Bucket | None, key: str) -> None:
    if bucket is None or not key or not should_delete_temporary_object():
        return
    try:
        bucket.delete_object(key)
        print(f"Temporary OSS file deleted: oss://{bucket.bucket_name}/{key}")
    except Exception as exc:
        print(f"Temporary OSS cleanup failed: {exc}", file=sys.stderr)


def create_task(file_url: str, source_name: str) -> str:
    app_key = require_env("TINGWU_APP_KEY")
    source_language = os.getenv("TINGWU_SOURCE_LANGUAGE", "cn")
    speaker_count = int(os.getenv("TINGWU_SPEAKER_COUNT", "0"))
    task_key = f"local_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    body = {
        "AppKey": app_key,
        "Input": {
            "SourceLanguage": source_language,
            "FileUrl": file_url,
            "TaskKey": task_key,
        },
        "Parameters": {
            "Transcription": {
                "DiarizationEnabled": True,
                "Diarization": {"SpeakerCount": speaker_count},
                "OutputLevel": 1,
            },
            "TextPolishEnabled": True,
            "AutoChaptersEnabled": False,
            "MeetingAssistanceEnabled": False,
            "SummarizationEnabled": False,
        },
    }
    print(f"[2/4] Creating Tingwu task for: {source_name}")
    data = signed_request(
        "PUT",
        "/openapi/tingwu/v2/tasks",
        "CreateTask",
        query={"type": "offline"},
        body=body,
    )
    task_id = str((data.get("Data") or {}).get("TaskId") or "")
    if not task_id:
        raise RuntimeError(f"Tingwu CreateTask returned no TaskId: {json.dumps(data, ensure_ascii=False)}")
    return task_id


def get_task_info(task_id: str) -> dict[str, Any]:
    return signed_request(
        "GET",
        f"/openapi/tingwu/v2/tasks/{task_id}",
        "GetTaskInfo",
        body={},
    )


def poll_task(task_id: str, output_dir: Path) -> dict[str, Any]:
    interval = float(os.getenv("TINGWU_POLL_SECONDS", "20"))
    max_wait = float(os.getenv("TINGWU_MAX_WAIT_SECONDS", "21600"))
    started = time.monotonic()
    print(f"[3/4] Waiting for Tingwu task: {task_id}")
    while True:
        data = get_task_info(task_id)
        (output_dir / "tingwu_task_latest.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        task_data = data.get("Data") or {}
        status = str(task_data.get("TaskStatus") or "")
        print(f"Tingwu status: {status or 'UNKNOWN'}")
        if status in TERMINAL_STATUSES:
            if status not in {"COMPLETED", "SUCCESS"}:
                raise RuntimeError(
                    f"Tingwu task failed: {task_data.get('ErrorCode') or status} "
                    f"{task_data.get('ErrorMessage') or ''}".strip()
                )
            return data
        if time.monotonic() - started > max_wait:
            raise TimeoutError(f"Tingwu task timed out after {max_wait:g}s: {task_id}")
        time.sleep(interval)


def download_json(url: str, target: Path) -> dict[str, Any]:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    data = response.json()
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def result_url(result: dict[str, Any], name: str) -> str:
    value = result.get(name)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("Url", "url", "FileUrl", "file_url", "ResultUrl", "result_url"):
            nested = value.get(key)
            if isinstance(nested, str) and nested:
                return nested
    return ""


def seconds_from_timestamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number / 1000.0 if number > 1000 else number


def timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}"


def candidate_text(node: dict[str, Any]) -> str:
    for key in ("Text", "text", "Sentence", "sentence", "Content", "content", "Word", "word"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def candidate_start(node: dict[str, Any]) -> float:
    for key in ("BeginTime", "begin_time", "StartTime", "start_time", "Start", "start", "Begin", "begin"):
        if key in node:
            return seconds_from_timestamp(node.get(key))
    return 0.0


def candidate_speaker(node: dict[str, Any]) -> str:
    for key in ("SpeakerId", "speaker_id", "Speaker", "speaker", "ChannelId", "channel_id"):
        value = node.get(key)
        if value not in (None, ""):
            return f"发言人 {value}"
    return ""


def collect_timed_text(data: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            text = candidate_text(value)
            if text:
                rows.append(
                    {
                        "start": candidate_start(value),
                        "speaker": candidate_speaker(value),
                        "text": text,
                    }
                )
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[float, str, str]] = set()
    for row in sorted(rows, key=lambda item: (float(item["start"]), str(item["text"]))):
        key = (float(row["start"]), str(row.get("speaker") or ""), str(row["text"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def write_transcript(rows: list[dict[str, Any]], output_path: Path) -> None:
    if not rows:
        raise RuntimeError("Tingwu transcription result did not contain readable timed text.")
    lines: list[str] = []
    last_speaker = ""
    for row in rows:
        speaker = str(row.get("speaker") or "")
        speaker_prefix = f" {speaker}：" if speaker and speaker != last_speaker else " "
        lines.append(f"[{timestamp(float(row['start']))}]{speaker_prefix}{row['text']}")
        if speaker:
            last_speaker = speaker
    output_path.write_text("\n\n".join(lines).strip() + "\n", encoding="utf-8")


def transcribe_file_tingwu(input_path: Path, output_dir: Path, force: bool) -> None:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")
    if not is_supported_media_path(input_path):
        raise ValueError(f"Unsupported file type: {input_path.suffix}")
    validate_audio_stream(input_path)

    file_output_dir = output_dir / input_path.stem
    transcript_path = file_output_dir / "transcript.txt"
    if not force and transcript_path.exists():
        print(f"Skipping existing transcript: {transcript_path}")
        return

    file_output_dir.mkdir(parents=True, exist_ok=True)
    file_url, temporary_bucket, temporary_key = source_url_for(input_path)
    task_id = create_task(file_url, input_path.name)
    task_info = poll_task(task_id, file_output_dir)
    try:
        task_data = task_info.get("Data") or {}
        (file_output_dir / "transcript_tingwu_task.json").write_text(
            json.dumps(task_info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        result = task_data.get("Result") or {}
        transcription_url = result_url(result, "Transcription")
        if not transcription_url:
            raise RuntimeError("Tingwu task completed but returned no Transcription result URL.")

        print("[4/4] Downloading Tingwu result JSON")
        transcription_data = download_json(str(transcription_url), file_output_dir / "transcript_tingwu.json")
        text_polish_url = result_url(result, "TextPolish")
        if text_polish_url:
            try:
                download_json(str(text_polish_url), file_output_dir / "transcript_tingwu_text_polish.json")
            except Exception as exc:
                print(f"TextPolish download failed, continuing with timed transcription: {exc}")

        rows = collect_timed_text(transcription_data)
        write_transcript(rows, transcript_path)
        print(f"TXT : {transcript_path}")
        print(f"JSON: {file_output_dir / 'transcript_tingwu.json'}")
    finally:
        delete_temporary_object(temporary_bucket, temporary_key)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe inputs with Alibaba Cloud Tingwu.")
    parser.add_argument("--input-dir", type=Path, default=Path("inputs"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--files", nargs="*", default=[], help="Only process these file names from input-dir.")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    args.input_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    selected = set(args.files)
    files = [
        path
        for path in sorted(args.input_dir.iterdir())
        if path.is_file() and is_supported_media_path(path)
        and (not selected or path.name in selected)
    ]
    if not files:
        print(f"No audio/video files found in: {args.input_dir.resolve()}")
        return 0

    failed: list[Path] = []
    print(f"Found {len(files)} file(s). Provider: Alibaba Cloud Tingwu")
    for index, input_path in enumerate(files, start=1):
        print("")
        print(f"===== Tingwu [{index}/{len(files)}] {input_path.name} =====")
        try:
            transcribe_file_tingwu(input_path, args.output_dir, args.force)
        except Exception as exc:
            failed.append(input_path)
            print(f"Failed: {input_path.name}: {exc}", file=sys.stderr)

    if failed:
        print("")
        print("Finished with Tingwu transcription failures:")
        for path in failed:
            print(f"- {path}")
        return 1
    print("")
    print("All Tingwu transcriptions completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
