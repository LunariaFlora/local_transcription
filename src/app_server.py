from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from settings import load_settings, save_settings


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "web"
INPUTS_DIR = ROOT / "inputs"
OUTPUTS_DIR = ROOT / "outputs"
AUDIO_EXPORTS_DIR = ROOT / "audio_exports"
COMPRESSED_AUDIO_DIR = ROOT / "compressed_audio"
INFO_SOURCE_PYTHON = Path(r"D:\Project\Codex\信息源系统\.venv\Scripts\python.exe")
YOUTUBE_COOKIE_FILE = ROOT / "youtube_cookies.txt"
YOUTUBE_WATCH_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^ \t\r\n<>]*v=|shorts/)|youtu\.be/)[A-Za-z0-9_-]{6,}[^ \t\r\n<>]*"
)
YOUTUBE_PLAYLISTS = {
    "watchlater": "https://www.youtube.com/playlist?list=WL",
    "liked": "https://www.youtube.com/playlist?list=LL",
    "favorites": "https://www.youtube.com/playlist?list=FL",
}
MOBILE_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
MOBILE_SESSION_TTL_SECONDS = 24 * 60 * 60
MOBILE_SESSIONS_LOCK = threading.Lock()
MOBILE_SESSIONS: dict[str, dict[str, object]] = {}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}


def lan_ip_address() -> str:
    configured = os.getenv("LOCAL_TRANSCRIPTION_LAN_IP", "").strip()
    if configured:
        return configured

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = str(probe.getsockname()[0])
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass

    try:
        address = socket.gethostbyname(socket.gethostname())
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    return ""


def network_state() -> dict[str, str]:
    port = int(os.getenv("LOCAL_TRANSCRIPTION_PORT", "8765"))
    address = lan_ip_address()
    return {
        "local_url": f"http://127.0.0.1:{port}",
        "phone_url": f"http://{address}:{port}" if address else "",
    }


def bring_explorer_to_front(folder_name: str) -> bool:
    if os.name != "nt":
        return False

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    matches: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect_window(hwnd: int, _lparam: int) -> bool:
        class_name = ctypes.create_unicode_buffer(64)
        title = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        user32.GetWindowTextW(hwnd, title, len(title))
        if class_name.value == "CabinetWClass" and title.value.startswith(folder_name):
            matches.append(hwnd)
        return True

    user32.EnumWindows(callback_type(collect_window), 0)
    if not matches:
        return False

    hwnd = matches[-1]
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
    try:
        user32.ShowWindow(hwnd, 9)
        user32.BringWindowToTop(hwnd)
        return bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(current_thread, foreground_thread, False)


class TaskRunner:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen[str] | None = None
        self.logs: list[str] = []
        self.started_at: float | None = None
        self.name: str | None = None
        self.returncode: int | None = None

    def status(self) -> dict[str, object]:
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            return {
                "running": running,
                "name": self.name,
                "started_at": self.started_at,
                "returncode": None if running else self.returncode,
                "logs": self.logs[-500:],
            }

    def append(self, text: str) -> None:
        with self.lock:
            self.logs.append(text.rstrip())
            self.logs = self.logs[-1000:]

    def start(self, name: str, args: list[str]) -> None:
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("A task is already running.")
            self.logs = []
            self.name = name
            self.started_at = time.time()
            self.returncode = None

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            self.process = subprocess.Popen(
                [sys.executable, *args],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )

        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            self.append(line)
        code = process.wait()
        with self.lock:
            self.returncode = code
            self.logs.append(f"[task exited with code {code}]")

    def stop(self) -> None:
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                return
            self.process.terminate()
            self.logs.append("[stop requested]")


RUNNER = TaskRunner()
YOUTUBE_IMPORTED_ITEMS: list[dict[str, object]] = []
YOUTUBE_IMPORTED_AT: float | None = None


def json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


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


def normalize_youtube_url(value: str) -> str | None:
    value = value.strip()
    match = YOUTUBE_WATCH_RE.search(value)
    if not match:
        return None
    url = match.group(0).rstrip(".,，。)）]】")
    parsed = urlparse(url)
    if parsed.netloc.endswith("youtu.be"):
        video_id = parsed.path.strip("/")
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else None
    if parsed.path.startswith("/shorts/"):
        video_id = parsed.path.removeprefix("/shorts/").strip("/")
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else None
    video_id = (parse_qs(parsed.query).get("v") or [""])[0]
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else None


