# MATRIX developer helper (Windows PowerShell).
# Usage:  .\scripts\dev.ps1 [install|test|lint|scan|run|doctor|backup]
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "test", "lint", "scan", "run", "doctor", "backup")]
    [string]$Task = "test"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

switch ($Task) {
    "install" {
        if (-not (Test-Path $venvPython)) { py -3.12 -m venv .venv }
        & $venvPython -m pip install --upgrade pip
        & $venvPython -m pip install -e ".[dev]"
    }
    "test" { & $python -m pytest -q }
    "lint" { & $python -m ruff check . }
    "scan" { & $python -m matrix_ai.security.secret_scan . }
    "run" { & $python -m matrix_ai serve }
    "doctor" { & $python -m matrix_ai doctor }
    "backup" { & $python -m matrix_ai backup }
}
