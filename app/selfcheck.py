"""`python -m app --self-check [REPORT.json]`: non-interactive smoke test of an install/build.

Designed for CI and for checking a windowed executable (which has no console): the result is
written as JSON to REPORT (default: stdout when available) and the exit code is 0 only if every
required check passed. Checks: imports, Tk window creation, image processing end-to-end through
`app.services` in a temp folder (Unicode + spaces in the path), FFmpeg discovery and, when FFmpeg
is available, a tiny video round-trip.
"""
from __future__ import annotations

import json
import platform
import sys
import tempfile
import time
import traceback
from pathlib import Path


def _check(results: dict, name: str, fn, required: bool = True) -> None:
    t = time.monotonic()
    try:
        detail = fn()
        results[name] = {"ok": True, "required": required, "detail": detail}
    except Exception as e:  # noqa: BLE001
        results[name] = {"ok": False, "required": required, "detail": f"{type(e).__name__}: {e}",
                         "trace": traceback.format_exc()[-1500:]}
    results[name]["seconds"] = round(time.monotonic() - t, 2)


def run(report: str | None = None) -> int:
    from . import __version__
    res: dict = {}
    out: dict = {"version": __version__, "python": sys.version, "platform": platform.platform(),
                 "frozen": bool(getattr(sys, "frozen", False)), "executable": sys.executable,
                 "checks": res}

    def imports():
        import numpy
        import PIL

        from . import desktop, services  # noqa: F401
        from .desktop import window  # noqa: F401  (GUI module, imported lazily by the app)
        return {"numpy": numpy.__version__, "pillow": PIL.__version__}

    def tk_window():
        import tkinter as tk

        from . import desktop
        root = tk.Tk()
        try:
            root.withdraw()
            desktop.create_app(root, restore_state=False)  # builds the real main window
            root.update_idletasks()
            return {"tk": str(root.tk.call("info", "patchlevel")), "title": root.title()}
        finally:
            root.destroy()

    def images():
        import numpy as np
        from PIL import Image

        from . import services
        with tempfile.TemporaryDirectory() as d:
            inp = Path(d) / "entrada ação ü"
            out = Path(d) / "saída 結果"
            inp.mkdir()
            arr = (np.arange(48 * 32 * 3) % 251).astype("uint8").reshape(32, 48, 3)
            Image.fromarray(arr).save(inp / "foto 1.jpg", quality=90)
            Image.fromarray(arr).save(inp / "imagem 2.png")
            job = services.wait_job(services.start_job(str(inp), str(out)), timeout=60)
            if job["status"] != "done" or job["succeeded"] != 2:
                raise RuntimeError(f"job {job['status']}: {[r['error'] for r in job['results']]}")
            return sorted(p.name for p in out.iterdir())

    def ffmpeg():
        from . import media
        info = media.ffmpeg_info()
        if not info["available"]:
            raise RuntimeError(info["error"])
        return {"path": info["path"], "ffprobe": info.get("ffprobe"), "version": info["version"]}

    def video():
        import subprocess

        from . import media, services
        from .proc import subprocess_kwargs
        info = media.ffmpeg_info()
        if not info["available"]:
            return "pulado: FFmpeg ausente"
        with tempfile.TemporaryDirectory() as d:
            inp, out = Path(d) / "vídeo in", Path(d) / "vídeo out"
            inp.mkdir()
            subprocess.run([info["path"], "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc=size=64x48:rate=10:duration=1", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", str(inp / "clipe ç.mp4")],
                           check=True, capture_output=True, timeout=60, **subprocess_kwargs())
            job = services.wait_job(services.start_job(str(inp), str(out)), timeout=120)
            if job["status"] != "done" or job["succeeded"] != 1:
                raise RuntimeError(f"job {job['status']}: {[r['error'] for r in job['results']]}")
            return sorted(p.name for p in out.iterdir())

    _check(res, "imports", imports)
    _check(res, "tk_window", tk_window)
    _check(res, "images", images)
    _check(res, "ffmpeg", ffmpeg, required=False)
    _check(res, "video", video, required=False)
    out["ok"] = all(c["ok"] for c in res.values() if c["required"])
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if report:
        Path(report).write_text(text, encoding="utf-8")
    elif sys.stdout is not None:
        sys.stdout.write(text + "\n")
    return 0 if out["ok"] else 1
