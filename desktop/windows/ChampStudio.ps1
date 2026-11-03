<#
.SYNOPSIS
    Champ Studio for Windows. The desktop shortcut runs this.

.DESCRIPTION
    First run: installs Docker Desktop (and WSL) if needed, builds the Champ Studio engine image and
    starts it. Every run after that: starts Docker and the engine if they aren't running and opens
    Champ Studio in its own window. Closing the window stops the engine (after any running exports
    or AI jobs finish); set "stopEngineOnClose": false in %LOCALAPPDATA%\ChampStudio\config.json to
    keep it running. Each start first checks the git branch it was installed from and updates itself
    if there's a newer commit (set "autoUpdate": false to turn that off).

    Troubleshooting:
      -Check      show what is installed and running; changes nothing
      -NoWindow   run the startup in this console and exit 0 once Champ Studio is ready
      -Stop       stop the Champ Studio engine
#>
param([switch] $Check, [switch] $NoWindow, [switch] $Stop, [switch] $AfterUpdate)

. (Join-Path $PSScriptRoot 'lib\Common.ps1')
. (Join-Path $PSScriptRoot 'lib\Engine.ps1')

$paths = Get-ChampPaths
$sync = New-ChampSync

if ($Check -or $NoWindow -or $Stop) {
    Initialize-ChampLog -Path $paths.Log -Console:$NoWindow
    Initialize-ChampEngine -Sync $sync
    if ($Check) { Show-ChampCheck; exit 0 }
    if ($Stop) { Stop-ChampEngine; exit 0 }
    Invoke-ChampStartup
    if ($sync.State -eq 'relaunch') {
        & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -ExecutionPolicy Bypass -File (Join-Path $paths.App 'ChampStudio.ps1') -NoWindow
        exit $LASTEXITCODE
    }
    if ($sync.State -eq 'ready') { Write-Host "Champ Studio is ready at $($sync.Url)"; exit 0 }
    Write-Host "$($sync.Title): $($sync.Error)"
    exit 1
}

# ---- one copy per user: a second launch asks the first one to show itself ----
$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\ChampStudio.Desktop', [ref]$created)
$showSignal = New-Object System.Threading.EventWaitHandle($false, [System.Threading.EventResetMode]::AutoReset, 'Local\ChampStudio.Desktop.Show')
if (-not $created) {
    if (-not $AfterUpdate) { [void]$showSignal.Set(); exit 0 }
    # Started by the previous version after an update: wait for it to hand over.
    try { [void]$mutex.WaitOne(20000) } catch [System.Threading.AbandonedMutexException] { }
}

. (Join-Path $PSScriptRoot 'lib\Window.ps1')
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

Initialize-ChampLog -Path $paths.Log -Queue $sync.Log -Lock $sync.LogLock
Initialize-ChampEngine -Sync $sync

# Modes: setup -> opening -> running -> (waiting for jobs) -> stopping -> exit
$script:App = @{
    Mode = 'setup'; Worker = $null; Exiting = $false; Quitting = $false; ReopenAfterStop = $false
    BrowserExe = $null; Browser = $null; OpenedAt = [DateTime]::MinValue; Seen = $false; Missed = 0; NextCheck = [DateTime]::MinValue
}
$script:ChampUi = New-ChampWindow -Steps $script:ChampSteps -IconPath $paths.Icon
$window = $script:ChampUi.Window

# ---- background work (the window stays responsive while Docker installs or the image builds) ----

function Start-ChampWorker {
    param([string] $Task)
    $rs = [runspacefactory]::CreateRunspace()
    $rs.ApartmentState = 'STA'
    $rs.Open()
    $ps = [powershell]::Create()
    $ps.Runspace = $rs
    [void]$ps.AddScript({
        param($LibDir, $Sync, $Task)
        . (Join-Path $LibDir 'Common.ps1')
        . (Join-Path $LibDir 'Engine.ps1')
        Initialize-ChampLog -Path (Get-ChampPaths).Log -Queue $Sync.Log -Lock $Sync.LogLock -NoRotate
        try {
            Initialize-ChampEngine -Sync $Sync
            if ($Task -eq 'stop') { Stop-ChampEngine } else { Invoke-ChampStartup }
        } catch {
            Write-ChampLog "Unexpected error: $($_.Exception.Message) $($_.ScriptStackTrace)" 'error'
            if ($Task -ne 'stop') { Set-ChampFailure -Title 'Something went wrong' -Message $_.Exception.Message }
        }
    }.ToString()).AddArgument($script:ChampLibDir).AddArgument($sync).AddArgument($Task)
    $script:App.Worker = @{ PS = $ps; Handle = $ps.BeginInvoke() }
}

function Test-ChampWorkerDone {
    $w = $script:App.Worker
    if (-not $w) { return $true }
    if (-not $w.Handle.IsCompleted) { return $false }
    try { [void]$w.PS.EndInvoke($w.Handle) } catch { Write-ChampLog "worker: $($_.Exception.Message)" 'error' }
    $w.PS.Runspace.Dispose()
    $w.PS.Dispose()
    $script:App.Worker = $null
    $true
}

