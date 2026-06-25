$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath ".\.venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run .\setup_local.ps1 first."
}

& .\.venv\Scripts\pip.exe install nvidia-cublas-cu12 nvidia-cudnn-cu12==9.*

Write-Host ""
Write-Host "GPU Python packages installed."
Write-Host "Test with:"
Write-Host "  .\run_inputs_gpu.ps1"
Write-Host ""
Write-Host "If CUDA DLL errors appear, your Windows CUDA/cuDNN path still needs manual fixing."
