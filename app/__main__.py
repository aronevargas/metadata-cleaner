"""Launcher.

  python -m app                 -> desktop window (Tkinter), no browser, no HTTP server
  python -m app --web [--port N] [--no-browser]
                                -> LEGACY local web server on 127.0.0.1 (needs fastapi+uvicorn)
  python -m app --version
  python -m app --self-check [REPORT.json]
                                -> non-interactive smoke test (CI / packaged executable)
For backward compatibility `--port`/`--no-browser` alone also start the legacy web server.
"""
import argparse
import os
import sys

from . import __version__


def _run_web(port: int, open_browser: bool) -> int:
    try:
        import threading
        import webbrowser

        import uvicorn

        from .main import create_app
    except ImportError as e:
        print(f"Modo web (legado) requer dependências extras: pip install -r requirements-web.txt ({e})",
              file=sys.stderr)
        return 2
    host = "127.0.0.1"  # loopback only, never configurable
    url = f"http://{host}:{port}/"
    print(f"Metadata Editor (modo web legado) em {url}  (Ctrl+C para sair)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(port=port), host=host, port=port, log_level="info")
    return 0


def _fatal(msg: str) -> int:
    """Report a startup error. Under pythonw.exe / a windowed .exe sys.stderr is None, so on
    Windows also show a native message box (ctypes, works even without Tk)."""
    if sys.stderr is not None:
        print(msg, file=sys.stderr)
    if sys.platform == "win32" and (sys.stderr is None or getattr(sys, "frozen", False)):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "Metadata Editor", 0x10)  # MB_ICONERROR
        except Exception:  # noqa: BLE001
            pass
    return 2


def _run_desktop(argv: list[str]) -> int:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        return _fatal("Tkinter não está disponível neste Python. Linux (Debian/Ubuntu): sudo apt install "
                      "python3-tk; Fedora: sudo dnf install python3-tkinter; macOS/Windows: use o Python de "
                      "python.org (marque 'tcl/tk and IDLE' no instalador).")
    missing = ("Dependência ausente ({}). Rode a instalação: pip install -r requirements.txt "
               "(Windows: packaging\\windows\\install.ps1).")
    try:
        from .desktop import main as desktop_main
    except ImportError as e:
        return _fatal(missing.format(e))
    try:
        return int(desktop_main(argv) or 0)
    except ImportError as e:
        return _fatal(missing.format(e))
    except tkinter.TclError as e:
        return _fatal(f"Não foi possível abrir a janela (sem ambiente gráfico?): {e}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="metadata-editor", description="Metadata Editor")
    parser.add_argument("--web", action="store_true", help="modo web legado (servidor local 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="porta do modo web (padrão 8765)")
    parser.add_argument("--no-browser", action="store_true", help="modo web: não abrir o navegador")
    parser.add_argument("--version", action="version", version=f"Metadata Editor {__version__}")
    parser.add_argument("--self-check", nargs="?", const="", default=None, metavar="REPORT.json",
                        help="teste automático sem interação (imagens, Tk, FFmpeg); grava JSON")
    args, rest = parser.parse_known_args(argv)

    if args.self_check is not None:
        from .selfcheck import run
        return run(args.self_check or None)
    if args.web or args.port is not None or args.no_browser:
        port = args.port if args.port is not None else int(os.environ.get("METAEDIT_PORT", "8765"))
        return _run_web(port, not args.no_browser)
    return _run_desktop(rest)


if __name__ == "__main__":
    sys.exit(main())
