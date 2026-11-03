# Champ Studio desktop - the startup pipeline: Docker Desktop -> Docker engine -> image -> container,
# plus the app window, job checks and shutdown. Status goes to $script:ChampSync for the window.
# ASCII-only (see Common.ps1).

$script:ChampPaths = $null
$script:ChampConfig = $null
$script:ChampBuild = $null
$script:Docker = $null
$script:ChampPort = 8787                    # port used this run (config.port unless it was taken)
$script:ChampSteps = [ordered]@{ update = 'Updates'; docker = 'Docker Desktop'; engine = 'Docker engine'; image = 'Champ Studio engine'; start = 'Start Champ Studio' }

function New-ChampSync {
    $steps = [hashtable]::Synchronized(@{})
    foreach ($k in $script:ChampSteps.Keys) { $steps[$k] = @{ State = 'pending'; Note = '' } }
    [hashtable]::Synchronized(@{
        State = 'working'; Title = 'Starting Champ Studio'; Detail = ''; Percent = -1.0; Hint = ''
        Error = ''; Action = ''; Steps = $steps; PhaseStarted = [DateTime]::Now
        Url = ''; Port = 0; Cancel = $false
        Log = New-Object 'System.Collections.Concurrent.ConcurrentQueue[string]'; LogLock = New-Object object
    })
}

function Initialize-ChampEngine {
    param([Parameter(Mandatory)] $Sync)
    $script:ChampSync = $Sync
    $script:ChampPaths = Get-ChampPaths
    $script:ChampConfig = Read-ChampConfig $script:ChampPaths
    $script:ChampBuild = Get-ChampBuildInfo $script:ChampPaths
    $script:Docker = Find-ChampDocker
}

# ---- status reporting ----

function Set-ChampStatus {
    param([string] $Title, [string] $Detail = '', [double] $Percent = -1, [string] $Hint = '')
    $s = $script:ChampSync
    if ($s.Title -ne $Title) { $s.PhaseStarted = [DateTime]::Now; Write-ChampLog "== $Title" }
    if ($Detail -and $s.Detail -ne $Detail) { Write-ChampLog "   $Detail" -NoQueue }
    $s.Title = $Title; $s.Detail = $Detail; $s.Percent = $Percent; $s.Hint = $Hint
}

function Set-ChampStep {
    param([string] $Key, [string] $State, [string] $Note = '')
    $script:ChampSync.Steps[$Key] = @{ State = $State; Note = $Note }
}

function Set-ChampFailure {
    param([string] $Title, [string] $Message, [string] $Action = 'retry', [string] $State = 'error')
    $s = $script:ChampSync
    $look = if ($State -eq 'reboot') { 'paused' } else { 'error' }
    foreach ($k in $script:ChampSteps.Keys) { if ($s.Steps[$k].State -eq 'active') { Set-ChampStep $k $look } }
    Write-ChampLog "$Title - $Message" 'error'
    $s.Title = $Title; $s.Error = $Message; $s.Action = $Action; $s.Hint = ''; $s.Percent = -1
    $s.State = $State
}

function Get-ChampTail {
    param($Lines, [int] $Count = 12)
    (@($Lines | Where-Object { $_ -and $_.Trim() }) | Select-Object -Last $Count) -join "`n"
}

# ---- docker ----

function Find-ChampDocker {
    $cmd = Get-Command docker.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    $bundled = Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'   # PATH is stale right after an install
    if (Test-Path $bundled) { return $bundled }
    $null
}

function Invoke-ChampDocker {
    param([string[]] $Arguments, [int] $TimeoutSec = 60, [scriptblock] $OnLine, [hashtable] $Environment, [switch] $LogOutput)
    $envVars = @{ DOCKER_CLI_HINTS = 'false' }
    if ($Environment) { foreach ($k in $Environment.Keys) { $envVars[$k] = $Environment[$k] } }
    Invoke-ChampProcess -File $script:Docker -Arguments $Arguments -TimeoutSec $TimeoutSec -OnLine $OnLine -Environment $envVars -LogOutput:$LogOutput
}

function Get-ChampImageTag { '{0}:{1}' -f $script:ChampImageRepo, $script:ChampBuild.Build }

