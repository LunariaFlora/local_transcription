$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found on PATH. Install Python 3.10+ first."
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg was not found on PATH. Install ffmpeg first."
}

python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -U pip
& .\.venv\Scripts\pip.exe install -r requirements.txt

Write-Host ""
Write-Host "Local transcription environment is ready."
Write-Host "Run:"
Write-Host "  .\.venv\Scripts\activate"
Write-Host "  python .\transcribe_local.py `"D:\path\to\audio.mp3`""
