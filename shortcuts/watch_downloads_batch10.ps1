$ErrorActionPreference = "Stop"

param(
    [string]$SourceDir = "$env:USERPROFILE\Downloads"
)

Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path -LiteralPath ".\.venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run .\setup_local.ps1 first."
}

New-Item -ItemType Directory -Force -Path ".\inputs" | Out-Null
New-Item -ItemType Directory -Force -Path ".\outputs" | Out-Null

& .\.venv\Scripts\python.exe .\src\watch_downloads.py --source-dir $SourceDir --model small --ignore-existing --max-files 10 --max-minutes 30