# ---- app lifecycle ----

function Start-ChampSetup {
    if (-not (Test-ChampWorkerDone)) { return }
    $script:App.Mode = 'setup'
    $script:App.Quitting = $false
    $sync.State = 'working'
    $sync.Title = 'Starting Champ Studio'
    $window.Show()
    if ($window.WindowState -eq 'Minimized') { $window.WindowState = 'Normal' }
    [void]$window.Activate()
    Start-ChampWorker 'startup'
}

function Open-ChampApp {
    $script:App.BrowserExe = Open-ChampWindow -Url $sync.Url
    $script:App.Browser = $null
    $script:App.OpenedAt = [DateTime]::Now
    $script:App.Seen = $false
    $script:App.Missed = 0
    $script:App.Mode = 'opening'
    $tray.Text = 'Champ Studio'
    $tray.Visible = $true
}

function Test-ChampWindowOpen {
    $app = $script:App
    if (-not $app.BrowserExe) { return $false }
    $p = $app.Browser
    if (-not $p -or $p.HasExited) { $p = Get-ChampBrowserProcess -Exe $app.BrowserExe; $app.Browser = $p }
    if (-not $p) { return $false }
    $p.Refresh()
    (-not $p.HasExited) -and $p.MainWindowHandle -ne [IntPtr]::Zero
}

function Close-ChampBrowser {
    if (-not $script:App.BrowserExe) { return }
    $p = Get-ChampBrowserProcess -Exe $script:App.BrowserExe
    for ($i = 0; $p -and $i -lt 8; $i++) {
        $p.Refresh()
        if ($p.HasExited -or $p.MainWindowHandle -eq [IntPtr]::Zero) { break }
        [void]$p.CloseMainWindow()
        Start-Sleep -Milliseconds 250
    }
}

function Start-ChampStop {
    $script:App.Mode = 'stopping'
    $tray.Text = 'Champ Studio - stopping'
    Start-ChampWorker 'stop'
}

function Invoke-ChampWindowClosed {
    Write-ChampLog 'The Champ Studio window was closed'
    if (-not $script:ChampConfig.stopEngineOnClose) { Exit-ChampApp; return }
    $jobs = @(Get-ChampActiveJobs $sync.Port)
    if ($jobs.Count) {
        Write-ChampLog "Waiting for $($jobs.Count) job(s) before stopping the engine"
        $script:App.Mode = 'waiting'
        $tray.Text = "Champ Studio - finishing $($jobs.Count) job(s)"
        $tray.ShowBalloonTip(8000, 'Champ Studio is finishing up',
            "$($jobs.Count) export or AI job(s) still running. The engine stops by itself when they're done.", [System.Windows.Forms.ToolTipIcon]::Info)
        return
    }
    Start-ChampStop
}

function Show-ChampApp {
    switch ($script:App.Mode) {
        'setup' {
            $window.Show()
            if ($window.WindowState -eq 'Minimized') { $window.WindowState = 'Normal' }
            [void]$window.Activate()
        }
        'stopping' { $script:App.ReopenAfterStop = $true }
        default { if ($sync.Url) { Open-ChampApp } }
    }
}

function Request-ChampQuit {
    $app = $script:App
    switch ($app.Mode) {
        'setup' {
            if ($app.Worker -and $sync.State -eq 'working') {
                $answer = [System.Windows.MessageBox]::Show($window,
                    "Champ Studio is still setting up. Quit anyway?`n`nSetup picks up where it left off the next time you open Champ Studio.",
                    'Champ Studio', 'YesNo', 'Question')
                if ("$answer" -ne 'Yes') { return }
                $sync.Cancel = $true
                $app.Quitting = $true
                $window.Hide()
                return
            }
            Exit-ChampApp
        }
        'stopping' { $app.ReopenAfterStop = $false }
        default {
            $jobs = @(Get-ChampActiveJobs $sync.Port)
            if ($jobs.Count) {
                $answer = [System.Windows.MessageBox]::Show(
                    "$($jobs.Count) export or AI job(s) are still running. Quit Champ Studio and stop them?",
                    'Champ Studio', 'YesNo', 'Warning')
                if ("$answer" -ne 'Yes') { return }
            }
            Close-ChampBrowser
            if ($script:ChampConfig.stopEngineOnClose) { Start-ChampStop } else { Exit-ChampApp }
        }
    }
}

function Exit-ChampApp {
    if ($script:App.Exiting) { return }
    $script:App.Exiting = $true
    Write-ChampLog 'Champ Studio desktop closed'
    $timer.Stop()
    $tray.Visible = $false
    $tray.Dispose()
    try { $mutex.ReleaseMutex() } catch { }
    $wpfApp.Shutdown()
}

