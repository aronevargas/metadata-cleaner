"""Optional native folder picker for the LEGACY web mode. Runs in a subprocess so it never blocks the
server loop. (The desktop window uses app.desktop.window.native_ask_directory instead.)

Windows: the Tk dialog is the native Explorer folder dialog. The child writes the path as UTF-8 bytes so
Unicode paths survive regardless of the console code page, and it is started without a console window.
"""
import os
import shutil
import subprocess
import sys

_TK_SCRIPT = r"""
import sys, tkinter as tk
from tkinter import filedialog
if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
root = tk.Tk(); root.withdraw()
try: root.attributes('-topmost', True)
except Exception: pass
p = filedialog.askdirectory(title=sys.argv[1], initialdir=sys.argv[2] or None, mustexist=True)
if not isinstance(p, str):
    p = ''
if p and sys.platform == 'win32':
    import os
    p = os.path.normpath(p)
sys.stdout.buffer.write(p.encode('utf-8')); sys.stdout.flush()
"""


def _subprocess_kwargs() -> dict:
    try:
        from .proc import subprocess_kwargs  # backend helper (CREATE_NO_WINDOW on Windows)

        return dict(subprocess_kwargs())
    except Exception:  # noqa: BLE001
        kw: dict = {"stdin": subprocess.DEVNULL}
        if sys.platform == "win32":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return kw


def _has_display() -> bool:
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _can_run_tk_child() -> bool:
    # In a frozen build (PyInstaller) sys.executable is the app itself, not a Python interpreter:
    # `exe -c script` would start a second copy of the app instead of the dialog.
    return not getattr(sys, "frozen", False)


def method() -> str | None:
    if os.environ.get("METAEDIT_NO_PICKER") == "1" or not _has_display():
        return None
    if sys.platform.startswith("linux"):
        if shutil.which("zenity"):
            return "zenity"
        if shutil.which("kdialog"):
            return "kdialog"
    if not _can_run_tk_child():
        return None
    try:
        import tkinter  # noqa: F401
        return "tkinter"
    except Exception:  # noqa: BLE001
        return None


def pick(title: str, initial: str | None) -> dict:
    m = method()
    if m is None:
        return {"path": None, "supported": False, "cancelled": False}
    initial = os.path.expanduser(initial) if initial else os.path.expanduser("~")
    if m == "zenity":
        cmd = ["zenity", "--file-selection", "--directory", f"--title={title}",
               f"--filename={initial.rstrip('/')}/"]
    elif m == "kdialog":
        cmd = ["kdialog", "--getexistingdirectory", initial, "--title", title]
    else:
        cmd = [sys.executable, "-c", _TK_SCRIPT, title, initial]
    kw = _subprocess_kwargs()
    for k in ("stdout", "stderr", "capture_output", "text", "encoding", "errors"):
        kw.pop(k, None)
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=600, **kw)
    except Exception:  # noqa: BLE001
        return {"path": None, "supported": False, "cancelled": False}
    path = r.stdout.decode("utf-8", errors="replace").strip()
    if r.returncode == 0 and path:
        return {"path": path, "supported": True, "cancelled": False}
    return {"path": None, "supported": True, "cancelled": True}
