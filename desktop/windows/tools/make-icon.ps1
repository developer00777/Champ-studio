# Regenerates assets\champstudio.ico from the brand mark (the web app's favicon: ink rounded square,
# ember dot). Small sizes are stored as 32-bit bitmaps, 128 and 256 as PNG.
#   powershell -ExecutionPolicy Bypass -File desktop\windows\tools\make-icon.ps1

. (Join-Path $PSScriptRoot '..\lib\Common.ps1')

function Get-ChampDib {
    param([System.Drawing.Bitmap] $Bitmap)
    $w = $Bitmap.Width; $h = $Bitmap.Height
    $rect = New-Object System.Drawing.Rectangle(0, 0, $w, $h)
    $bits = $Bitmap.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $raw = New-Object byte[] ($bits.Stride * $h)
    [System.Runtime.InteropServices.Marshal]::Copy($bits.Scan0, $raw, 0, $raw.Length)
    $Bitmap.UnlockBits($bits)
    $maskRow = [int]([Math]::Ceiling($w / 32.0) * 4)
    $ms = New-Object System.IO.MemoryStream
    $bw = New-Object System.IO.BinaryWriter($ms)
    foreach ($v in @(40, $w, ($h * 2))) { $bw.Write([int]$v) }               # BITMAPINFOHEADER
    $bw.Write([int16]1); $bw.Write([int16]32)
    foreach ($v in @(0, ($w * $h * 4 + $maskRow * $h), 0, 0, 0, 0)) { $bw.Write([int]$v) }
    for ($y = $h - 1; $y -ge 0; $y--) { $bw.Write($raw, $y * $bits.Stride, $w * 4) }   # bottom-up BGRA
    $bw.Write((New-Object byte[] ($maskRow * $h)))                            # AND mask (alpha does the work)
    $bw.Flush()
    $ms.ToArray()
}

$sizes = 16, 20, 24, 32, 40, 48, 64, 128, 256
$frames = foreach ($s in $sizes) {
    $bmp = New-ChampMarkBitmap -Size $s
    if ($s -ge 128) {
        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
        , $ms.ToArray()
    } else { , (Get-ChampDib $bmp) }
    $bmp.Dispose()
}

$out = Join-Path $PSScriptRoot '..\assets\champstudio.ico'
New-Item -ItemType Directory -Force -Path (Split-Path $out) | Out-Null
$fs = [System.IO.File]::Create($out)
$bw = New-Object System.IO.BinaryWriter($fs)
$bw.Write([int16]0); $bw.Write([int16]1); $bw.Write([int16]$sizes.Count)    # ICONDIR
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {                                    # ICONDIRENTRY
    $dim = if ($sizes[$i] -ge 256) { 0 } else { $sizes[$i] }
    $bw.Write([byte]$dim); $bw.Write([byte]$dim); $bw.Write([byte]0); $bw.Write([byte]0)
    $bw.Write([int16]1); $bw.Write([int16]32)
    $bw.Write([int]$frames[$i].Length); $bw.Write([int]$offset)
    $offset += $frames[$i].Length
}
foreach ($f in $frames) { $bw.Write($f) }
$bw.Close()
Write-Host "Wrote $((Resolve-Path $out).Path) ($((Get-Item $out).Length) bytes)"
