# Champ Studio desktop - shared helpers: paths, config, logging, child processes, source bundle.
# Windows PowerShell 5.1. Keep this file ASCII-only (5.1 reads BOM-less scripts as ANSI).

$script:ChampLibDir = $PSScriptRoot
$script:ChampImageRepo = 'champstudio-desktop'
$script:ChampProject = 'champ_studio'      # same compose project, container and data volume as the repo's docker-compose.yml
$script:ChampContainer = 'champstudio'
$script:ChampLogState = @{ Path = $null; Queue = $null; Lock = (New-Object object); Console = $false }
$script:ChampSync = $null                  # synchronized hashtable shared with the window (null in console mode)

function Get-ChampPaths {
    $app = Split-Path -Parent $script:ChampLibDir
    $installed = Join-Path $app 'source'
    # Installed copies carry their own source\ folder; running from the repo uses the repo itself.
    $source = if (Test-Path (Join-Path $installed 'docker\Dockerfile')) { $installed } else { (Resolve-Path (Join-Path $app '..\..')).Path }
    $data = Join-Path $env:LOCALAPPDATA 'ChampStudio'
    [pscustomobject]@{
        App     = $app
        Source  = $source
        Compose = Join-Path $app 'compose.yml'
        Icon    = Join-Path $app 'assets\champstudio.ico'
        Version = Join-Path $app 'version.json'
        Data    = $data
        Config  = Join-Path $data 'config.json'
        EnvFile = Join-Path $data 'champstudio.env'
        Log     = Join-Path $data 'logs\launcher.log'
        Browser = Join-Path $data 'browser'
    }
}

# ---- config (%LOCALAPPDATA%\ChampStudio\config.json) ----

function Read-ChampConfig {
    param([Parameter(Mandatory)] $Paths)
    $cfg = @{ port = 8787; stopEngineOnClose = $true; browser = 'auto'; autoUpdate = $true }
    if (Test-Path $Paths.Config) {
        try {
            $raw = Get-Content -Raw -LiteralPath $Paths.Config | ConvertFrom-Json
            foreach ($p in $raw.PSObject.Properties) { $cfg[$p.Name] = $p.Value }
        } catch { Write-ChampLog "config.json is not valid JSON; using defaults ($_)" 'warn' }
    }
    $cfg
}

function Save-ChampConfig {
    param([Parameter(Mandatory)] $Paths, [Parameter(Mandatory)] [hashtable] $Config)
    New-Item -ItemType Directory -Force -Path $Paths.Data | Out-Null
    ($Config | ConvertTo-Json) | Set-Content -LiteralPath $Paths.Config -Encoding UTF8
}

# Optional KEY=VALUE file; the installer seeds it from the repo's .env so existing keys keep working.
function Read-ChampEnvFile {
    param([Parameter(Mandatory)] $Paths)
    $vars = @{}
    if (-not (Test-Path $Paths.EnvFile)) { return $vars }
    foreach ($line in Get-Content -LiteralPath $Paths.EnvFile) {
        if ($line -match '^\s*(#|$)') { continue }
        if ($line -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $vars[$Matches[1]] = $Matches[2].Trim('"').Trim("'")
        }
    }
    $vars
}

# ---- build identity ----

function Get-ChampVersion {
    param([Parameter(Mandatory)] [string] $Source)
    $toml = Join-Path $Source 'engine\pyproject.toml'
    $m = Select-String -LiteralPath $toml -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { $m.Matches[0].Groups[1].Value } else { '0.0.0' }
}