function Invoke-ChampCompose {
    param([string[]] $Arguments, [int] $TimeoutSec = 300)
    $vars = @{ CHAMP_IMAGE = (Get-ChampImageTag); CHAMP_PORT = [string]$script:ChampPort }
    $file = Read-ChampEnvFile $script:ChampPaths
    foreach ($k in @('OPENROUTER_API_KEY', 'MUAPI_API_KEY')) { if ($file[$k]) { $vars[$k] = $file[$k] } }
    Invoke-ChampDocker -Arguments (@('compose', '-p', $script:ChampProject, '-f', $script:ChampPaths.Compose) + $Arguments) -TimeoutSec $TimeoutSec -Environment $vars -LogOutput
}

function Test-ChampDockerEngine {
    if (-not $script:Docker) { return $false }
    $r = Invoke-ChampDocker @('version', '--format', '{{.Server.Version}}') -TimeoutSec 20
    $r.ExitCode -eq 0 -and ($r.Output | Select-Object -Last 1) -match '^\d'
}

function Test-ChampWsl {
    $wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
    (Test-Path $wsl) -and (Invoke-ChampProcess -File $wsl -Arguments @('--version') -TimeoutSec 30).ExitCode -eq 0
}

function Install-ChampDockerDesktop {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $winget) {
        Set-ChampFailure -Title 'Docker Desktop is needed' -Action 'get-docker' -Message ("Champ Studio runs inside Docker Desktop, and this PC can't install it automatically " +
            "(the Windows Package Manager, winget, isn't available). Click Get Docker Desktop, install it, then click Try again.")
        return $null
    }
    Set-ChampStatus -Title 'Installing Docker Desktop' `
        -Detail 'Champ Studio runs inside Docker Desktop. Downloading it (about 600 MB) and installing it. When Windows asks for permission, choose Yes.' `
        -Hint 'This happens once and can take 5 to 15 minutes, depending on your connection.'
    $r = Invoke-ChampProcess -File $winget.Source -TimeoutSec 3600 -LogOutput -Arguments @(
        'install', '--id', 'Docker.DockerDesktop', '--exact', '--source', 'winget',
        '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity', '--silent',
        '--override', 'install --quiet --accept-license --backend=wsl-2')
    if ($r.Cancelled) { return $null }
    $docker = Find-ChampDocker
    if ($docker) { Write-ChampLog "Docker Desktop installed ($docker)"; return $docker }
    # winget: 0x8A150109 / 0x8A15010A = restart needed; 3010 / 1641 = installer asked for a restart
    if ($r.ExitCode -in @(-1978335991, -1978335990, 3010, 1641)) {
        Set-ChampFailure -State 'reboot' -Action 'restart' -Title 'Restart your PC to finish' `
            -Message 'Docker Desktop is installed but Windows needs a restart. After restarting, open Champ Studio again; setup carries on from here.'
        return $null
    }
    $msg = ("The installer finished with code 0x{0:X8}. If you chose No at the Windows permission prompt, click Try again and choose Yes. " +
        "Otherwise install Docker Desktop yourself (Get Docker Desktop), then click Try again.") -f $r.ExitCode
    Set-ChampFailure -Title "Docker Desktop didn't install" -Action 'get-docker' -Message $msg
    $null
}

function Install-ChampWsl {
    Set-ChampStatus -Title 'Installing WSL' -Detail 'Docker needs the Windows Subsystem for Linux. When Windows asks for permission, choose Yes.'
    try {
        $p = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\wsl.exe') -ArgumentList '--install', '--no-distribution' -Verb RunAs -Wait -PassThru
        Write-ChampLog "wsl --install exited with $($p.ExitCode)"
    } catch {
        Set-ChampFailure -Title 'WSL needs your permission' -Message 'Installing WSL needs administrator permission. Click Try again and choose Yes when Windows asks.'
        return $false
    }
    if (Test-ChampWsl) { return $true }
    Set-ChampFailure -State 'reboot' -Action 'restart' -Title 'Restart your PC to finish' `
        -Message 'WSL is installed but Windows needs a restart. After restarting, open Champ Studio again; setup carries on from here.'
    $false
}

function Start-ChampDockerDesktop {
    $r = Invoke-ChampDocker @('desktop', 'start', '--detach') -TimeoutSec 60
    if ($r.ExitCode -eq 0) { Write-ChampLog 'Asked Docker Desktop to start'; return }
    $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $exe) { Start-Process -FilePath $exe; Write-ChampLog 'Started Docker Desktop' }
    else { Write-ChampLog "Couldn't start Docker Desktop: $(Get-ChampTail $r.Output 3)" 'warn' }
}

