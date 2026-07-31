# Local Transcription Tool

Local transcription uses `faster-whisper` / CTranslate2 on CUDA. Text polish uses DeepSeek. Cloud transcription can use Tongyi Tingwu or Groq.

## Setup

```powershell
cd D:\Project\Codex\video_to_audio\local_transcription
.\setup_local.ps1
.\setup_gpu.ps1
```

Put source audio/video files into:

```text
D:\Project\Codex\video_to_audio\local_transcription\inputs
```

Start the menu:

```powershell
.\Start.ps1
```

Start the local web frontend:

```powershell
.\launch_frontend.ps1
```

Then open:

```text
http://127.0.0.1:8765
```

To use the computer as a server for a phone on the same Wi-Fi, keep the frontend running and open Settings to copy the displayed phone URL. The server listens on the local network by default. Windows may ask whether Python can communicate on private networks; allow private-network access. The computer must remain powered on while the phone uses the app.

Set `LOCAL_TRANSCRIPTION_HOST=127.0.0.1` before launching if you want to disable local-network access and return to computer-only mode.

There is also a desktop launcher:

```text
C:\Users\lenovo\Desktop\音频转写工作台.bat
```

It starts the backend in a hidden background process, opens the frontend in Microsoft Edge, and closes the startup command window.

The web frontend copies dragged or selected files into `inputs`, automatically selects files that have not been transcribed, and marks finished files as already transcribed. Click any file or output folder to change the current selection before running a task.

## YouTube Import

The web frontend includes a `YouTube` tab. It can read Watch Later, liked videos, favorites, or a pasted playlist link through `yt-dlp --cookies-from-browser chrome`, then lets you select videos to download into `inputs`.

You can also paste multiple YouTube links into the text box and click parse. Downloaded files appear in `inputs` and can be transcribed from the `转写` tab.

For pasted public video links, keep `下载时使用 Chrome 登录态` unchecked.

For private lists such as Watch Later, export YouTube cookies as a Netscape `cookies.txt` file and upload it in the YouTube tab. The app saves it as:

```text
D:\Project\Codex\video_to_audio\local_transcription\youtube_cookies.txt
```

When this file exists, playlist scanning and authenticated downloads use it before trying to read Chrome directly. This avoids Chrome cookie lock and DPAPI decryption failures.

### Chrome Extension Import

The project also includes a local Chrome extension:

```text
D:\Project\Codex\video_to_audio\local_transcription\browser_extension
```

Install it once:

```text
1. Open chrome://extensions
2. Enable Developer mode
3. Click Load unpacked
4. Select D:\Project\Codex\video_to_audio\local_transcription\browser_extension
```

After the desktop launcher has started the workbench, open a YouTube page in Chrome. Click the extension icon only when you want to import the current page. The extension does not inject a floating button into YouTube by default. Clicking the popup button collects visible video links from the current page and sends them to:

```text
http://127.0.0.1:8765/api/youtube-import-links
```

The local backend then opens the YouTube tab in Microsoft Edge. Pick the videos you want and click `下载选中视频到 inputs`.

Chrome does not allow an extension to force itself pinned/unhidden in the toolbar, and a Chrome extension cannot directly paste into Edge. This extension avoids both limits by using the local backend as the bridge.

## Menu

```text
1. API Transcribe
2. Local Transcribe
3. Text Processing
4. Video Processing
5. Help
6. Settings
0. Exit
```

## API Transcribe

The frontend defaults to Tongyi Tingwu. Groq remains available as the second online provider.

### Tongyi Tingwu

Tingwu offline transcription requires a public HTTP/HTTPS file URL. This is separate from the optional callback URL in the Tingwu project. Keep the project on active polling; do not enable the project-level OSS option marked for legacy APIs.

For local files, this tool uses a private OSS bucket only as temporary file transport. It uploads the file, creates a short-lived signed URL, starts a Tingwu v2 offline task, polls it, downloads the result JSON, and deletes the temporary OSS object after the completed result is retrieved. The final `transcript.txt` includes timestamps and speaker labels when available.

