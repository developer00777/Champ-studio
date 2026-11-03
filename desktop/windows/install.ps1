<#
.SYNOPSIS
    Installs Champ Studio for this Windows user. Run it again to update.

.DESCRIPTION
    Copies the app and the engine source to %LOCALAPPDATA%\Programs\Champ Studio, adds Desktop and
    Start menu shortcuts and an entry in Settings > Apps, then opens Champ Studio. No administrator
    rights needed; the first launch installs Docker Desktop if it's missing and builds the engine.

    It also records the git repo, branch and commit it came from (from this checkout, or from
    update.json in a release zip). On every start the app fetches that branch and, if there's a
    newer commit, runs this installer again with -Update and restarts into the new version.
#>
param([switch] $NoLaunch, [switch] $NoDesktopShortcut, [switch] $Update)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\Common.ps1')

$from = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dest = Join-Path $env:LOCALAPPDATA 'Programs\Champ Studio'
$data = Join-Path $env:LOCALAPPDATA 'ChampStudio'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$launch = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$dest\ChampStudio.ps1`""

$running = $null
if (-not $Update -and [System.Threading.Mutex]::TryOpenExisting('Local\ChampStudio.Desktop', [ref]$running)) {
    $running.Dispose()
    Write-Host 'Champ Studio is open; the update applies the next time you open it.' -ForegroundColor Yellow
    $NoLaunch = $true
}

Write-Host "Installing Champ Studio to $dest"
New-Item -ItemType Directory -Force -Path $dest, "$dest\lib", "$dest\assets", $data | Out-Null
Copy-Item -Force -Path "$PSScriptRoot\ChampStudio.ps1", "$PSScriptRoot\Uninstall.ps1", "$PSScriptRoot\compose.yml" -Destination $dest
Copy-Item -Force -Path "$PSScriptRoot\lib\*.ps1" -Destination "$dest\lib"
Copy-Item -Force -Path "$PSScriptRoot\assets\champstudio.ico" -Destination "$dest\assets"
# Shortcuts get the icon under a name that changes with its content: Windows caches shortcut icons
# by path, so a new logo under the old name would keep showing the old picture.
$iconTag = (Get-FileHash -Algorithm SHA256 -LiteralPath "$dest\assets\champstudio.ico").Hash.Substring(0, 8).ToLower()
$shellIcon = "$dest\assets\champstudio-$iconTag.ico"
Get-ChildItem -LiteralPath "$dest\assets" -Filter 'champstudio-*.ico' | Where-Object { $_.FullName -ne $shellIcon } | Remove-Item -Force
Copy-Item -Force -LiteralPath "$dest\assets\champstudio.ico" -Destination $shellIcon
Write-Host '  Copying the engine source'
Copy-ChampSource -From $from -To "$dest\source"

$version = Get-ChampVersion "$dest\source"
$build = "$version-$(Get-ChampSourceHash "$dest\source")"

# Where updates come from: this checkout's origin/branch/commit, or update.json in a release zip.
$origin = $null
if (Test-Path "$PSScriptRoot\update.json") {
    $origin = Get-Content -Raw -LiteralPath "$PSScriptRoot\update.json" | ConvertFrom-Json
} elseif ((Test-Path (Join-Path $from '.git')) -and (Get-Command git.exe -ErrorAction SilentlyContinue)) {
    $origin = [pscustomobject]@{
        repo       = (& git.exe -C $from remote get-url origin 2>$null)
        branch     = (& git.exe -C $from rev-parse --abbrev-ref HEAD 2>$null)
        commit     = (& git.exe -C $from rev-parse HEAD 2>$null)
        commitDate = [long](& git.exe -C $from log -1 --format=%ct 2>$null)
    }
}
if (-not $origin -and (Test-Path "$dest\version.json")) { $origin = Get-Content -Raw -LiteralPath "$dest\version.json" | ConvertFrom-Json }
[ordered]@{
    version = $version; build = $build; installedFrom = $from; installedAt = (Get-Date).ToString('s')
    repo = $origin.repo; branch = $origin.branch; commit = $origin.commit; commitDate = $origin.commitDate
} | ConvertTo-Json | Set-Content -LiteralPath "$dest\version.json" -Encoding UTF8
if ($origin.repo) { Write-Host "  Updates from $($origin.repo) ($($origin.branch))" }
Get-ChildItem -LiteralPath $dest -Recurse -File | Unblock-File

# API keys: carry over the repo's .env the first time (later edits go to the copy).
$envFrom = Join-Path $from '.env'
$envTo = Join-Path $data 'champstudio.env'
if ((Test-Path $envFrom) -and -not (Test-Path $envTo)) {
    Copy-Item -LiteralPath $envFrom -Destination $envTo
    Write-Host "  Using the API keys from $envFrom"
}

$shell = New-Object -ComObject WScript.Shell
$links = @(Join-Path ([Environment]::GetFolderPath('Programs')) 'Champ Studio.lnk')
if (-not $NoDesktopShortcut) { $links += Join-Path ([Environment]::GetFolderPath('Desktop')) 'Champ Studio.lnk' }
if ($Update) { $links = @($links | Where-Object { Test-Path $_ }) }   # keep shortcuts the user removed removed
foreach ($path in $links) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $ps
    $lnk.Arguments = $launch
    $lnk.WorkingDirectory = $dest
    $lnk.IconLocation = "$shellIcon,0"
    $lnk.WindowStyle = 7          # minimized: no console flash while PowerShell hides itself
    $lnk.Description = 'Champ Studio'
    $lnk.Save()
    Write-Host "  Shortcut: $path"
}

# Settings > Apps > Installed apps
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ChampStudio'
New-Item -Path $key -Force | Out-Null
$kb = [int]((Get-ChildItem -LiteralPath $dest -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1KB)
$strings = [ordered]@{
    DisplayName     = 'Champ Studio'
    DisplayVersion  = $version
    Publisher       = 'Champions Group'
    DisplayIcon     = $shellIcon
    InstallLocation = $dest
    UninstallString = "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$dest\Uninstall.ps1`""
}
foreach ($name in $strings.Keys) { New-ItemProperty -Path $key -Name $name -Value $strings[$name] -PropertyType String -Force | Out-Null }
foreach ($pair in @(@('NoModify', 1), @('NoRepair', 1), @('EstimatedSize', $kb))) {
    New-ItemProperty -Path $key -Name $pair[0] -Value $pair[1] -PropertyType DWord -Force | Out-Null
}

& "$env:SystemRoot\System32\ie4uinit.exe" -show 2>$null   # ask Explorer to refresh icons now
Write-Host "Champ Studio $build installed." -ForegroundColor Green
if (-not $NoLaunch) {
    Write-Host 'Opening Champ Studio (the first launch sets up Docker and builds the engine).'
    Start-Process -FilePath $ps -ArgumentList $launch -WindowStyle Hidden
}
