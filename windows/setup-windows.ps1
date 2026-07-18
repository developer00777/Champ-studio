# Champ Studio — Windows one-shot setup (no Docker needed).
# Right-click → "Run with PowerShell", or:  powershell -ExecutionPolicy Bypass -File setup-windows.ps1
$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "== Champ Studio — Windows setup ==" -ForegroundColor DarkYellow

# 1. uv (standalone Python manager — no admin rights needed)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# 2. Engine environment (uv-managed CPython 3.12)
Set-Location "$RepoDir\engine"
uv venv --python-preference only-managed -p 3.12 .venv
uv pip install -p .venv\Scripts\python.exe -e .

# 3. Pre-fetch Remove-Background model weights (~170 MB, one time; skippable)
try {
    & .venv\Scripts\python.exe -c "from rembg import new_session; new_session('u2net')"
} catch {
    Write-Host "(weights will download on first use instead)"
}

Write-Host ""
Write-Host "Done. Start Champ Studio with:  windows\run-windows.bat" -ForegroundColor Green
Write-Host "Then open http://127.0.0.1:8787 and add your OpenRouter key in Settings."
