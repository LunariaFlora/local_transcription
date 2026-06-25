$ErrorActionPreference = "Stop"

Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path -LiteralPath ".\.venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run .\setup_local.ps1 first."
}

New-Item -ItemType Directory -Force -Path ".\inputs" | Out-Null
New-Item -ItemType Directory -Force -Path ".\outputs" | Out-Null

& .\.venv\Scripts\python.exe .\src\transcribe_inputs.py --model small --device cuda --compute-type float16 --beam-size 2
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
