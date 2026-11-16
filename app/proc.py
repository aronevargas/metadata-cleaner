"""Cross-platform helpers for spawning external tools (ffmpeg/ffprobe, file managers).

Windows specifics handled here:
- In a windowed app (pythonw.exe or PyInstaller ``--windowed``) there is no console, so every
  console child (ffmpeg) would flash its own console window -> ``CREATE_NO_WINDOW``.
- The parent has no valid stdin handle there either; inheriting it can fail -> ``stdin=DEVNULL``.
- The default text encoding is the ANSI code page (cp1252), but ffmpeg/ffprobe print UTF-8
  (file names, tags) -> always decode as UTF-8 with ``errors="replace"`` so a reader thread can
  never die on a decode error (which would stop draining the pipe and deadlock ffmpeg).
"""
from __future__ import annotations

import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"


def subprocess_kwargs(*, text: bool = True) -> dict:
    """Keyword arguments for subprocess.run/Popen that behave the same on Linux/macOS/Windows."""
    kw: dict = {"stdin": subprocess.DEVNULL}
    if text:
        kw.update(text=True, encoding="utf-8", errors="replace")
    if IS_WINDOWS:
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return kw