def extract_youtube_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in YOUTUBE_WATCH_RE.finditer(text):
        url = normalize_youtube_url(match.group(0))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def selected_urls(payload: dict[str, object]) -> list[str]:
    raw_urls = payload.get("urls")
    if not isinstance(raw_urls, list):
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for item in raw_urls:
        url = normalize_youtube_url(str(item))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def normalize_youtube_items(raw_items: object) -> list[dict[str, object]]:
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        if isinstance(raw_item, dict):
            url = normalize_youtube_url(str(raw_item.get("url") or ""))
            title = str(raw_item.get("title") or url or "").strip()
            channel = str(raw_item.get("channel") or "").strip()
            duration = raw_item.get("duration") or ""
        else:
            url = normalize_youtube_url(str(raw_item))
            title = url or ""
            channel = ""
            duration = ""
        if not url or url in seen:
            continue
        seen.add(url)
        items.append(
            {
                "title": title or url,
                "url": url,
                "channel": channel,
                "duration": duration,
                "id": (parse_qs(urlparse(url).query).get("v") or [""])[0],
            }
        )
    return items


def imported_youtube_payload() -> dict[str, object]:
    return {
        "ok": True,
        "items": YOUTUBE_IMPORTED_ITEMS,
        "imported_at": YOUTUBE_IMPORTED_AT,
        "count": len(YOUTUBE_IMPORTED_ITEMS),
    }