# After a self-update: start the new version and hand over (it waits for this one to exit).
function Restart-ChampApp {
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    Write-ChampLog 'Restarting into the updated version'
    Start-Process -FilePath $ps -WindowStyle Hidden -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $paths.App 'ChampStudio.ps1')`" -AfterUpdate"
    Exit-ChampApp
}

function Invoke-ChampButton {
    param([string] $Label)
    switch ($Label) {
        'Try again' { Start-ChampSetup }
        'Quit' { Exit-ChampApp }
        'Later' { Exit-ChampApp }
        'Get Docker Desktop' { Start-Process 'https://www.docker.com/products/docker-desktop/' }
        'Open Docker Desktop' {
            $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
            if (Test-Path $exe) { Start-Process -FilePath $exe }
        }
        'Restart now' {
            $answer = [System.Windows.MessageBox]::Show($window, 'Restart Windows now? Save your work in other apps first.', 'Champ Studio', 'OKCancel', 'Question')
            if ("$answer" -eq 'OK') { Restart-Computer }
        }
    }
}

function Invoke-ChampTick {
    $app = $script:App
    if ($showSignal.WaitOne(0)) { Show-ChampApp }
    $now = [DateTime]::Now
    switch ($app.Mode) {
        'setup' {
            Update-ChampWindow $script:ChampUi $sync
            $done = Test-ChampWorkerDone
            if ($done -and $app.Quitting) { Exit-ChampApp }
            elseif ($done -and $sync.State -eq 'ready') { Open-ChampApp }
            elseif ($done -and $sync.State -eq 'relaunch') { Restart-ChampApp }
        }
        'opening' {
            Update-ChampWindow $script:ChampUi $sync
            if (-not $app.BrowserExe) { $window.Hide(); $app.Mode = 'detached'; return }
            if ($now -lt $app.NextCheck) { return }
            $app.NextCheck = $now.AddMilliseconds(700)
            $open = Test-ChampWindowOpen
            if ($open -or ($now - $app.OpenedAt).TotalSeconds -gt 25) {
                $app.Seen = $open
                $window.Hide()
                $app.Mode = 'running'
            }
        }
        'running' {
            if ($now -lt $app.NextCheck) { return }
            $app.NextCheck = $now.AddSeconds(2)
            if (Test-ChampWindowOpen) { $app.Seen = $true; $app.Missed = 0; return }
            if (-not $app.Seen) { return }
            $app.Missed++
            if ($app.Missed -ge 2) { Invoke-ChampWindowClosed }
        }
        'waiting' {
            if ($now -lt $app.NextCheck) { return }
            $app.NextCheck = $now.AddSeconds(15)
            if (-not @(Get-ChampActiveJobs $sync.Port).Count) { Start-ChampStop }
        }
        'stopping' {
            if (-not (Test-ChampWorkerDone)) { return }
            if ($app.ReopenAfterStop) { $app.ReopenAfterStop = $false; $tray.Visible = $false; Start-ChampSetup }
            else { Exit-ChampApp }
        }
    }
}

# ---- tray icon (shown once the app is open) ----

$tray = New-Object System.Windows.Forms.NotifyIcon
$tray.Icon = if (Test-Path $paths.Icon) { New-Object System.Drawing.Icon($paths.Icon, 16, 16) } else { [System.Drawing.SystemIcons]::Application }
$tray.Text = 'Champ Studio'
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$openItem = $menu.Items.Add('Open Champ Studio', $null, { Show-ChampApp })
$openItem.Font = New-Object System.Drawing.Font($menu.Font, [System.Drawing.FontStyle]::Bold)
[void]$menu.Items.Add('Open in your browser', $null, { if ($sync.Url) { Start-Process $sync.Url } })
[void]$menu.Items.Add('Show log', $null, { Start-Process notepad.exe -ArgumentList "`"$($paths.Log)`"" })
[void]$menu.Items.Add('-')
[void]$menu.Items.Add('Quit Champ Studio', $null, { Request-ChampQuit })
$tray.ContextMenuStrip = $menu
$tray.Add_DoubleClick({ Show-ChampApp })

# ---- window wiring ----

$script:ChampUi.CloseButton.Add_Click({ Request-ChampQuit })
$script:ChampUi.PrimaryButton.Add_Click({ Invoke-ChampButton "$($this.Content)" })
$script:ChampUi.SecondaryButton.Add_Click({ Invoke-ChampButton "$($this.Content)" })
$script:ChampUi.LogButton.Add_Click({ Start-Process notepad.exe -ArgumentList "`"$($paths.Log)`"" })
$window.Add_Closing({
    param($s, $e)
    if (-not $script:App.Exiting) { $e.Cancel = $true; Request-ChampQuit }
})

$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(150)
$timer.Add_Tick({ try { Invoke-ChampTick } catch { Write-ChampLog "UI: $($_.Exception.Message) $($_.ScriptStackTrace)" 'error' } })

$wpfApp = New-Object System.Windows.Application
$wpfApp.ShutdownMode = [System.Windows.ShutdownMode]::OnExplicitShutdown
$wpfApp.Add_DispatcherUnhandledException({ param($s, $e) Write-ChampLog "UI: $($e.Exception.Message)" 'error'; $e.Handled = $true })

Start-ChampSetup
$timer.Start()
[void]$wpfApp.Run()