function Wait-ChampDockerEngine {
    if (Test-ChampDockerEngine) { return $true }
    if (-not (Test-ChampWsl) -and -not (Install-ChampWsl)) { return $false }
    Set-ChampStatus -Title 'Starting Docker' -Detail 'Waiting for the Docker engine. This takes up to a minute after Windows starts.'
    Start-ChampDockerDesktop
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt 300) {
        if (Test-ChampCancelled) { return $false }
        Start-Sleep -Seconds 2
        if (Test-ChampDockerEngine) { return $true }
        if ($sw.Elapsed.TotalSeconds -gt 45 -and -not $script:ChampSync.Hint) {
            $script:ChampSync.Hint = 'If Docker Desktop opened a window (terms, sign-in or an update), accept or skip it. Champ Studio carries on by itself.'
        }
    }
    Set-ChampFailure -Title "Docker didn't start" -Action 'open-docker' -Message ("Docker Desktop hasn't started after 5 minutes. Open Docker Desktop to see what it needs " +
        "(often virtualization has to be turned on in the PC's BIOS, or WSL needs an update), then click Try again.")
    $false
}

# ---- image ----

# Milestones of docker/Dockerfile (runtime target) with rough first-build weights (percent).
$script:ChampBuildPhases = @(
    @{ Match = '^\[(editor|deps) +1/\d+\] FROM'; Label = 'Downloading base images'; Weight = 4 }
    @{ Match = 'npm ci'; Label = 'Installing editor packages'; Weight = 8 }
    @{ Match = 'npm run build'; Label = 'Building the video editor'; Weight = 6 }
    @{ Match = 'apt-get'; Label = 'Installing ffmpeg and system libraries'; Weight = 15 }
    @{ Match = 'pip install --no-cache-dir -r'; Label = 'Installing Python packages'; Weight = 35 }
    @{ Match = 'new_session'; Label = 'Downloading the background-removal model'; Weight = 10 }
    @{ Match = 'pip install --no-cache-dir --no-deps'; Label = 'Installing the Champ Studio engine'; Weight = 4 }
    @{ Match = '^exporting to image'; Label = 'Saving the image'; Weight = 10 }
)

function Update-ChampBuildProgress {
    param($Tracker, [string] $Line)
    if ($Line -notmatch '^#(\d+) (.+)$') { return }
    $id = $Matches[1]; $rest = $Matches[2]
    if ($rest -match '^(DONE|CACHED)\b') {
        if ($Tracker.Steps.ContainsKey($id) -and $Tracker.Active.Contains($id)) {
            $Tracker.Credit += $script:ChampBuildPhases[$Tracker.Steps[$id]].Weight
            $Tracker.Active.Remove($id)
        } else { return }
    } elseif ($rest -match '^ERROR') {
        $Tracker.Errors.Add($rest)
        return
    } elseif (-not $Tracker.Steps.ContainsKey($id) -and $rest -match '^(\[|exporting to image)') {
        for ($i = 0; $i -lt $script:ChampBuildPhases.Count; $i++) {
            if ($rest -match $script:ChampBuildPhases[$i].Match) {
                $Tracker.Steps[$id] = $i
                $Tracker.Active[$id] = $script:ChampBuildPhases[$i].Label
                break
            }
        }
        if (-not $Tracker.Active.Contains($id)) { return }
    } else { return }
    $labels = @($Tracker.Active.Values | Select-Object -Unique)
    $detail = if ($labels) { ($labels -join ', ') + '...' } else { 'Working...' }
    Set-ChampStatus -Title $script:ChampSync.Title -Detail $detail -Percent ([Math]::Min(99, $Tracker.Credit)) -Hint $script:ChampSync.Hint
}

