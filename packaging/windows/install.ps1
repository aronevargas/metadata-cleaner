<#
.SYNOPSIS
  Metadata Editor - installs the desktop app on Windows from this source folder.
.DESCRIPTION
  Creates .venv in the project folder, installs requirements.txt (Pillow, numpy), checks Tkinter
  and FFmpeg, and optionally creates Start Menu / Desktop shortcuts that open the window
  (pythonw.exe, no console).
  Run from any folder:
    powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\install.ps1 -Shortcut
  or double-click packaging\windows\install.bat.
.PARAMETER Python
  Python to use (default: "py -3" launcher, then "python" from PATH). Needs 3.10+ with Tkinter.
  Either a path/name (C:\Python313\python.exe, python) or a launcher command with arguments
  ("py -3.13"); a value that is not an existing file is split on spaces.
.PARAMETER Shortcut
  Also create "Metadata Editor" shortcuts in the Start Menu and on the Desktop.
.PARAMETER Dev
  Also install requirements-dev.txt (pytest) to run the test suite.
#>
[CmdletBinding()]
param(
  [string]$Python = "",
  [switch]$Shortcut,
  [switch]$Dev
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location -LiteralPath $Root

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
  & $Exe @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $($Arguments -join ' ')" }
}

# 1. Find a suitable Python (3.10+ with tkinter).
$candidates = @()
if ($Python) {
  if (Test-Path -LiteralPath $Python -PathType Leaf) { $candidates += ,@($Python) }
  else { $candidates += ,@($Python -split '\s+' | Where-Object { $_ }) }
}
if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += ,@("py", "-3") }
if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += ,@("python") }
$check = "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 3)"
$py = $null
foreach ($c in $candidates) {
  $exe = $c[0]; $pre = @($c | Select-Object -Skip 1)
  try { & $exe @pre -c $check 2>$null; if ($LASTEXITCODE -eq 0) { $py = $c; break } } catch { }
}
if (-not $py) {
  Write-Host "ERROR: Python 3.10+ with Tkinter not found." -ForegroundColor Red
  Write-Host "Install it from https://www.python.org/downloads/windows/ (keep 'tcl/tk and IDLE' checked)"
  Write-Host "or run: winget install Python.Python.3.13   then open a NEW terminal and retry."
  exit 1
}
$pyExe = $py[0]; $pyPre = @($py | Select-Object -Skip 1)
Write-Host ("Using Python: " + (& $pyExe @pyPre -c "import sys; print(sys.executable, sys.version.split()[0])"))

# 2. Virtual environment + dependencies.
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPy)) {
  Write-Host "Creating .venv ..."
  Invoke-Checked $pyExe ($pyPre + @("-m", "venv", (Join-Path $Root ".venv")))
}
Invoke-Checked $venvPy @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", (Join-Path $Root "requirements.txt"))
if ($Dev) {
  Invoke-Checked $venvPy @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", (Join-Path $Root "requirements-dev.txt"))
}

# 3. Self-check (Tk window, image processing, FFmpeg) without opening the UI.
$report = Join-Path $env:TEMP "metadata-editor-selfcheck.json"
& $venvPy -m app --self-check $report | Out-Null
$sc = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($p in $sc.checks.PSObject.Properties) {
  $state = if ($p.Value.ok) { "OK  " } elseif ($p.Value.required) { "FAIL" } else { "WARN" }
  Write-Host ("  [{0}] {1}: {2}" -f $state, $p.Name, ($p.Value.detail | ConvertTo-Json -Compress -Depth 4))
}
if (-not $sc.ok) { Write-Host "Self-check failed; see $report" -ForegroundColor Red; exit 1 }
if (-not $sc.checks.ffmpeg.ok) {
  Write-Host "NOTE: FFmpeg not found - images work, videos will fail." -ForegroundColor Yellow
  Write-Host "      Install: winget install Gyan.FFmpeg   (then open a new terminal)"
  Write-Host "      or put ffmpeg.exe + ffprobe.exe in: $Root\ffmpeg\bin\"
}

# 4. Shortcuts (optional).
if ($Shortcut) {
  $pyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
  $shell = New-Object -ComObject WScript.Shell
  $targets = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Metadata Editor.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Metadata Editor.lnk")
  )
  foreach ($lnkPath in $targets) {
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = $pyw
    $lnk.Arguments = "-m app"
    $lnk.WorkingDirectory = $Root
    $lnk.Description = "Metadata Editor"
    $ico = Join-Path $Root "app\desktop\assets\metadata-editor.ico"
    if (Test-Path -LiteralPath $ico) { $lnk.IconLocation = $ico }
    $lnk.Save()
    Write-Host "Shortcut: $lnkPath"
  }
}
Write-Host ""
Write-Host "Done. Start the app with: metadata-editor.bat  (or .venv\Scripts\pythonw.exe -m app)" -ForegroundColor Green
