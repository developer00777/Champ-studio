<#
.SYNOPSIS
    Packs Champ Studio for Windows teammates: dist\ChampStudio-Windows-<version>.zip.

.DESCRIPTION
    The zip holds the engine source and the desktop app. Teammates unzip it and double-click
    "Install Champ Studio.cmd"; the first launch installs Docker Desktop if needed and builds the engine.
#>
param([string] $OutDir)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\Common.ps1')
# Windows PowerShell's .NET writes zip paths with backslashes unless told otherwise.
[AppContext]::SetSwitch('Switch.System.IO.Compression.ZipFile.UseBackslash', $false)
Add-Type -AssemblyName System.IO.Compression.FileSystem

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $OutDir) { $OutDir = Join-Path $repo 'dist' }
$version = Get-ChampVersion $repo
$stage = Join-Path $env:TEMP "champstudio-release-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$root = Join-Path $stage "Champ Studio $version"
$app = Join-Path $root 'desktop\windows'

Copy-ChampSource -From $repo -To $root
New-Item -ItemType Directory -Force -Path "$app\lib", "$app\assets" | Out-Null
Copy-Item -Path "$PSScriptRoot\ChampStudio.ps1", "$PSScriptRoot\install.ps1", "$PSScriptRoot\Uninstall.ps1", "$PSScriptRoot\compose.yml", "$PSScriptRoot\README.md" -Destination $app
Copy-Item -Path "$PSScriptRoot\lib\*.ps1" -Destination "$app\lib"
Copy-Item -Path "$PSScriptRoot\assets\champstudio.ico" -Destination "$app\assets"
# Installed copies update themselves from this repo and branch.
[ordered]@{
    repo       = (& git.exe -C $repo remote get-url origin)
    branch     = (& git.exe -C $repo rev-parse --abbrev-ref HEAD)
    commit     = (& git.exe -C $repo rev-parse HEAD)
    commitDate = [long](& git.exe -C $repo log -1 --format=%ct)
} | ConvertTo-Json | Set-Content -LiteralPath "$app\update.json" -Encoding ASCII
Set-Content -LiteralPath "$root\Install Champ Studio.cmd" -Encoding ASCII -Value @(
    '@echo off'
    'rem Installs Champ Studio for this Windows user (no administrator rights needed).'
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0desktop\windows\install.ps1"'
    'if errorlevel 1 pause'
)
Set-Content -LiteralPath "$root\README.txt" -Encoding ASCII -Value @(
    "Champ Studio $version for Windows 10/11"
    ''
    '1. Double-click "Install Champ Studio.cmd" (if Windows asks whether to run it, choose Run).'
    '2. Champ Studio opens. The first time it installs Docker Desktop if needed (choose Yes when'
    '   Windows asks for permission; a restart may be needed) and builds its engine (5-10 minutes).'
    '3. Next time, just double-click Champ Studio on your desktop.'
    ''
    'Add your OpenRouter key in Settings inside the app. More: desktop\windows\README.md'
)

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$zip = Join-Path $OutDir "ChampStudio-Windows-$version.zip"
Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
[System.IO.Compression.ZipFile]::CreateFromDirectory($root, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)
Remove-Item -LiteralPath $stage -Recurse -Force
Write-Host "Wrote $zip ($([Math]::Round((Get-Item $zip).Length / 1MB, 1)) MB)"
