$ErrorActionPreference = "Stop"

param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path -LiteralPath ".\.venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run .\setup_local.ps1 first."
}

& .\.venv\Scripts\python.exe .\src\inspect_media.py $Path