function Install-ChampImage {
    $tag = Get-ChampImageTag
    if ((Invoke-ChampDocker @('image', 'inspect', '--format', '{{.Id}}', $tag) -TimeoutSec 30).ExitCode -eq 0) { return $true }
    $first = -not (Invoke-ChampDocker @('image', 'ls', '-q', $script:ChampImageRepo) -TimeoutSec 30).Output.Count
    $why = if ($first) { 'First run only' } else { 'Champ Studio was updated' }
    Set-ChampStatus -Title 'Building Champ Studio' -Detail 'Preparing...' -Percent 2 `
        -Hint "${why}: downloading and building everything Champ Studio needs. This usually takes 5 to 10 minutes the first time, less after updates."
    Write-ChampLog "Building $tag from $($script:ChampPaths.Source)"
    $tracker = @{ Steps = @{}; Active = [ordered]@{}; Credit = 2.0; Errors = New-Object System.Collections.Generic.List[string] }
    $src = $script:ChampPaths.Source
    $r = Invoke-ChampDocker -TimeoutSec 5400 -LogOutput -OnLine { param($line) Update-ChampBuildProgress $tracker $line } -Arguments @(
        'build', '--progress=plain', '--target', 'runtime', '--label', "champstudio.build=$($script:ChampBuild.Build)",
        '--tag', $tag, '--file', (Join-Path $src 'docker\Dockerfile'), $src)
    if ($r.Cancelled) { return $false }
    if ($r.ExitCode -ne 0) {
        $why = if ($r.TimedOut) { 'The build took longer than 90 minutes.' } else { Get-ChampTail $(if ($tracker.Errors.Count) { $tracker.Errors } else { $r.Output }) 6 }
        Set-ChampFailure -Title 'Building Champ Studio failed' -Message "$why`n`nCheck your internet connection and click Try again. Show details has the full log."
        return $false
    }
    Write-ChampLog "Built $tag"
    $true
}

function Remove-ChampOldImages {
    $tag = Get-ChampImageTag
    $r = Invoke-ChampDocker @('image', 'ls', $script:ChampImageRepo, '--format', '{{.Repository}}:{{.Tag}}') -TimeoutSec 30
    foreach ($old in $r.Output) {
        if ($old -and $old -ne $tag -and $old -notmatch '<none>') {
            $rm = Invoke-ChampDocker @('image', 'rm', $old) -TimeoutSec 120
            if ($rm.ExitCode -eq 0) { Write-ChampLog "Removed the previous image $old" }
        }
    }
}

# ---- container ----

function Test-ChampHealth {
    param([int] $Port)
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 3
        $r.status -eq 'ok' -and $r.name -like 'Champ Studio*'
    } catch { $false }
}

function Test-ChampPortBusy {
    param([int] $Port)
    # The container publishes on 127.0.0.1, so the real question is whether that address can be bound.
    $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
    $probe.ExclusiveAddressUse = $true
    try { $probe.Start(); $probe.Stop(); $false } catch { $true }
}

function Get-ChampContainerState {
    $r = Invoke-ChampDocker @('inspect', '--format', '{{.State.Status}}|{{.Config.Image}}', $script:ChampContainer) -TimeoutSec 20
    if ($r.ExitCode -ne 0) { return $null }
    $status, $image = ($r.Output | Select-Object -First 1) -split '\|', 2
    [pscustomobject]@{ Status = $status; Image = $image }
}

function Test-ChampPortFree {
    param([int] $Port)
    # Docker Desktop holds ports briefly while it starts, so give a busy port a few seconds.
    for ($i = 0; $i -lt 6; $i++) {
        if (-not (Test-ChampPortBusy $Port)) { return $true }
        Start-Sleep -Seconds 1
    }
    $false
}

# The configured port (8787) is always tried first; a fallback is used for this run only.
function Resolve-ChampPort {
    $port = [int]$script:ChampConfig.port
    if (-not (Test-ChampPortBusy $port)) { return $port }
    $r = Invoke-ChampDocker @('ps', '--filter', "name=^$($script:ChampContainer)$", '--format', '{{.Ports}}') -TimeoutSec 20
    if (($r.Output -join ' ') -match ":$port->") { return $port }
    if (Test-ChampPortFree $port) { return $port }
    foreach ($p in ($port + 1)..($port + 40)) {
        if (-not (Test-ChampPortBusy $p)) {
            Write-ChampLog "Port $port is used by another program; using $p this time" 'warn'
            return $p
        }
    }
    $port
}

function Start-ChampContainer {
    $script:ChampPort = Resolve-ChampPort
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $r = Invoke-ChampCompose @('up', '--detach', '--no-build')
        if ($r.ExitCode -eq 0) { return $true }
        $text = $r.Output -join "`n"
        if ($text -match 'is already in use by container') {
            Write-ChampLog 'A container named champstudio was created outside this app; replacing it (your data volume is kept)' 'warn'
            [void](Invoke-ChampDocker @('rm', '--force', $script:ChampContainer) -TimeoutSec 60)
        } elseif ($text -match 'port is already allocated|address already in use|ports are not available') {
            $next = $script:ChampPort + 1
            while (Test-ChampPortBusy $next) { $next++ }
            Write-ChampLog "Port $($script:ChampPort) is taken; using $next this time" 'warn'
            $script:ChampPort = $next
        } else { break }
    }
    Set-ChampFailure -Title "Champ Studio couldn't start" -Message (Get-ChampTail $r.Output 8)
    $false
}
function Wait-ChampHealthy {
    $port = $script:ChampPort
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt 180) {
        if (Test-ChampCancelled) { return $false }
        if (Test-ChampHealth $port) { return $true }
        Start-Sleep -Seconds 1
        if ([int]$sw.Elapsed.TotalSeconds % 10 -eq 0) {
            $state = Get-ChampContainerState
            if ($state -and $state.Status -in @('exited', 'dead')) {
                $logs = Invoke-ChampDocker @('logs', '--tail', '40', $script:ChampContainer) -TimeoutSec 30
                $logs.Output | ForEach-Object { Write-ChampLog "  $_" 'tool' }
                Set-ChampFailure -Title 'The Champ Studio engine stopped' -Message (Get-ChampTail $logs.Output 6)
                return $false
            }
        }
    }
    Set-ChampFailure -Title "Champ Studio isn't responding" -Message "The engine started but hasn't answered on port $port for 3 minutes. Click Try again; Show details has the log."
    $false
}

