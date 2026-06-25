$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath ".\.venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run .\setup_local.ps1 first."
}

$existing = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
foreach ($conn in $existing) {
    try {
        $proc = Get-Process -Id $conn.OwningProcess -ErrorAction Stop
        if ($proc.ProcessName -match "python") {
            Stop-Process -Id $proc.Id -Force
        }
    } catch {
    }
}

& .\.venv\Scripts\python.exe .\src\app_server.py