Add this to `.env`:

```text
ALIBABA_CLOUD_ACCESS_KEY_ID=your-access-key-id
ALIBABA_CLOUD_ACCESS_KEY_SECRET=your-access-key-secret
TINGWU_APP_KEY=your-tingwu-app-key
TINGWU_SOURCE_LANGUAGE=cn

ALIYUN_OSS_BUCKET=your-bucket-name
ALIYUN_OSS_ENDPOINT=https://oss-cn-beijing.aliyuncs.com
TINGWU_OSS_PREFIX=local-transcription/tingwu-inputs
TINGWU_DELETE_OSS_OBJECT=true
```

If you already have a public URL for a single test file, you can set `TINGWU_FILE_URL=...` and skip OSS upload for that run.

Tingwu output:

```text
outputs
└─ source-name
   ├─ transcript.txt
   ├─ transcript_tingwu.json
   ├─ transcript_tingwu_task.json
   └─ tingwu_task_latest.json
```

### Groq

Uses Groq Whisper API. Add this to `.env`:

```text
GROQ_API_KEY=gsk-your-groq-key-here
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_TRANSCRIBE_MODEL=whisper-large-v3-turbo
```

Before upload, audio is compressed to 16k mono MP3 at 64k and split under a 24MiB safety limit. If a chunk is too large, the chunk duration is automatically reduced and retried. Groq requests are spaced by 20 seconds to respect 3 requests/minute behavior.

Groq returns segment timestamps. DeepSeek then corrects obvious recognition errors, restores punctuation, and formats the final `transcript.txt` with `[HH:MM:SS]` markers.

## Local Transcribe

Local transcription only exposes CUDA options in the menu:

```text
1. Use small
2. Use medium
0. Back
```

The beam size is controlled in Settings. Output is converted to Simplified Chinese when `opencc-python-reimplemented` is installed.

## Text Processing

Uses DeepSeek only. Add this to `.env`:

```text
DEEPSEEK_API_KEY=sk-your-deepseek-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_TEXT_MODEL=deepseek-chat
```

Text tools:

```text
1. Polish transcript
2. Auto title and rename
3. Summary overview
0. Back
```

Polish creates `elegant_transcript.md`. Auto title reads the transcript and appends a safe English title to the original output folder name. Summary overview creates `summary.md`.

## Video Processing

```text
1. Export high-quality MP3
2. Compress only for Groq
0. Back
```

High-quality MP3 exports podcast-style audio to `audio_exports`.

Compress-only creates Groq-safe MP3 chunks in `compressed_audio` without uploading anything.

## Settings

Settings are saved in `settings.json`.

```text
watch_downloads
auto_title_rename
local_beam_size
auto_close_after_task
auto_close_minutes
```

Watch Downloads is off by default. When enabled, API Transcribe first watches the Downloads folder for up to 10 files or 30 minutes.

Auto close exits the tool after a task. If disabled, the menu waits for Enter and returns to the menu.

## Outputs

Local transcription:

```text
outputs
└─ source-name
   ├─ transcript.txt (formatted final text with timestamps)
   ├─ transcript.srt
   └─ transcript.json
```

Local transcription temporarily creates `audio.16k.wav` while running, then deletes it after successful transcription.

Groq API transcription:

```text
outputs
└─ source-name
   ├─ transcript.txt (formatted final text with timestamps)
   └─ transcript_api.json
```

Text processing may add:

```text
elegant_transcript.md
summary.md
```

## GPU Check

Run:

```powershell
.\.venv\Scripts\python.exe .\src\check_gpu.py
```

This checks `nvidia-smi` and starts a tiny `faster-whisper` CUDA test without PyTorch.

## Groq Spend Limit Note

The attached Groq spend-limit document describes paid-tier spending limits, not free-tier rate limits. Paid spend limits are organization-wide, reset monthly, update with a 10-15 minute delay, and block new API calls with `blocked_api_access` after the cap is reached.
