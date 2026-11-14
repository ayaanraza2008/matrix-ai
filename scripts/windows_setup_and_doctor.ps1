<#
.SYNOPSIS
    MATRIX AI — One-Command Windows Production Setup & Diagnostic Experience
.DESCRIPTION
    Verifies host compatibility, installs dependencies, validates local SQLite storage,
    runs integrity tests, tests Windows SAPI voice capability, inspects scheduled startup tasks,
    and produces a comprehensive system health report.
#>

param(
    [switch]$InstallDeps = $false,
    [switch]$SkipTests = $false,
    [switch]$VerboseOutput = $false
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   MATRIX AI — Windows Production Setup & Diagnostics" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Host Architecture & OS
$os = [System.Environment]::OSVersion.VersionString
$arch = [System.Environment]::GetEnvironmentVariable("PROCESSOR_ARCHITECTURE")
$cores = [System.Environment]::ProcessorCount
Write-Host "[1/7] Host Architecture & OS:" -ForegroundColor Green
Write-Host "      OS: $os"
Write-Host "      Architecture: $arch"
Write-Host "      Logical Cores: $cores"

# 2. Python Environment Detection
Write-Host "[2/7] Python Runtime Detection:" -ForegroundColor Green
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

$pyVersion = & $python --version 2>&1
Write-Host "      Interpreter: $python"
Write-Host "      Version: $pyVersion"

# Optional dependency install
if ($InstallDeps) {
    Write-Host "      Installing/upgrading dependencies with pip..."
    & $python -m pip install --upgrade pip
    & $python -m pip install -e ".[dev]"
}

# 3. Storage & Directory Structure
Write-Host "[3/7] Local Storage & Data Directories:" -ForegroundColor Green
$localAppData = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::LocalApplicationData)
$matrixHome = Join-Path $localAppData "MATRIX"
$matrixDb = Join-Path $matrixHome "matrix.db"
$workspace = Join-Path $matrixHome "workspace"
$backups = Join-Path $matrixHome "backups"

New-Item -ItemType Directory -Force -Path $matrixHome | Out-Null
New-Item -ItemType Directory -Force -Path $workspace | Out-Null
New-Item -ItemType Directory -Force -Path $backups | Out-Null

Write-Host "      Home: $matrixHome"
Write-Host "      Database Path: $matrixDb"
Write-Host "      Workspace: $workspace"
Write-Host "      Backups: $backups"

# 4. SQLite Integrity Verification & Backup
Write-Host "[4/7] Database Integrity & Backup:" -ForegroundColor Green
if (Test-Path $matrixDb) {
    Write-Host "      Existing matrix.db found. Creating verified backup..."
    & $python -m matrix_ai backup
} else {
    Write-Host "      Fresh environment: matrix.db will be initialized upon first serve."
}

# 5. Windows SAPI Voice Stack Preflight
Write-Host "[5/7] Windows SAPI Voice Stack Diagnostic:" -ForegroundColor Green
try {
    Add-Type -AssemblyName System.Speech -ErrorAction Stop
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $voices = $synth.GetInstalledVoices()
    Write-Host "      System.Speech SAPI: Available" -ForegroundColor Green
    Write-Host "      Installed Voices count: $($voices.Count)"
    foreach ($v in $voices) {
        Write-Host "      - $($v.VoiceInfo.Name) ($($v.VoiceInfo.Culture))"
    }
} catch {
    Write-Host "      System.Speech SAPI not active or not installed in current session." -ForegroundColor Yellow
}

# 6. Scheduled Startup Task Check
Write-Host "[6/7] Windows Startup Task Status:" -ForegroundColor Green
$schtasks = Get-Command "schtasks.exe" -ErrorAction SilentlyContinue
if ($schtasks) {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    $taskOutput = & schtasks.exe /query /tn "MATRIX AI" 2>&1
    $taskExitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
    if ($taskExitCode -eq 0) {
        Write-Host "      Startup Task 'MATRIX AI': Installed and Registered" -ForegroundColor Green
    } else {
        Write-Host "      Startup Task 'MATRIX AI': Not installed (install with: matrix startup install)" -ForegroundColor Yellow
    }
} else {
    Write-Host "      schtasks.exe not found on PATH." -ForegroundColor Yellow
}

# 7. Core Health & Doctor Report
Write-Host "[7/7] Executing MATRIX Doctor Smoke Check:" -ForegroundColor Green
& $python -m matrix_ai doctor

if (-not $SkipTests) {
    Write-Host "`nRunning test verification..." -ForegroundColor Green
    & $python -m pytest tests/test_windows_startup_diagnostics.py -v
}

Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host "   Windows Setup & Diagnostics Complete!" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
