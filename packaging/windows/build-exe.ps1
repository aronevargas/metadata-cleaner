<#
.SYNOPSIS
  Metadata Editor - builds the Windows app folder (dist\metadata-editor\metadata-editor.exe).
.DESCRIPTION
  Must run ON Windows (PyInstaller does not cross-compile). Uses .venv (created by install.ps1),
  installs requirements-build.txt (pinned PyInstaller), builds with packaging\metadata-editor.spec,
  optionally copies FFmpeg next to the exe, runs the non-interactive self-check of the built exe
  and optionally zips the folder.
    powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\build-exe.ps1 -Zip
    ... -FfmpegDir C:\ffmpeg\bin -Zip     # bundle YOUR ffmpeg.exe/ffprobe.exe (see README: licence)
.PARAMETER FfmpegDir
  Folder containing ffmpeg.exe and ffprobe.exe to copy into dist\metadata-editor\ffmpeg\.
  Nothing is downloaded by this script.
.PARAMETER RequireVideo
  Fail if the self-check of the built exe could not process a video (use when FFmpeg is installed).
.PARAMETER Zip
  Create dist\metadata-editor-windows-<arch>.zip.
#>
[CmdletBinding()]
param(
  [string]$FfmpegDir = "",
  [switch]$Zip,
  [switch]$RequireVideo,
  [switch]$SkipSelfCheck
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location -LiteralPath $Root

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
  & $Exe @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPy)) {
  Write-Host "No .venv yet - running install.ps1 first ..."
  & (Join-Path $PSScriptRoot "install.ps1")
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Invoke-Checked $venvPy @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", (Join-Path $Root "requirements-build.txt"))

$dist = Join-Path $Root "dist"
$work = Join-Path $Root "build"
Invoke-Checked $venvPy @("-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", $dist, "--workpath", $work,
  (Join-Path $Root "packaging\metadata-editor.spec"))
$app = Join-Path $dist "metadata-editor"
$exe = Join-Path $app "metadata-editor.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "Build did not produce $exe" }

if ($FfmpegDir) {
  $target = Join-Path $app "ffmpeg"
  New-Item -ItemType Directory -Force -Path $target | Out-Null
  foreach ($name in @("ffmpeg.exe", "ffprobe.exe")) {
    $src = Join-Path $FfmpegDir $name
    if (-not (Test-Path -LiteralPath $src)) { throw "$src not found" }
    Copy-Item -LiteralPath $src -Destination $target -Force
  }
  # "shared" FFmpeg builds need their DLLs next to the exes
  Get-ChildItem -LiteralPath $FfmpegDir -Filter *.dll -ErrorAction SilentlyContinue |
    Copy-Item -Destination $target -Force
  # keep the licence texts that came with that FFmpeg build (GPL/LGPL obligations)
  $parent = Split-Path -Parent $FfmpegDir
  foreach ($lic in @("LICENSE", "LICENSE.txt", "COPYING.GPLv3", "COPYING.LGPLv2.1", "README.txt")) {
    foreach ($d in @($FfmpegDir, $parent)) {
      $p = Join-Path $d $lic
      if (Test-Path -LiteralPath $p) { Copy-Item -LiteralPath $p -Destination $target -Force }
    }
  }
  Write-Host "FFmpeg copied to $target"
}

if (-not $SkipSelfCheck) {
  $report = Join-Path $dist "selfcheck-windows.json"
  if (Test-Path -LiteralPath $report) { Remove-Item -LiteralPath $report }
  # windowed exe: no console output, so the result goes to a JSON file
  $proc = Start-Process -FilePath $exe -ArgumentList @("--self-check", "`"$report`"") -Wait -PassThru
  if (-not (Test-Path -LiteralPath $report)) { throw "Self-check produced no report (exit $($proc.ExitCode))" }
  $json = Get-Content -LiteralPath $report -Raw -Encoding UTF8
  Write-Host $json
  if ($proc.ExitCode -ne 0) { throw "Self-check of the built exe failed (exit $($proc.ExitCode))" }
  if ($RequireVideo) {
    $sc = $json | ConvertFrom-Json
    if (-not $sc.checks.ffmpeg.ok -or -not $sc.checks.video.ok -or ($sc.checks.video.detail -is [string])) {
      throw "Self-check: video processing required but failed/skipped: $($sc.checks.video.detail)"
    }
  }
}

if ($Zip) {
  $arch = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
  $zipPath = Join-Path $dist "metadata-editor-windows-$arch.zip"
  if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath }
  Compress-Archive -Path $app -DestinationPath $zipPath
  $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash
  Write-Host "Zip: $zipPath  SHA256 $hash"
}
Write-Host "Done: $exe" -ForegroundColor Green