# ---- self-update from the git repo the app was installed from ----

function Find-ChampGit {
    $cmd = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    @("$env:ProgramFiles\Git\cmd\git.exe", "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function Get-ChampInstallInfo {
    if (-not (Test-Path $script:ChampPaths.Version)) { return $null }
    Get-Content -Raw -LiteralPath $script:ChampPaths.Version | ConvertFrom-Json
}

function Invoke-ChampGit {
    param([string[]] $Arguments, [int] $TimeoutSec = 120)
    # No console prompts (there's no console); Git Credential Manager can still show its sign-in window.
    Invoke-ChampProcess -File $script:Git -Arguments $Arguments -TimeoutSec $TimeoutSec -Environment @{ GIT_TERMINAL_PROMPT = '0' }
}

# Fetches the branch into a private clone; if it has a newer commit, re-installs from it.
# Returns $true when an update was installed (the caller restarts into it). Never fails startup.
function Update-ChampApp {
    $info = Get-ChampInstallInfo
    if ($script:ChampConfig.autoUpdate -eq $false) { Set-ChampStep update done 'turned off'; return $false }
    if (-not $info -or -not $info.repo -or -not $info.branch) { Set-ChampStep update done 'not set up'; return $false }
    Set-ChampStatus -Title 'Checking for updates' -Detail "Looking for a newer Champ Studio on $($info.branch)..."
    $script:Git = Find-ChampGit
    if (-not $script:Git -and (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Set-ChampStatus -Title 'Installing Git' -Detail 'Champ Studio updates itself from GitHub and needs Git for that. When Windows asks for permission, choose Yes.'
        [void](Invoke-ChampProcess -File (Get-Command winget.exe).Source -TimeoutSec 900 -LogOutput -Arguments @(
            'install', '--id', 'Git.Git', '--exact', '--source', 'winget', '--silent',
            '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity'))
        $script:Git = Find-ChampGit
    }
    if (-not $script:Git) { Write-ChampLog 'Git is not available; skipping the update check' 'warn'; Set-ChampStep update done 'skipped (no Git)'; return $false }

    $clone = Join-Path $script:ChampPaths.Data 'repo'
    if (Test-Path (Join-Path $clone '.git')) {
        [void](Invoke-ChampGit @('-C', $clone, 'remote', 'set-url', 'origin', $info.repo) 30)
        $r = Invoke-ChampGit @('-C', $clone, 'fetch', '--depth', '1', 'origin', $info.branch)
        if ($r.ExitCode -eq 0) { $r = Invoke-ChampGit @('-C', $clone, 'reset', '--hard', 'FETCH_HEAD') 60 }
    } else {
        Remove-Item -LiteralPath $clone -Recurse -Force -ErrorAction SilentlyContinue
        $r = Invoke-ChampGit @('clone', '--depth', '1', '--branch', $info.branch, '--single-branch', $info.repo, $clone) 600
    }
    if ($r.ExitCode -ne 0) {
        Write-ChampLog "Couldn't check for updates: $(Get-ChampTail $r.Output 3)" 'warn'
        Set-ChampStep update done "couldn't check (offline?)"
        return $false
    }
    $head = (Invoke-ChampGit @('-C', $clone, 'rev-parse', 'HEAD') 30).Output | Select-Object -Last 1
    $when = [long]((Invoke-ChampGit @('-C', $clone, 'log', '-1', '--format=%ct') 30).Output | Select-Object -Last 1)
    $short = "$head".Substring(0, [Math]::Min(7, "$head".Length))
    # Same commit, or older than what's installed (e.g. installed from unpushed local work): nothing to do.
    if ($head -eq $info.commit -or ($info.commitDate -and $when -le [long]$info.commitDate)) {
        Set-ChampStep update done 'up to date'
        return $false
    }
    $installer = Join-Path $clone 'desktop\windows\install.ps1'
    if (-not (Test-Path $installer)) { Write-ChampLog "$($info.branch) has no desktop app; not updating" 'warn'; Set-ChampStep update done 'up to date'; return $false }
    Set-ChampStatus -Title 'Updating Champ Studio' -Detail "Installing the latest version ($short)..."
    Write-ChampLog "Updating from $($info.commit) to $head"
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $r = Invoke-ChampProcess -File $ps -TimeoutSec 600 -LogOutput -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $installer, '-NoLaunch', '-Update')
    if ($r.ExitCode -ne 0) { Write-ChampLog 'The update did not install; starting the current version' 'warn'; Set-ChampStep update done 'update failed'; return $false }
    Set-ChampStep update done "updated to $short"
    $true
}

# ---- the whole startup ----

function Invoke-ChampStartup {
    $s = $script:ChampSync
    $s.State = 'working'; $s.Error = ''; $s.Action = ''; $s.Hint = ''; $s.Cancel = $false
    foreach ($k in $script:ChampSteps.Keys) { Set-ChampStep $k 'pending' }
    Write-ChampLog "Champ Studio desktop $($script:ChampBuild.Build) starting (source: $($script:ChampPaths.Source))"

    Set-ChampStep update active
    $updated = $false
    try { $updated = Update-ChampApp } catch { Write-ChampLog "Update check failed: $($_.Exception.Message)" 'warn'; Set-ChampStep update done "couldn't check" }
    if ($updated) {
        Set-ChampStatus -Title 'Restarting Champ Studio' -Detail 'Opening the new version...'
        $s.State = 'relaunch'
        return
    }

    Set-ChampStep docker active
    Set-ChampStatus -Title 'Checking Docker Desktop'
    if (-not $script:Docker) { $script:Docker = Install-ChampDockerDesktop }
    if (-not $script:Docker) { return }
    Set-ChampStep docker done 'installed'

    Set-ChampStep engine active
    Set-ChampStatus -Title 'Checking the Docker engine'
    if (-not (Wait-ChampDockerEngine)) { return }
    Set-ChampStep engine done 'running'

    Set-ChampStep image active
    Set-ChampStatus -Title 'Checking Champ Studio'
    $tag = Get-ChampImageTag
    $script:ChampPort = [int]$script:ChampConfig.port
    $state = Get-ChampContainerState
    $warm = $state -and $state.Status -eq 'running' -and $state.Image -eq $tag -and (Test-ChampHealth $script:ChampPort)
    if (-not $warm) {
        if (-not (Install-ChampImage)) { return }
    }
    Set-ChampStep image done 'ready'

    Set-ChampStep start active
    if (-not $warm) {
        Set-ChampStatus -Title 'Starting Champ Studio' -Detail 'Starting the engine...'
        if (-not (Start-ChampContainer)) { return }
        if (-not (Wait-ChampHealthy)) { return }
    }
    Set-ChampStep start done "port $($script:ChampPort)"
    Set-ChampStatus -Title 'Opening Champ Studio' -Percent 100
    try { Remove-ChampOldImages } catch { Write-ChampLog "Couldn't remove old images: $_" 'warn' }
    $s.Port = $script:ChampPort
    $s.Url = "http://127.0.0.1:$($s.Port)/"
    Write-ChampLog "Ready at $($s.Url)"
    $s.State = 'ready'
}

# ---- running app ----

function Get-ChampActiveJobs {
    param([int] $Port)
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/jobs?limit=200" -TimeoutSec 5
        @($r.jobs | Where-Object { $_.status -in @('pending', 'running') })
    } catch { @() }
}

