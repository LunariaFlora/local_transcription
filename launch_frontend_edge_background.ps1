$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$server = Join-Path $root "src\app_server.py"
$url = "http://127.0.0.1:8765/"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Run setup_local.ps1 first."
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

$env:LOCAL_TRANSCRIPTION_NO_BROWSER = "1"
Start-Process -FilePath $python -ArgumentList $server -WorkingDirectory $root -WindowStyle Hidden

for ($i = 0; $i -lt 40; $i++) {
    try {
        $connection = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop
        if ($connection) { break }
    } catch {
    }
    Start-Sleep -Milliseconds 250
}

Start-Process -FilePath "msedge.exe" -ArgumentList $url
