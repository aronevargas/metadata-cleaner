"""Real Tk window tests (desktop flow). Drives app.desktop programmatically on an X display.

Display: uses $METAEDIT_TEST_DISPLAY if set (e.g. an Xvfb ':97'), otherwise $DISPLAY.
Screenshots (evidence) are written to $METAEDIT_SHOTS if set.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from conftest import ROOT, make_jpeg, make_mp4, make_png, sha256, tree_hashes

DISPLAY = os.environ.get("METAEDIT_TEST_DISPLAY") or os.environ.get("DISPLAY")
pytestmark = pytest.mark.skipif(not DISPLAY, reason="sem display X (defina DISPLAY ou METAEDIT_TEST_DISPLAY)")
SHOTS = os.environ.get("METAEDIT_SHOTS")

if DISPLAY:
    os.environ["DISPLAY"] = DISPLAY
    os.environ.pop("WAYLAND_DISPLAY", None)


def shot(root, name):
    if not SHOTS:
        return
    from PIL import ImageGrab
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    try:
        im = ImageGrab.grab(bbox=(x, y, x + w, y + h), xdisplay=DISPLAY)
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        im.save(Path(SHOTS) / f"{name}.png")
    except OSError as e:  # e.g. XWayland refuses GetImage
        print("screenshot indisponível:", e)


def pump(app, cond, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.root.update()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def gui():
    import tkinter as tk
    from app import desktop, services
    root = tk.Tk()
    app = desktop.create_app(root)
    root.update()
    yield app
    try:
        app.destroy()
    except tk.TclError:
        pass
    try:
        root.destroy()
    except tk.TclError:
        pass
    services.shutdown()


def tree_rows(tree, skip_tag="ignored"):
    return [tree.item(i, "values") for i in tree.get_children("") if skip_tag not in tree.item(i, "tags")]


def finished(app):
    j = getattr(app, "last_job", None)
    return (bool(j) and j["status"] in ("done", "error", "cancelled") and not app.busy
            and not getattr(app, "job_running", False))


def choose_dirs(app, inp, out):
    """Exercise the folder pickers through the injection hook (no modal dialog)."""
    app.ask_directory = lambda **kw: str(inp)
    app.pick_input()
    app.ask_directory = lambda **kw: str(out)
    app.pick_output()
    assert app.input_var.get() == str(inp) and app.output_var.get() == str(out)


def do_scan(app):
    app.scan()
    assert pump(app, lambda: not app.busy and app.last_scan is not None, 30), "scan não terminou"


# ---------------------------------------------------------------------------------------------
def test_window_opens(gui):
    r = gui.root
    assert r.winfo_viewable()
    assert r.title()
    assert r.winfo_width() >= 600 and r.winfo_height() >= 400, (r.winfo_width(), r.winfo_height())
    shot(r, "gui-01-open")


def test_cancelled_picker_keeps_value(gui, tmp_path):
    gui.input_var.set("/x")
    gui.ask_directory = lambda **kw: ""
    gui.pick_input()
    assert gui.input_var.get() == "/x"


def test_full_flow_images_and_video(gui, tmp_path):
    inp, out = tmp_path / "entrada", tmp_path / "saída"
    inp.mkdir()
    make_jpeg(inp / "foto.JPG", orientation=6)
    make_png(inp / "logo.png")
    make_mp4(inp / "clip.mp4", seconds=1)
    (inp / "ruim.jpg").write_bytes(b"lixo")
    (inp / "nota.txt").write_text("x")
    originals = tree_hashes(inp)
    choose_dirs(gui, inp, out)
    do_scan(gui)
    rows = tree_rows(gui.file_tree)
    assert len(rows) == 4, rows
    assert not gui.start_btn.instate(["disabled"])
    shot(gui.root, "gui-02-scan")
    gui.start()
    progress_seen = []
    assert pump(gui, lambda: progress_seen.append(float(gui.progress["value"])) or finished(gui), 180)
    gui.root.update()
    j = gui.last_job
    assert j["status"] == "done", j
    assert (j["succeeded"], j["failed"]) == (3, 1), j
    assert float(gui.progress["value"]) == pytest.approx(100, abs=0.5)
    assert progress_seen == sorted(progress_seen), "progresso regrediu"
    res = tree_rows(gui.results_tree)
    assert len(res) == 4, res
    flat = " ".join(" ".join(map(str, r)) for r in res)
    assert "ruim.jpg" in flat and "1.mp4" in flat
    assert sorted(p.name for p in out.iterdir()) == ["1.mp4", "2.jpg", "3.png"]
    assert tree_hashes(inp) == originals
    for o in out.iterdir():
        assert b"SecretCam" not in o.read_bytes() and b"VIDSECRET" not in o.read_bytes()
    assert gui.progress_text_var.get() or gui.status_var.get()
    shot(gui.root, "gui-03-done")
    # start must require a new scan (numbering refresh); re-scan shows next index
    do_scan(gui)
    assert gui.last_scan["next_index"] == 4


def test_replace_mode_via_gui(gui, tmp_path):
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    make_jpeg(inp / "a.jpg")
    gui.input_var.set(str(inp)); gui.output_var.set(str(out))
    gui.mode_var.set("replace")
    gui.meta_vars["artist"].set("AutorGUI")
    gui.meta_vars["title"].set("TituloGUI")
    gui.strength_var.set(2)
    do_scan(gui)
    gui.start()
    assert pump(gui, lambda: finished(gui), 60)
    data = (out / "1.jpg").read_bytes()
    assert b"AutorGUI" in data and b"SecretCam" not in data
    with Image.open(out / "1.jpg") as im:
        assert im.getexif().get(0x013B) == "AutorGUI"


def test_errors_shown_inline(gui, tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    make_jpeg(inp / "a.jpg")
    cases = [(str(tmp_path / "nao_existe"), str(tmp_path / "o")),   # missing input
             (str(inp), str(inp)),                                   # same dir
             (str(inp), str(tmp_path)),                              # input inside output
             ("", str(tmp_path / "o"))]                              # empty
    for i, o in cases:
        gui.error_var.set("")
        gui.input_var.set(i); gui.output_var.set(o)
        gui.scan()
        pump(gui, lambda: not gui.busy, 10)
        gui.start()
        pump(gui, lambda: not gui.busy, 10)
        assert gui.error_var.get().strip(), (i, o)
    assert not (tmp_path / "o").exists() or not any((tmp_path / "o").iterdir())
    shot(gui.root, "gui-04-error")


def test_cancel_via_gui(gui, tmp_path):
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    for k in range(12):
        make_jpeg(inp / f"{k:02d}.jpg", w=1600, h=1600)
    make_mp4(inp / "zz.mp4", seconds=6)
    gui.input_var.set(str(inp)); gui.output_var.set(str(out))
    do_scan(gui)
    gui.start()
    assert pump(gui, lambda: gui.last_job and gui.last_job["processed"] >= 1, 60)
    assert not gui.cancel_btn.instate(["disabled"])
    gui.cancel()
    assert pump(gui, lambda: finished(gui), 60)
    j = gui.last_job
    assert j["status"] == "cancelled", j
    assert any(r["status"] == "skipped" for r in j["results"])
    files = list(out.iterdir())
    assert len(files) == j["succeeded"]
    assert not [f for f in files if not f.name.split(".")[0].isdigit()], "temporário sobrou"
    shot(gui.root, "gui-05-cancel")


def test_close_window_during_job_stops_work(tmp_path):
    """WM_DELETE_WINDOW during processing must not hang and must stop the worker."""
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    make_mp4(inp / "long.mp4", seconds=20)
    code = f"""
