"""PyInstaller entry point: the desktop window by default; `--self-check [REPORT.json]` for CI."""
import sys

from app.__main__ import _run_desktop

if __name__ == "__main__":
    if "--self-check" in sys.argv[1:]:
        from app.selfcheck import run
        i = sys.argv.index("--self-check")
        sys.exit(run(sys.argv[i + 1] if len(sys.argv) > i + 1 else None))
    sys.exit(_run_desktop(sys.argv[1:]))
