<#
.SYNOPSIS
    Removes Champ Studio for this Windows user (Settings > Apps runs this).

.DESCRIPTION
    Removes the shortcuts, the app folder, the engine container and images. Projects, media and
    settings live in the Docker volume champ_studio_champ-data and are kept unless you choose to
    delete them (or pass -RemoveData with -Quiet). Docker Desktop itself is left installed.
#>
param([switch] $Quiet, [switch] $RemoveData)

. (Join-Path $PSScriptRoot 'lib\Common.ps1')
. (Join-Path $PSScriptRoot 'lib\Engine.ps1')
Add-Type -AssemblyName PresentationFramework

function Confirm-Champ([string] $Text, [string] $Icon = 'Question') {
    "$([System.Windows.MessageBox]::Show($Text, 'Uninstall Champ Studio', 'YesNo', $Icon, 'No'))" -eq 'Yes'
}

$running = $null
if ([System.Threading.Mutex]::TryOpenExisting('Local\ChampStudio.Desktop', [ref]$running)) {
    $running.Dispose()
    $msg = 'Champ Studio is open. Quit it first (right-click its icon by the clock, then Quit Champ Studio) and uninstall again.'
    if ($Quiet) { Write-Error $msg } else { [void][System.Windows.MessageBox]::Show($msg, 'Uninstall Champ Studio', 'OK', 'Information') }
    exit 1
}
if (-not $Quiet) {
    if (-not (Confirm-Champ "Uninstall Champ Studio?`n`nYour projects, media and settings are kept unless you choose to delete them next.")) { exit 1 }
    $RemoveData = Confirm-Champ ("Also delete all Champ Studio projects, media and settings?`n`nThis can't be undone. " +
        'Choose No to keep them; installing Champ Studio again picks them up.') 'Warning'
}

$sync = New-ChampSync
Initialize-ChampEngine -Sync $sync
$notes = @()
if (Test-ChampDockerEngine) {
    [void](Invoke-ChampDocker @('rm', '--force', $script:ChampContainer) -TimeoutSec 120)
    foreach ($tag in (Invoke-ChampDocker @('image', 'ls', $script:ChampImageRepo, '--format', '{{.Repository}}:{{.Tag}}') -TimeoutSec 30).Output) {
        if ($tag) { [void](Invoke-ChampDocker @('image', 'rm', '--force', $tag) -TimeoutSec 120) }
    }
    if ($RemoveData) { [void](Invoke-ChampDocker @('volume', 'rm', "$($script:ChampProject)_champ-data") -TimeoutSec 60) }
} elseif ($script:Docker) {
    $notes += "Docker isn't running, so the Champ Studio engine image (and data, if you chose to delete it) is still in Docker."
}

$paths = Get-ChampPaths
foreach ($lnk in @((Join-Path ([Environment]::GetFolderPath('Programs')) 'Champ Studio.lnk'), (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Champ Studio.lnk'))) {
    Remove-Item -LiteralPath $lnk -Force -ErrorAction SilentlyContinue
}
Remove-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ChampStudio' -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $paths.Data -Recurse -Force -ErrorAction SilentlyContinue
Set-Location $env:TEMP
# Always the installed copy, never the folder this script runs from (that may be the repo).
Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\Champ Studio') -Recurse -Force -ErrorAction SilentlyContinue

$done = 'Champ Studio was uninstalled. Docker Desktop is still installed; remove it from Settings > Apps if nothing else uses it.'
if ($notes) { $done += "`n`n" + ($notes -join "`n") }
if ($Quiet) { Write-Host $done } else { [void][System.Windows.MessageBox]::Show($done, 'Uninstall Champ Studio', 'OK', 'Information') }
