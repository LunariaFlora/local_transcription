$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

function Require-Venv {
    if (-not (Test-Path -LiteralPath ".\.venv\Scripts\python.exe")) {
        throw "Virtual environment not found. Run .\setup_local.ps1 first."
    }
}

function Ensure-WorkDirs {
    New-Item -ItemType Directory -Force -Path ".\inputs" | Out-Null
    New-Item -ItemType Directory -Force -Path ".\outputs" | Out-Null
    New-Item -ItemType Directory -Force -Path ".\audio_exports" | Out-Null
    New-Item -ItemType Directory -Force -Path ".\compressed_audio" | Out-Null
}

function Get-SettingValue {
    param([string]$Name)
    $json = & .\.venv\Scripts\python.exe .\src\settings.py
    $settings = $json | ConvertFrom-Json
    return $settings.$Name
}

function Set-SettingValue {
    param([string]$Name, [string]$Value)
    & .\.venv\Scripts\python.exe .\src\update_settings.py $Name $Value
}

function Run-WatchIfEnabled {
    $enabled = Get-SettingValue "watch_downloads"
    if ($enabled -ne $true) {
        return
    }
    $source = "$env:USERPROFILE\Downloads"
    $beam = Get-SettingValue "local_beam_size"
    Write-Host "Watch Downloads is enabled. Watching Downloads for up to 10 files / 30 minutes."
    & .\.venv\Scripts\python.exe .\src\watch_downloads.py --source-dir $source --model small --device cuda --compute-type float16 --beam-size $beam --ignore-existing --max-files 10 --max-minutes 30
}

function Pause-AfterTask {
    $autoClose = Get-SettingValue "auto_close_after_task"
    if ($autoClose -eq $true) {
        $minutes = Get-SettingValue "auto_close_minutes"
        if ($minutes -gt 0) {
            Write-Host "Task completed. Tool will exit after $minutes minute(s)."
            Start-Sleep -Seconds ([int]($minutes * 60))
        }
        exit 0
    }
    Write-Host ""
    Read-Host "Press Enter to return to menu"
}

Require-Venv
Ensure-WorkDirs