def open_edge_frontend(path: str = "/?view=youtube&imported=1") -> None:
    url = f"http://127.0.0.1:{os.getenv('LOCAL_TRANSCRIPTION_PORT', '8765')}{path}"
    if os.name == "nt":
        subprocess.Popen(
            ["msedge.exe", url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    else:
        webbrowser.open(url)


def scan_youtube_playlist(playlist_url: str, limit: int) -> list[dict[str, object]]:
    command = [
        yt_dlp_python(),
        "-m",
        "yt_dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--ignore-errors",
        "--no-warnings",
    ]
    if YOUTUBE_COOKIE_FILE.exists():
        command.extend(["--cookies", str(YOUTUBE_COOKIE_FILE)])
    else:
        command.extend(["--cookies-from-browser", "chrome"])
    if limit > 0:
        command.extend(["--playlist-end", str(limit)])
    command.append(playlist_url)

    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or "yt-dlp 读取播放列表失败。")

    data = json.loads(result.stdout)
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in data.get("entries") or []:
        if not entry:
            continue
        video_id = entry.get("id") or entry.get("url")
        webpage_url = str(entry.get("webpage_url") or entry.get("url") or "")
        url = normalize_youtube_url(webpage_url)
        if not url and video_id:
            url = f"https://www.youtube.com/watch?v={video_id}"
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append(
            {
                "title": entry.get("title") or "(untitled)",
                "url": url,
                "channel": entry.get("channel") or entry.get("uploader") or "",
                "duration": entry.get("duration") or "",
                "id": video_id or "",
            }
        )
    return rows


def friendly_youtube_error(detail: str) -> str:
    if "Could not copy Chrome cookie database" in detail:
        return (
            "无法读取 Chrome 登录态：Chrome 正在占用 cookie 数据库，yt-dlp 复制失败。"
            "建议导出 YouTube cookies.txt 并在本页上传；之后扫描私有列表就不需要直接读取 Chrome 数据库。"
            "如果只是下载公开视频，不要勾选“下载时使用 Chrome 登录态”。"
        )
    if "Failed to decrypt with DPAPI" in detail:
        return (
            "无法解密 Chrome cookies：Chrome 使用了当前 yt-dlp 不能直接解开的 DPAPI 加密。"
            "请导出 YouTube cookies.txt 并在本页上传；上传后会优先使用 cookies.txt 扫描私有列表。"
        )
    return detail


def has_transcript_for_stem(stem: str) -> bool:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    for folder in OUTPUTS_DIR.iterdir():
        if not folder.is_dir():
            continue
        if folder.name == stem or folder.name.startswith(f"{stem}--"):
            if (folder / "transcript.txt").exists():
                return True
    return False


def list_inputs() -> list[dict[str, object]]:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for path in sorted(INPUTS_DIR.iterdir()):
        if not path.is_file():
            continue
        output_folder = OUTPUTS_DIR / path.stem
        rows.append(
            {
                "name": path.name,
                "stem": path.stem,
                "size": path.stat().st_size,
                "mtime": path.stat().st_mtime,
                "transcribed": (output_folder / "transcript.txt").exists() or has_transcript_for_stem(path.stem),
            }
        )
    return rows


def list_outputs() -> list[dict[str, object]]:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for folder in sorted(OUTPUTS_DIR.iterdir()):
        if not folder.is_dir():
            continue
        files = sorted(path.name for path in folder.iterdir() if path.is_file())
        stat = folder.stat()
        rows.append(
            {
                "name": folder.name,
                "files": files,
                "mtime": stat.st_mtime,
                "has_transcript": "transcript.txt" in files,
                "has_polished": "elegant_transcript.md" in files,
                "has_title": "--" in folder.name,
                "has_summary": "summary.md" in files,
            }
        )
    return rows


def valid_mobile_session_id(value: str) -> bool:
    return bool(MOBILE_SESSION_RE.fullmatch(value))


def mobile_session(session_id: str, *, create: bool = True) -> dict[str, object] | None:
    if not valid_mobile_session_id(session_id):
        return None

    now = time.time()
    with MOBILE_SESSIONS_LOCK:
        expired = [
            key
            for key, value in MOBILE_SESSIONS.items()
            if now - float(value.get("created_at", now)) > MOBILE_SESSION_TTL_SECONDS
        ]
        for key in expired:
            MOBILE_SESSIONS.pop(key, None)

        session = MOBILE_SESSIONS.get(session_id)
        if session is None and create:
            session = {
                "created_at": now,
                "inputs": {},
                "task_started_at": None,
            }
            MOBILE_SESSIONS[session_id] = session
        return session


def mobile_upload_name(filename: str, session_id: str) -> str:
    path = Path(filename)
    candidate = path.name
    stem = path.stem or "mobile-upload"
    suffix = path.suffix
    if not (INPUTS_DIR / candidate).exists() and not has_transcript_for_stem(stem):
        return candidate

    short_id = session_id[:8]
    candidate = f"{stem}--phone-{short_id}{suffix}"
    index = 2
    while (INPUTS_DIR / candidate).exists() or has_transcript_for_stem(Path(candidate).stem):
        candidate = f"{stem}--phone-{short_id}-{index}{suffix}"
        index += 1
    return candidate


def mobile_session_state(session_id: str) -> dict[str, object] | None:
    session = mobile_session(session_id)
    if session is None:
        return None

    raw_inputs = session.get("inputs", {})
    session_inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
    display_names = {str(name): str(display) for name, display in session_inputs.items()}
    inputs = []
    for row in list_inputs():
        name = str(row["name"])
        if name not in display_names:
            continue
        inputs.append({**row, "display_name": display_names[name]})

    stems = sorted({Path(name).stem for name in display_names}, key=len, reverse=True)
    created_at = float(session.get("created_at", 0.0))
    outputs = []
    for row in list_outputs():
        folder_name = str(row["name"])
        if float(row["mtime"]) + 2 < created_at:
            continue
        source_stem = next(
            (stem for stem in stems if folder_name == stem or folder_name.startswith(f"{stem}--")),
            None,
        )
        if source_stem is None:
            continue
        original = display_names.get(next((name for name in display_names if Path(name).stem == source_stem), ""), source_stem)
        outputs.append({**row, "display_name": Path(original).stem})

    task = RUNNER.status()
    if session.get("task_started_at") != task.get("started_at"):
        task = {
            "running": False,
            "name": None,
            "started_at": None,
            "returncode": None,
            "logs": [],
        }
    return {
        "settings": load_settings(),
        "inputs": inputs,
        "outputs": outputs,
        "task": task,
        "network": network_state(),
        "mobile_session": True,
    }


def mobile_session_owns_output(session_id: str, folder_name: str) -> bool:
    state = mobile_session_state(session_id)
    if state is None:
        return False
    return any(str(row["name"]) == folder_name for row in state["outputs"])


def selected_names(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        name = Path(str(item)).name
        if name:
            names.append(name)
    return names


def append_files(args: list[str], files: list[str]) -> list[str]:
    if files:
        args = [*args, "--files", *files]
    return args


def append_folders(args: list[str], folders: list[str]) -> list[str]:
    if folders:
        args = [*args, "--folders", *folders]
    return args


def task_args(task: str, payload: dict[str, object]) -> tuple[str, list[str]]:
    settings = load_settings()
    beam = str(settings.get("local_beam_size", 2))
    files = selected_names(payload, "files")
    folders = selected_names(payload, "folders")
    if task == "api":
        return "Groq API Transcribe", append_files(["src/transcribe_api.py"], files)
    if task == "api-tingwu":
        return "Tingwu API Transcribe", append_files(["src/transcribe_tingwu.py"], files)
    if task == "local-small":
        return "Local Transcribe small", append_files(
            [
                "src/transcribe_inputs.py",
                "--model",
                "small",
                "--device",
                "cuda",
                "--compute-type",
                "float16",
                "--beam-size",
                beam,
            ],
            files,
        )
    if task == "local-medium":
        return "Local Transcribe medium", append_files(
            [
                "src/transcribe_inputs.py",
                "--model",
                "medium",
                "--device",
                "cuda",
                "--compute-type",
                "float16",
                "--beam-size",
                beam,
            ],
            files,
        )
    if task == "polish":
        return "Text Polish", append_folders(["src/text_tools.py", "polish"], folders)
    if task == "title":
        args = append_folders(["src/text_tools.py", "title"], folders)
        if settings.get("auto_title_rename", True):
            args.append("--rename-folder")
        return "Title and Rename", args
    if task == "summary":
        return "Summary", append_folders(["src/text_tools.py", "summary"], folders)
    if task == "high-quality-mp3":
        return "High-quality MP3", append_files(["src/video_tools.py", "high-quality-mp3"], files)
    if task == "youtube-download":
        urls = selected_urls(payload)
        if not urls:
            raise RuntimeError("没有可下载的 YouTube 链接。")
        args = ["src/youtube_download_inputs.py", "--urls", *urls]
        if payload.get("use_cookies"):
            args.append("--cookies-from-chrome")
        if payload.get("audio_only"):
            args.append("--audio-only")
        return "YouTube Download", args
    if task == "compress-groq":
        return "Compress for Groq", append_files(["src/video_tools.py", "compress-for-groq"], files)
    if task == "check-gpu":
        return "Check CUDA GPU", ["src/check_gpu.py"]
    raise RuntimeError(f"Unknown task: {task}")


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalTranscriptionFrontend/1.0"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Mobile-Session")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            query = parse_qs(parsed.query)
            session_id = str(query.get("session_id", [""])[0])
            if query.get("mobile", [""])[0] == "1":
                state = mobile_session_state(session_id)
                if state is None:
                    self.send_json({"ok": False, "error": "无效的手机会话。"}, status=400)
                else:
                    self.send_json(state)
                return
            self.send_json(
                {
                    "settings": load_settings(),
                    "inputs": list_inputs(),
                    "outputs": list_outputs(),
                    "task": RUNNER.status(),
                    "network": network_state(),
                }
            )
            return
        if parsed.path == "/api/download-transcript":
            query = parse_qs(parsed.query)
            session_id = str(query.get("session_id", [""])[0])
            folder_name = Path(str(query.get("folder", [""])[0])).name
            if not folder_name or not mobile_session_owns_output(session_id, folder_name):
                self.send_json({"ok": False, "error": "找不到本次会话的文字稿。"}, status=404)
                return
            transcript = (OUTPUTS_DIR / folder_name / "transcript.txt").resolve()
            if OUTPUTS_DIR.resolve() not in transcript.parents or not transcript.is_file():
                self.send_json({"ok": False, "error": "文字稿尚未生成。"}, status=404)
                return
            self.send_file_download(transcript, f"{folder_name}-文字稿.txt")
            return
        if parsed.path == "/api/logs":
            self.send_json(RUNNER.status())
            return
        if parsed.path == "/api/youtube-config":
            self.send_json(
                {
                    "playlists": YOUTUBE_PLAYLISTS,
                    "cookie_file_exists": YOUTUBE_COOKIE_FILE.exists(),
                    "cookie_file": str(YOUTUBE_COOKIE_FILE),
                }
            )
            return
        if parsed.path == "/api/youtube-imported":
            self.send_json(imported_youtube_payload())
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/run":
            payload = self.read_json()
            session_id = self.mobile_session_id()
            session = mobile_session(session_id, create=False) if session_id else None
            if session_id and session is None:
                self.send_json({"ok": False, "error": "手机会话已失效，请刷新页面。"}, status=400)
                return
            if session is not None:
                task = str(payload.get("task"))
                if task not in {"api", "api-tingwu"}:
                    self.send_json({"ok": False, "error": "手机端仅支持在线转写。"}, status=400)
                    return
                owned = session.get("inputs", {})
                owned_names = set(owned) if isinstance(owned, dict) else set()
                files = selected_names(payload, "files")
                if not files or any(name not in owned_names for name in files):
                    self.send_json({"ok": False, "error": "请选择本次上传的文件。"}, status=400)
                    return
            name, args = task_args(str(payload.get("task")), payload)
            try:
                RUNNER.start(name, args)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=409)
                return
            if session is not None:
                session["task_started_at"] = RUNNER.status()["started_at"]
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/youtube-scan":
            payload = self.read_json()
            source = str(payload.get("source", "watchlater"))
            playlist_url = str(payload.get("playlist_url") or YOUTUBE_PLAYLISTS.get(source) or "").strip()
            if not playlist_url:
                self.send_json({"ok": False, "error": "缺少播放列表链接。"}, status=400)
                return
            try:
                items = scan_youtube_playlist(playlist_url, int(payload.get("limit") or 80))
            except Exception as exc:
                self.send_json({"ok": False, "error": friendly_youtube_error(str(exc))}, status=502)
                return
            self.send_json({"ok": True, "items": items})
            return
        if parsed.path == "/api/youtube-parse-links":
            payload = self.read_json()
            urls = extract_youtube_urls(str(payload.get("text", "")))
            self.send_json(
                {
                    "ok": True,
                    "items": [
                        {"title": url, "url": url, "channel": "", "duration": "", "id": ""}
                        for url in urls
                    ],
                }
            )
            return
        if parsed.path == "/api/youtube-import-links":
            global YOUTUBE_IMPORTED_AT, YOUTUBE_IMPORTED_ITEMS
            payload = self.read_json()
            raw_items: object = payload.get("items")
            if raw_items is None:
                raw_items = payload.get("urls")
            items = normalize_youtube_items(raw_items)
            if not items and payload.get("text"):
                items = normalize_youtube_items(extract_youtube_urls(str(payload.get("text", ""))))
            if not items:
                self.send_json({"ok": False, "error": "没有识别到 YouTube 视频链接。"}, status=400)
                return
            YOUTUBE_IMPORTED_ITEMS = items
            YOUTUBE_IMPORTED_AT = time.time()
            if payload.get("open_edge") is not False:
                try:
                    open_edge_frontend()
                except Exception:
                    pass
            self.send_json(imported_youtube_payload())
            return
        if parsed.path == "/api/youtube-cookies":
            self.handle_youtube_cookies_upload()
            return
        if parsed.path == "/api/stop":
            RUNNER.stop()
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/open-folder":
            payload = self.read_json()
            folder_name = str(payload.get("folder", "outputs"))
            folders = {
                "inputs": INPUTS_DIR,
                "outputs": OUTPUTS_DIR,
                "audio_exports": AUDIO_EXPORTS_DIR,
                "compressed_audio": COMPRESSED_AUDIO_DIR,
            }
            folder = folders.get(folder_name)
            if folder is None:
                self.send_json({"ok": False, "error": "Unknown folder."}, status=400)
                return
            folder.mkdir(parents=True, exist_ok=True)
            try:
                if os.name == "nt":
                    subprocess.Popen(
                        ["explorer.exe", "/n,", str(folder)],
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    )
                    for _ in range(12):
                        time.sleep(0.1)
                        if bring_explorer_to_front(folder.name):
                            break
                else:
                    subprocess.Popen(["xdg-open", str(folder)])
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
                return
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/settings":
            payload = self.read_json()
            settings = load_settings()
            for key, value in payload.items():
                if key in settings:
                    settings[key] = value
            save_settings(settings)
            self.send_json({"ok": True, "settings": settings})
            return
        if parsed.path == "/api/upload":
            self.handle_upload()
            return
        self.send_error(404)

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def handle_upload(self) -> None:
        INPUTS_DIR.mkdir(parents=True, exist_ok=True)
        content_type = self.headers.get("Content-Type", "")
        match = re.search(r"boundary=(.+)", content_type)
        if not match:
            self.send_json({"ok": False, "error": "Missing multipart boundary."}, status=400)
            return

        boundary = match.group(1).strip().strip('"').encode("utf-8")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        uploaded: list[str] = []
        session_id = self.mobile_session_id()
        session = mobile_session(session_id) if session_id else None
        if session_id and session is None:
            self.send_json({"ok": False, "error": "无效的手机会话。"}, status=400)
            return

        for raw_part in body.split(b"--" + boundary):
            if not raw_part or raw_part in {b"--\r\n", b"--"}:
                continue
            raw_part = raw_part.strip(b"\r\n")
            if b"\r\n\r\n" not in raw_part:
                continue
            header_bytes, data = raw_part.split(b"\r\n\r\n", 1)
            headers = header_bytes.decode("utf-8", errors="replace")
            filename_match = re.search(r'filename="([^"]+)"', headers)
            if not filename_match:
                continue
            filename = Path(filename_match.group(1)).name
            if not filename:
                continue
            if data.endswith(b"\r\n"):
                data = data[:-2]
            target_name = mobile_upload_name(filename, session_id) if session is not None else filename
            target = INPUTS_DIR / target_name
            with target.open("wb") as handle:
                handle.write(data)
            uploaded.append(target.name)
            if session is not None:
                inputs = session.get("inputs", {})
                if isinstance(inputs, dict):
                    inputs[target.name] = filename
        state_inputs = mobile_session_state(session_id)["inputs"] if session is not None else list_inputs()
        self.send_json({"ok": True, "uploaded": uploaded, "inputs": state_inputs})

    def handle_youtube_cookies_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if "multipart/form-data" in content_type:
            match = re.search(r"boundary=(.+)", content_type)
            if not match:
                self.send_json({"ok": False, "error": "Missing multipart boundary."}, status=400)
                return
            boundary = match.group(1).strip().strip('"').encode("utf-8")
            cookie_data = b""
            for raw_part in body.split(b"--" + boundary):
                raw_part = raw_part.strip(b"\r\n")
                if b"\r\n\r\n" not in raw_part:
                    continue
                headers, data = raw_part.split(b"\r\n\r\n", 1)
                if b"filename=" not in headers:
                    continue
                cookie_data = data[:-2] if data.endswith(b"\r\n") else data
                break
        else:
            cookie_data = body

        text = cookie_data.decode("utf-8", errors="replace")
        if "# Netscape HTTP Cookie File" not in text and ".youtube.com" not in text and "youtube.com" not in text:
            self.send_json({"ok": False, "error": "这不像 YouTube cookies.txt 文件。"}, status=400)
            return
        YOUTUBE_COOKIE_FILE.write_text(text, encoding="utf-8")
        self.send_json({"ok": True, "cookie_file": str(YOUTUBE_COOKIE_FILE)})

    def serve_static(self, route: str) -> None:
        relative = "index.html" if route in {"", "/"} else route.lstrip("/")
        path = (WEB_DIR / relative).resolve()
        if WEB_DIR.resolve() not in path.parents and path != WEB_DIR.resolve():
            self.send_error(403)
            return
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        content_type = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def mobile_session_id(self) -> str:
        return self.headers.get("X-Mobile-Session", "").strip()

    def send_file_download(self, path: Path, download_name: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(download_name)}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: object, status: int = 200) -> None:
        data = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    port = int(os.getenv("LOCAL_TRANSCRIPTION_PORT", "8765"))
    host = os.getenv("LOCAL_TRANSCRIPTION_HOST", "0.0.0.0").strip() or "0.0.0.0"
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Local frontend: {url}")
    phone_url = network_state()["phone_url"]
    if host not in {"127.0.0.1", "localhost"} and phone_url:
        print(f"Phone on the same Wi-Fi: {phone_url}")
    if os.getenv("LOCAL_TRANSCRIPTION_NO_BROWSER") != "1":
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