import threading, time, tkinter as tk
from app import desktop, services
root = tk.Tk(); app = desktop.create_app(root)
app.input_var.set({str(inp)!r}); app.output_var.set({str(out)!r})
app.scan()
t = time.time()
while (app.busy or app.last_scan is None) and time.time() - t < 20: root.update(); time.sleep(0.02)
app.start()
t = time.time()
while time.time() - t < 1.5: root.update(); time.sleep(0.02)
root.after(10, lambda: root.tk.call(root.protocol('WM_DELETE_WINDOW')))
desktop_mainloop_start = time.time()
root.mainloop()
print('closed', round(time.time() - desktop_mainloop_start, 2))
"""
    t0 = time.time()
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr[-2000:]
    assert "closed" in p.stdout
    assert time.time() - t0 < 30
    time.sleep(0.5)
    ps = subprocess.run(["pgrep", "-af", str(inp / "long.mp4")], capture_output=True, text=True).stdout
    assert "ffmpeg" not in ps, f"ffmpeg órfão: {ps}"
    assert not [f for f in out.iterdir() if not f.name.split(".")[0].isdigit()] if out.exists() else True


def test_launcher_opens_window_without_server(tmp_path):
    """Default command `python -m app` must open a Tk window and listen on no port."""
    py = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv").exists() else sys.executable
    env = dict(os.environ, DISPLAY=DISPLAY)
    env.pop("WAYLAND_DISPLAY", None)
    p = subprocess.Popen([py, "-m", "app"], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(4)
        assert p.poll() is None, p.stdout.read()
        ss = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
        assert f"pid={p.pid}," not in ss, "launcher abriu porta de rede"
        # no browser child processes
        kids = subprocess.run(["pgrep", "-P", str(p.pid), "-a"], capture_output=True, text=True).stdout
        assert not any(b in kids for b in ("firefox", "chrom", "xdg-open")), kids
        if SHOTS:
            from PIL import ImageGrab
            try:
                im = ImageGrab.grab(xdisplay=DISPLAY)
                im.save(Path(SHOTS) / "gui-00-launcher-screen.png")
                assert np.asarray(im).std() > 5, "tela vazia: janela não desenhada?"
            except OSError as e:
                print("screenshot indisponível:", e)
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
            pytest.fail("launcher não encerrou com SIGTERM")


def test_shortcut_script_exists():
    s = ROOT / "metadata-editor"
    assert s.exists() and os.access(s, os.X_OK)


@pytest.mark.parametrize("size", [None, "820x560"])
def test_primary_actions_visible_without_scrolling(gui, size):
    r = gui.root
    if size:
        r.geometry(size)
    r.update()
    for btn in (gui.scan_btn, gui.start_btn, gui.cancel_btn):
        y = btn.winfo_rooty() - r.winfo_rooty()
        x = btn.winfo_rootx() - r.winfo_rootx()
        assert btn.winfo_viewable(), btn
        assert 0 <= y and y + btn.winfo_height() <= r.winfo_height(), (str(btn), y, r.winfo_height())
        assert 0 <= x and x + btn.winfo_width() <= r.winfo_width(), (str(btn), x, r.winfo_width())


# --- Seletor de pastas real (sem hook de injeção) ------------------------------------------------
def _fake_zenity(tmp_path, body):
    d = tmp_path / "bin"
    d.mkdir()
    z = d / "zenity"
    z.write_text("#!/bin/sh\n" + body + "\n")
    z.chmod(0o755)
    return d


def test_picker_zenity_selection_and_cancel(gui, tmp_path, monkeypatch):
    """native_ask_directory via zenity: argumentos, pasta escolhida e cancelamento (exit 1)."""
    from app.desktop import window
    target = tmp_path / "pasta escolhida ç"
    target.mkdir()
    log = tmp_path / "args.txt"
    bindir = _fake_zenity(tmp_path, f'printf "%s\\n" "$@" > "{log}"; sleep 0.3; echo "{target}"')
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.delenv("METAEDIT_TK_PICKER", raising=False)
    if not sys.platform.startswith("linux"):
        pytest.skip("zenity só no Linux")
    got = window.native_ask_directory(parent=gui.root, title="Entrada", initialdir=str(tmp_path))
    assert got == str(target)
    args = log.read_text().splitlines()
    assert "--directory" in args and "--title=Entrada" in args and f"--filename={tmp_path}/" in args
    # fluxo completo pela janela: botão Procurar… usa o seletor real
    gui.ask_directory = window.native_ask_directory
    gui.pick_input()
    assert gui.input_var.get() == str(target)
    # cancelamento: zenity sai com 1 e nada impresso -> valor anterior preservado
    (bindir / "zenity").write_text("#!/bin/sh\nexit 1\n")
    gui.pick_input()
    assert gui.input_var.get() == str(target)


def test_picker_tk_fallback_real_dialog(gui, tmp_path, monkeypatch):
    """METAEDIT_TK_PICKER=1: abre o tk_chooseDirectory real e confirma via Tcl."""
    from app.desktop import window
    monkeypatch.setenv("METAEDIT_TK_PICKER", "1")
    target = tmp_path / "alvo"
    target.mkdir()
    r = gui.root
    seen = {}

    def confirm():
        dlgs = [w for w in r.tk.call("winfo", "children", ".") if "choosedir" in str(w).lower()
                or "__tk_choosedir" in str(w)]
        if not dlgs:
            r.after(100, confirm)
            return
        d = str(dlgs[0])
        seen["dlg"] = d
        # digita o caminho e clica OK: o 1º OK navega até a pasta, o 2º (entrada vazia) confirma
        r.tk.eval(f"{d}.contents.f2.ent delete 0 end; {d}.contents.f2.ent insert 0 {{{target}}}")
        r.tk.eval(f"{d}.contents.f2.ok invoke")
        r.after(300, lambda: r.tk.eval(f"if {{[winfo exists {d}]}} {{{d}.contents.f2.ok invoke}}"))
        r.after(10000, lambda: r.tk.eval(f"if {{[winfo exists {d}]}} {{destroy {d}}}"))  # nunca travar

    r.after(300, confirm)
    got = window.native_ask_directory(parent=r, title="Saída", initialdir=str(tmp_path))
    assert seen.get("dlg"), "diálogo Tk não apareceu"
    assert os.path.realpath(got) == os.path.realpath(target)


def test_shell_launcher_creates_visible_window(tmp_path):
    """`./metadata-editor` (atalho do README) abre janela X 'Editor de Metadados', sem porta e sem
    carregar o servidor web legado."""
    import shutil as _sh
    if not _sh.which("xwininfo"):
        pytest.skip("xwininfo ausente")
    env = dict(os.environ, DISPLAY=DISPLAY, HOME=str(tmp_path))
    env.pop("WAYLAND_DISPLAY", None)
    p = subprocess.Popen([str(ROOT / "metadata-editor"), "--no-restore"], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 20
        tree = ""
        while time.time() < deadline:
            tree = subprocess.run(["xwininfo", "-root", "-tree"], env=env, capture_output=True,
                                  text=True).stdout
            if '"metadata-editor"' in tree:
                break
            time.sleep(0.3)
        assert p.poll() is None, p.stdout.read()
        line = next((ln for ln in tree.splitlines() if '"metadata-editor"' in ln), "")
        assert "Editor de Metadados" in line, tree[-2000:]
        maps = Path(f"/proc/{p.pid}/maps")
        if maps.exists():
            m = maps.read_text()
            assert "uvicorn" not in m and "fastapi" not in m and "pydantic_core" not in m
        ss = subprocess.run(["ss", "-ltnup"], capture_output=True, text=True).stdout
        assert f"pid={p.pid}," not in ss
    finally:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