while ($true) {
    $returnToMain = $false
    Write-Host ""
    Write-Host "Local Transcription Tool"
    Write-Host "1. API Transcribe"
    Write-Host "2. Local Transcribe"
    Write-Host "3. Text Processing"
    Write-Host "4. Video Processing"
    Write-Host "5. Help"
    Write-Host "6. Settings"
    Write-Host "0. Exit"
    Write-Host ""

    $choice = Read-Host "Choose 0-6"

    switch ($choice) {
        "1" {
            while ($true) {
                Write-Host ""
                Write-Host "API Transcribe"
                Write-Host "Press Enter to start"
                Write-Host "0. Back"
                $sub = Read-Host "Choose"
                if ($sub -eq "0") { break }
                if ([string]::IsNullOrWhiteSpace($sub)) {
                    Run-WatchIfEnabled
                    & .\.venv\Scripts\python.exe .\src\transcribe_api.py
                    Pause-AfterTask
                    $returnToMain = $true
                    break
                } else {
                    Write-Host "No valid option selected."
                }
            }
        }
        "2" {
            while ($true) {
                Write-Host ""
                Write-Host "Local Transcribe"
                Write-Host "1. Use small"
                Write-Host "2. Use medium"
                Write-Host "0. Back"
                $sub = Read-Host "Choose 0-2"
                if ($sub -eq "0") { break }
                $beam = Get-SettingValue "local_beam_size"
                if ($sub -eq "1") {
                    & .\.venv\Scripts\python.exe .\src\transcribe_inputs.py --model small --device cuda --compute-type float16 --beam-size $beam
                    Pause-AfterTask
                    $returnToMain = $true
                    break
                } elseif ($sub -eq "2") {
                    & .\.venv\Scripts\python.exe .\src\transcribe_inputs.py --model medium --device cuda --compute-type float16 --beam-size $beam
                    Pause-AfterTask
                    $returnToMain = $true
                    break
                } else {
                    Write-Host "No valid option selected."
                }
            }
        }
        "3" {
            while ($true) {
                Write-Host ""
                Write-Host "Text Processing"
                Write-Host "1. Polish transcript"
                Write-Host "2. Summary and English title"
                Write-Host "0. Back"
                $sub = Read-Host "Choose 0-2"
                if ($sub -eq "0") { break }
                if ($sub -eq "1") {
                    & .\.venv\Scripts\python.exe .\src\text_tools.py polish
                    Pause-AfterTask
                    $returnToMain = $true
                    break
                } elseif ($sub -eq "2") {
                    & .\.venv\Scripts\python.exe .\src\text_tools.py summary --rename-folder
                    Pause-AfterTask
                    $returnToMain = $true
                    break
                } else {
                    Write-Host "No valid option selected."
                }
            }
        }
        "4" {
            while ($true) {
                Write-Host ""
                Write-Host "Video Processing"
                Write-Host "1. Export high-quality MP3"
                Write-Host "2. Compress only for Groq"
                Write-Host "0. Back"
                $sub = Read-Host "Choose 0-2"
                if ($sub -eq "0") { break }
                if ($sub -eq "1") {
                    & .\.venv\Scripts\python.exe .\src\video_tools.py high-quality-mp3
                    Pause-AfterTask
                    $returnToMain = $true
                    break
                } elseif ($sub -eq "2") {
                    & .\.venv\Scripts\python.exe .\src\video_tools.py compress-for-groq
                    Pause-AfterTask
                    $returnToMain = $true
                    break
                } else {
                    Write-Host "No valid option selected."
                }
            }
        }
        "5" {
            Write-Host ""
            Write-Host "Help"
            Write-Host "1 API Transcribe: Groq Whisper transcription with timestamps and automatic DeepSeek cleanup."
            Write-Host "2 Local Transcribe: faster-whisper/CTranslate2 on CUDA only. Choose small or medium."
            Write-Host "3 Text Processing: DeepSeek-only polish or summary/title generation."
            Write-Host "4 Video Processing: export podcast MP3 or prepare 24MiB Groq-safe audio chunks."
            Write-Host "Settings are saved in settings.json."
            Write-Host "Put source files in inputs. Read results in outputs."
            Write-Host "Run option 9 manually with: .\.venv\Scripts\python.exe .\src\check_gpu.py"
            Pause-AfterTask
        }
        "6" {
            while ($true) {
                Write-Host ""
                Write-Host "Settings"
                & .\.venv\Scripts\python.exe .\src\settings.py
                Write-Host ""
                Write-Host "1. Toggle Watch Downloads"
                Write-Host "2. Set local beam size"
                Write-Host "3. Toggle auto close after task"
                Write-Host "4. Set auto close minutes"
                Write-Host "0. Back"
                $sub = Read-Host "Choose 0-4"
                if ($sub -eq "0") { break }
                if ($sub -eq "1") {
                    $current = Get-SettingValue "watch_downloads"
                    Set-SettingValue "watch_downloads" (-not $current)
                } elseif ($sub -eq "2") {
                    $beam = Read-Host "Beam size"
                    Set-SettingValue "local_beam_size" $beam
                } elseif ($sub -eq "3") {
                    $current = Get-SettingValue "auto_close_after_task"
                    Set-SettingValue "auto_close_after_task" (-not $current)
                } elseif ($sub -eq "4") {
                    $minutes = Read-Host "Auto close minutes"
                    Set-SettingValue "auto_close_minutes" $minutes
                } else {
                    Write-Host "No valid option selected."
                }
            }
        }
        "0" {
            Write-Host "Exit."
            exit 0
        }
        default {
            Write-Host "No valid option selected."
        }
    }
}