function Stop-ChampEngine {
    if (-not $script:Docker) { $script:Docker = Find-ChampDocker }
    if (-not (Test-ChampDockerEngine)) { return }
    Write-ChampLog 'Stopping the Champ Studio engine'
    $r = Invoke-ChampDocker @('stop', '--time', '30', $script:ChampContainer) -TimeoutSec 120
    if ($r.ExitCode -ne 0) { Write-ChampLog "docker stop: $(Get-ChampTail $r.Output 3)" 'warn' }
}

function Find-ChampBrowser {
    $edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")
    $chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
    $order = if ($script:ChampConfig.browser -eq 'chrome') { $chrome + $edge } else { $edge + $chrome }
    $order | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}

# Opens the app in its own borderless browser window with a dedicated profile, so the window
# has no tabs or address bar and we can tell when it's closed. Returns the browser exe or $null.
function Open-ChampWindow {
    param([Parameter(Mandatory)] [string] $Url)
    $exe = Find-ChampBrowser
    if (-not $exe) { Start-Process $Url; return $null }
    $dir = $script:ChampPaths.Browser
    $browserArgs = @("--app=$Url", "--user-data-dir=`"$dir`"", '--no-first-run', '--no-default-browser-check')
    if (-not (Test-Path $dir)) { $browserArgs += '--start-maximized' }
    Start-Process -FilePath $exe -ArgumentList $browserArgs
    Write-ChampLog "Opened the app window ($([IO.Path]::GetFileName($exe)))"
    $exe
}

# The browser's main process for our profile (it owns every window we opened).
function Get-ChampBrowserProcess {
    param([Parameter(Mandatory)] [string] $Exe)
    $dir = $script:ChampPaths.Browser
    $name = [IO.Path]::GetFileName($Exe)
    $hit = Get-CimInstance Win32_Process -Filter "Name='$name'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($dir) -and $_.CommandLine -notmatch '--type=' } |
        Select-Object -First 1
    if ($hit) { Get-Process -Id $hit.ProcessId -ErrorAction SilentlyContinue }
}

# ---- diagnostics (ChampStudio.ps1 -Check) ----

function Show-ChampCheck {
    $p = $script:ChampPaths
    $row = { param($k, $v) Write-Host ('  {0,-16} {1}' -f $k, $v) }
    Write-Host "Champ Studio desktop $($script:ChampBuild.Build)"
    & $row 'App folder' $p.App
    & $row 'Source' $p.Source
    $info = Get-ChampInstallInfo
    & $row 'Updates' $(if ($info -and $info.repo) { "$($info.repo) $($info.branch) @ $("$($info.commit)".Substring(0, [Math]::Min(7, "$($info.commit)".Length)))$(if ($script:ChampConfig.autoUpdate -eq $false) { ' (turned off)' })" } else { 'not set up (running from a repo checkout)' })
    & $row 'Data folder' $p.Data
    & $row 'Docker CLI' $(if ($script:Docker) { $script:Docker } else { 'not installed' })
    & $row 'WSL' $(if (Test-ChampWsl) { 'installed' } else { 'missing' })
    $up = Test-ChampDockerEngine
    & $row 'Docker engine' $(if ($up) { 'running' } else { 'not running' })
    $tag = Get-ChampImageTag
    if ($up) {
        $img = (Invoke-ChampDocker @('image', 'inspect', '--format', '{{.Id}}', $tag) -TimeoutSec 30).ExitCode -eq 0
        & $row 'Image' "$tag $(if ($img) { '(built)' } else { '(not built yet; the next start builds it)' })"
        $state = Get-ChampContainerState
        & $row 'Container' $(if ($state) { "$($state.Status) ($($state.Image))" } else { 'not created' })
    }
    & $row 'Port' $script:ChampConfig.port
    & $row 'Health' $(if (Test-ChampHealth ([int]$script:ChampConfig.port)) { 'ok' } else { 'not responding' })
    $b = Find-ChampBrowser
    & $row 'App window' $(if ($b) { $b } else { 'default browser' })
    & $row 'Keys file' $(if (Test-Path $p.EnvFile) { "$($p.EnvFile) ($((Read-ChampEnvFile $p).Keys -join ', '))" } else { 'none (keys come from Settings)' })
    & $row 'Log' $p.Log
}
