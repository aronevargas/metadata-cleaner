# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec shared by Linux (packaging/build-pyinstaller.sh) and Windows
# (packaging/windows/build-exe.ps1). It must be built ON the target OS: PyInstaller does not
# cross-compile. Output: dist/metadata-editor/ (onedir; "metadata-editor.exe" on Windows).
# FFmpeg is NOT bundled here; build-exe.ps1 -FfmpegDir copies a user-supplied ffmpeg.exe and
# ffprobe.exe into dist/metadata-editor/ffmpeg/, where app.media looks for them first.
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821  (SPECPATH is set by PyInstaller)
ICON = None
for cand in ("app/desktop/assets/metadata-editor.ico", "packaging/windows/metadata-editor.ico"):
    if sys.platform == "win32" and os.path.isfile(os.path.join(ROOT, cand)):
        ICON = os.path.join(ROOT, cand)
        break
ASSETS = os.path.join(ROOT, "app", "desktop", "assets")
datas = [(ASSETS, "app/desktop/assets")] if os.path.isdir(ASSETS) else []

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "packaging", "entry.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=collect_submodules("app.desktop") + ["app.selfcheck", "app.proc"],
    excludes=["fastapi", "uvicorn", "starlette", "pydantic", "app.main", "app.picker",
              "pytest", "httpx"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="metadata-editor",
    console=False,          # windowed: no console window on Windows
    icon=ICON,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="metadata-editor")  # noqa: F821