# Content hash of the source bundle: a new hash means a new image gets built on next launch.
function Get-ChampSourceHash {
    param([Parameter(Mandatory)] [string] $Source)
    $root = (Resolve-Path $Source).Path.TrimEnd('\')
    $files = foreach ($item in @('engine', 'web', 'docker', '.dockerignore')) {
        $p = Join-Path $root $item
        if (Test-Path $p -PathType Leaf) { Get-Item -LiteralPath $p }
        elseif (Test-Path $p) { Get-ChildItem -LiteralPath $p -Recurse -File | Where-Object { -not (Test-ChampExcluded $_.FullName.Substring($root.Length + 1)) } }
    }
    $rel = $files | ForEach-Object { $_.FullName.Substring($root.Length + 1).Replace('\', '/') } | Sort-Object -CaseSensitive
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $latin1 = [System.Text.Encoding]::GetEncoding(28591)   # byte-for-byte
    foreach ($r in $rel) {
        $name = [System.Text.Encoding]::UTF8.GetBytes("$r`n")
        $bytes = [System.IO.File]::ReadAllBytes((Join-Path $root $r))
        # git may check text out with CRLF; the same commit must give the same hash (and no rebuild)
        if ($r -notmatch '\.(ttf|otf|woff2?|png|jpe?g|gif|webp|ico|mp3|mp4|wav|onnx|zip)$') { $bytes = $latin1.GetBytes($latin1.GetString($bytes).Replace("`r", '')) }
        [void]$sha.TransformBlock($name, 0, $name.Length, $null, 0)
        [void]$sha.TransformBlock($bytes, 0, $bytes.Length, $null, 0)
    }
    [void]$sha.TransformFinalBlock([byte[]]@(), 0, 0)
    (($sha.Hash | ForEach-Object { $_.ToString('x2') }) -join '').Substring(0, 12)
}

function Get-ChampBuildInfo {
    param([Parameter(Mandatory)] $Paths)
    if (Test-Path $Paths.Version) {
        $v = Get-Content -Raw -LiteralPath $Paths.Version | ConvertFrom-Json
        return [pscustomobject]@{ Version = $v.version; Build = $v.build }
    }
    $version = Get-ChampVersion $Paths.Source
    [pscustomobject]@{ Version = $version; Build = "$version-$(Get-ChampSourceHash $Paths.Source)" }
}

# Build outputs, caches and tests never go into the bundle (mirrors .dockerignore).
$script:ChampExcludedDirs = @('.venv', '__pycache__', '.pytest_cache', 'node_modules', 'dist', 'build', '.DS_Store')
$script:ChampExcludedPaths = @('web/editor', 'engine/tests')

function Test-ChampExcluded {
    param([string] $Relative)
    $r = $Relative.Replace('\', '/')
    foreach ($p in $script:ChampExcludedPaths) { if ($r -eq $p -or $r.StartsWith("$p/")) { return $true } }
    foreach ($part in $r.Split('/')) {
        if ($script:ChampExcludedDirs -contains $part -or $part -like '*.egg-info' -or $part -like '*.pyc') { return $true }
    }
    $false
}

function Copy-ChampSource {
    param([Parameter(Mandatory)] [string] $From, [Parameter(Mandatory)] [string] $To)
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    foreach ($dir in @('engine', 'web', 'docker')) {
        $xd = @($script:ChampExcludedDirs + ($script:ChampExcludedPaths | Where-Object { $_ -like "$dir/*" } | ForEach-Object { Join-Path $From $_.Replace('/', '\') }))
        & robocopy.exe (Join-Path $From $dir) (Join-Path $To $dir) /MIR /XD @xd /XF '*.pyc' '.DS_Store' /NFL /NDL /NJH /NJS /NP /R:2 /W:1 | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Copying $dir failed (robocopy exit $LASTEXITCODE)" }
    }
    Copy-Item -LiteralPath (Join-Path $From '.dockerignore') -Destination $To -Force
}

# ---- logging ----

function Initialize-ChampLog {
    param([Parameter(Mandatory)] [string] $Path, $Queue, $Lock, [switch] $Console, [switch] $NoRotate)
    New-Item -ItemType Directory -Force -Path (Split-Path $Path) | Out-Null
    if (-not $NoRotate -and (Test-Path $Path) -and (Get-Item $Path).Length -gt 2MB) { Move-Item -Force $Path "$Path.1" }
    $script:ChampLogState.Path = $Path
    $script:ChampLogState.Queue = $Queue
    if ($Lock) { $script:ChampLogState.Lock = $Lock }
    $script:ChampLogState.Console = [bool]$Console
}

function Write-ChampLog {
    param([string] $Message, [string] $Level = 'info', [switch] $NoQueue)
    $s = $script:ChampLogState
    $line = '{0:yyyy-MM-dd HH:mm:ss} {1,-5} {2}' -f (Get-Date), $Level, $Message
    if ($s.Path) {
        [System.Threading.Monitor]::Enter($s.Lock)
        try { [System.IO.File]::AppendAllText($s.Path, "$line`r`n") } catch { } finally { [System.Threading.Monitor]::Exit($s.Lock) }
    }
    if ($s.Queue -and -not $NoQueue) { $s.Queue.Enqueue($Message) }
    if ($s.Console) { Write-Host $line }
}

# ---- child processes ----

function ConvertTo-ChampArgument {
    param([string] $Value)
    if ($Value -eq '') { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Test-ChampCancelled { [bool]($script:ChampSync -and $script:ChampSync.Cancel) }

# Runs a console tool with no window, streaming stdout+stderr line by line (no callbacks on other
# threads, so it is safe inside a PowerShell runspace). Returns ExitCode, Output, TimedOut, Cancelled.
function Invoke-ChampProcess {
    param(
        [Parameter(Mandatory)] [string] $File,
        [string[]] $Arguments = @(),
        [int] $TimeoutSec = 120,
        [scriptblock] $OnLine,
        [hashtable] $Environment,
        [switch] $LogOutput
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $File
    $psi.Arguments = ($Arguments | ForEach-Object { ConvertTo-ChampArgument $_ }) -join ' '
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    if ($Environment) { foreach ($k in $Environment.Keys) { $psi.EnvironmentVariables[$k] = [string]$Environment[$k] } }

    $out = New-Object System.Collections.Generic.List[string]
    $result = [pscustomobject]@{ ExitCode = -1; Output = $out; TimedOut = $false; Cancelled = $false }
    try { $p = [System.Diagnostics.Process]::Start($psi) } catch {
        $out.Add("could not start ${File}: $($_.Exception.Message)")
        return $result
    }
    $readers = @($p.StandardOutput, $p.StandardError)
    $pending = @($readers[0].ReadLineAsync(), $readers[1].ReadLineAsync())
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $quiet = [System.Diagnostics.Stopwatch]::StartNew()
    while ($pending[0] -or $pending[1]) {
        $live = [System.Threading.Tasks.Task[]]@($pending | Where-Object { $_ })
        [void][System.Threading.Tasks.Task]::WaitAny($live, 250)
        for ($i = 0; $i -lt 2; $i++) {
            $t = $pending[$i]
            if (-not $t -or -not $t.IsCompleted) { continue }
            $line = $t.Result
            if ($null -eq $line) { $pending[$i] = $null; continue }
            $pending[$i] = $readers[$i].ReadLineAsync()
            $quiet.Restart()
            $out.Add($line)
            if ($LogOutput -and $line.Trim() -and $line -notmatch '[\u2580-\u259F]|^\s*[-\\|/]\s*$') { Write-ChampLog "  $line" 'tool' }
            if ($OnLine) { & $OnLine $line }
        }
        $timedOut = $TimeoutSec -gt 0 -and $sw.Elapsed.TotalSeconds -gt $TimeoutSec
        if ($timedOut -or (Test-ChampCancelled)) {
            $result.TimedOut = $timedOut
            $result.Cancelled = -not $timedOut
            try { $p.Kill() } catch { }
            break
        }
        # A tool that started a long-lived program (Docker Desktop) can leave the pipes open.
        if ($p.HasExited -and $quiet.Elapsed.TotalSeconds -gt 2) { break }
    }
    [void]$p.WaitForExit(5000)
    if ($p.HasExited) { $result.ExitCode = $p.ExitCode }
    $p.Dispose()
    $result
}

# ---- brand mark (web/mark.svg: ink rounded square, ember disc, play triangle) ----

function New-ChampMarkBitmap {
    param([Parameter(Mandatory)] [int] $Size)
    Add-Type -AssemblyName System.Drawing
    $bmp = New-Object System.Drawing.Bitmap($Size, $Size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([System.Drawing.Color]::Transparent)
    [single]$s = $Size; [single]$d = $Size * 0.5; [single]$far = $s - $d
    [single]$zero = 0
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddArc($zero, $zero, $d, $d, [single]180, [single]90)
    $path.AddArc($far, $zero, $d, $d, [single]270, [single]90)
    $path.AddArc($far, $far, $d, $d, [single]0, [single]90)
    $path.AddArc($zero, $far, $d, $d, [single]90, [single]90)
    $path.CloseFigure()
    $g.FillPath((New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml('#0B0D12'))), $path)
    if ($Size -ge 32) {
        $pen = New-Object System.Drawing.Pen([System.Drawing.ColorTranslator]::FromHtml('#2A2F3D'), [single][Math]::Max(1, $Size / 64))
        $g.DrawPath($pen, $path)
    }
    # ember disc (web/mark.svg: r=18 of 64) with an ink play triangle cut in
    [single]$r = $Size * 18 / 64; [single]$c = $Size / 2
    $disc = New-Object System.Drawing.RectangleF(($c - $r), ($c - $r), (2 * $r), (2 * $r))
    $grad = New-Object System.Drawing.Drawing2D.LinearGradientBrush($disc, [System.Drawing.ColorTranslator]::FromHtml('#FF8C5A'),
        [System.Drawing.ColorTranslator]::FromHtml('#F0571E'), [single]45)
    $g.FillEllipse($grad, $disc)
    $u = $Size / 64.0
    $tri = [System.Drawing.PointF[]]@(
        (New-Object System.Drawing.PointF([single](27.5 * $u), [single](23.0 * $u))),
        (New-Object System.Drawing.PointF([single](27.5 * $u), [single](41.0 * $u))),
        (New-Object System.Drawing.PointF([single](43.0 * $u), [single](32.0 * $u))))
    $g.FillPolygon((New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml('#0B0D12'))), $tri)
    $g.Dispose()
    $bmp
}
