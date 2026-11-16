"""Portabilidade Windows — testes SIMULADOS/ESTÁTICOS executados no Linux.

Não substituem execução real no Windows (veja TEST-REPORT.md, seção Windows). Cobrem:
- subprocessos: sem console (CREATE_NO_WINDOW), stdin=DEVNULL, decodificação UTF-8 tolerante;
- leitura do stderr do ffmpeg com bytes inválidos (não pode travar nem matar a thread);
- execução sob locale não-UTF-8 (equivalente ao cp1252 do Windows: encoding padrão ≠ UTF-8);
- publicação sem hardlink (FAT32/exFAT/SMB): byte a byte idêntica, modo binário (O_BINARY);
- temporário bloqueado (PermissionError no unlink) não derruba o job;
- ffmpeg/ffprobe "ao lado do exe" (sys.frozen) e via METAEDIT_FFMPEG;
- regras de caminho do Windows (normcase, drive, UNC) com ntpath;
- revisão estática dos scripts Windows (.bat, .ps1, .spec, workflow) quando existirem.
"""
import ast
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from conftest import FFMPEG, FFPROBE, ROOT, make_jpeg, make_mp4, sha256

APP = ROOT / "app"
POSIX_ONLY_LATE = pytest.mark.skipif(sys.platform == "win32", reason="simulação POSIX (shebang/locale C)")
SUBPROCESS_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}


# --------------------------------------------------------------------------------- app.proc
def test_proc_kwargs_on_windows(monkeypatch):
    from app import proc
    monkeypatch.setattr(proc, "IS_WINDOWS", True)
    kw = proc.subprocess_kwargs()
    assert kw["creationflags"] & 0x08000000, "CREATE_NO_WINDOW ausente: console piscaria no .exe --windowed"
    assert kw["stdin"] is subprocess.DEVNULL
    assert kw["encoding"].lower().replace("-", "") == "utf8" and kw["errors"] == "replace"
    kwb = proc.subprocess_kwargs(text=False)
    assert "encoding" not in kwb and kwb["creationflags"] & 0x08000000


def test_proc_kwargs_on_linux_has_no_creationflags(monkeypatch):
    from app import proc
    monkeypatch.setattr(proc, "IS_WINDOWS", False)
    assert "creationflags" not in proc.subprocess_kwargs()


def _subprocess_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess" \
                and node.func.attr in SUBPROCESS_FUNCS:
            yield node


def _uses_kwargs_helper(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg is None and isinstance(kw.value, ast.Call):
            f = kw.value.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name == "subprocess_kwargs":
                return True
    return False


# Subprocessos que só existem fora do Windows (motivo documentado) ficam isentos.
LINUX_ONLY = {
    ("app/desktop/window.py", "zenity/kdialog"),
    ("app/picker.py", "zenity/kdialog legado web"),
}


def test_every_subprocess_call_uses_portable_kwargs():
    """Estático: toda chamada subprocess.* em app/ usa **subprocess_kwargs() (sem console no Windows,
    stdin DEVNULL, UTF-8). Exceções só para código restrito ao Linux."""
    offenders = []
    for py in sorted(APP.rglob("*.py")):
        rel = py.relative_to(ROOT).as_posix()
        src = py.read_text(encoding="utf-8")
        for call in _subprocess_calls(ast.parse(src)):
            if _uses_kwargs_helper(call):
                continue
            seg = ast.get_source_segment(src, call) or ""
            if any(rel == f for f, _ in LINUX_ONLY) and re.search(r"zenity|kdialog|cmd\b", seg + src):
                # aceita só se a chamada está num ramo Linux (verificado no próximo teste)
                continue
            offenders.append(f"{rel}:{call.lineno}: {seg[:90]}")
    assert not offenders, "subprocess sem **subprocess_kwargs():\n" + "\n".join(offenders)


def test_linux_only_subprocesses_are_guarded():
    src = (APP / "desktop" / "window.py").read_text(encoding="utf-8")
    fn = src[src.index("def native_ask_directory"):]
    fn = fn[:fn.index("\nclass ") if "\nclass " in fn else len(fn)]
    assert 'sys.platform.startswith("linux")' in fn, "zenity/kdialog deve rodar só no Linux"
    assert "filedialog.askdirectory" in fn, "Windows/macOS devem usar o diálogo nativo do Tk"


# --------------------------------------------------------------------------- stderr do ffmpeg
@POSIX_ONLY_LATE
@pytest.mark.skipif(not FFPROBE or not FFMPEG, reason="ffmpeg/ffprobe ausentes")
def test_ffmpeg_non_utf8_stderr_does_not_hang(tmp_path, monkeypatch):
    """Fake ffmpeg escreve >256 KiB de bytes inválidos em UTF-8/cp1252 no stderr e falha:
    deve virar MediaError rápido (thread leitora não pode morrer e travar o pipe)."""
    import threading
    from app import media
    fake = tmp_path / "ffmpeg"
    fake.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys
        if "-version" in sys.argv:
            print("ffmpeg version 9.9-fake"); sys.exit(0)
        junk = b"\\x81\\x8d\\x8f\\x90\\x9d\\xff\\xfe erro \\xc3\\xa7\\xe3\\x81\\x82\\n"
        for _ in range(20000):
            sys.stderr.buffer.write(junk)
        sys.stderr.flush()
        sys.exit(3)
        """))
    fake.chmod(0o755)
    monkeypatch.setenv("METAEDIT_FFMPEG", str(fake))
    monkeypatch.setenv("METAEDIT_FFPROBE", FFPROBE)
    src = tmp_path / "v.mp4"
    make_mp4(src, seconds=1, audio=False)
    t0 = time.monotonic()
    with pytest.raises(media.MediaError) as ei:
        media.process_video(src, tmp_path / "o.mp4", ".mp4", metadata_mode="strip", metadata={},
                            pixel_noise=True, noise_strength=1, cancel=threading.Event())
    assert time.monotonic() - t0 < 30
    assert "FFmpeg falhou" in str(ei.value) and "erro" in str(ei.value)


@POSIX_ONLY_LATE
@pytest.mark.skipif(not FFPROBE or not FFMPEG, reason="ffmpeg/ffprobe ausentes")
def test_job_under_non_utf8_locale(tmp_path):
    """Simula o Windows (encoding padrão ≠ UTF-8): locale C, sem modo UTF-8, sem coerção.
    Vídeo com tags Unicode (que o ffprobe devolve em UTF-8 no JSON) deve ser processado.
    Caminhos ficam ASCII aqui: no Linux com locale C o *sistema de arquivos* também vira ASCII, o que não
    acontece no Windows (APIs wide); caminhos Unicode são cobertos pelos demais testes."""
    inp, out = tmp_path / "entrada", tmp_path / "saida"
    inp.mkdir()
    src = inp / "video com espaco.mp4"
    make_mp4(tmp_path / "raw.mp4", seconds=1)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(tmp_path / "raw.mp4"), "-c", "copy",
                    "-metadata", "title=ação 日本語 ü", "-metadata", "comment=çãõ ✓", str(src)], check=True)
    code = textwrap.dedent(f"""
        import locale, sys
        from app import services
        print("enc", locale.getpreferredencoding(False), sys.flags.utf8_mode, flush=True)
        jid = services.start_job({str(inp)!a}, {str(out)!a})
        j = services.wait_job(jid, timeout=120)
        print([(r["status"], r.get("error")) for r in j["results"]])
        assert j["status"] == "done" and all(r["status"] == "done" for r in j["results"]), j["results"]
        """)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "LANG", "PYTHONUTF8"))}
    env.update(LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "enc" in p.stdout and "utf-8" not in p.stdout.split("\n")[0].lower(), p.stdout  # locale realmente não-UTF-8
    assert sorted(x.name for x in out.iterdir()) == ["1.mp4"]


# ------------------------------------------------------------------- publicação sem hardlink
def test_publish_without_hardlink_is_byte_identical(tmp_path, monkeypatch):
    """FAT32/exFAT/alguns compartilhamentos SMB não têm hardlink: o fallback (O_EXCL + cópia) precisa
    ser binário (no Windows, os.open sem O_BINARY traduz LF->CRLF) e nunca sobrescrever."""
    from app import paths
    out = tmp_path / "out"
    out.mkdir()
    payload = bytes(range(256)) * 64 + b"\n\r\n\x1a\n"
    tmp = out / ".metaedit-tmp-x.bin"
    tmp.write_bytes(payload)
    (out / "1.bin").write_bytes(b"existente")

    def no_link(*a, **k):
        raise OSError(1, "Operation not permitted")
    monkeypatch.setattr(paths.os, "link", no_link)
    name, n = paths.publish_exclusive(tmp, out, 1, ".bin")
    assert (name, n) == ("2.bin", 2)
    assert (out / "2.bin").read_bytes() == payload
    assert (out / "1.bin").read_bytes() == b"existente"


def test_publish_fallback_opens_in_binary_mode():
    """Estático: o os.open do fallback inclui O_BINARY (no-op fora do Windows)."""
    src = (APP / "paths.py").read_text(encoding="utf-8")
    body = src[src.index("def publish_exclusive"):]
    assert "O_BINARY" in body, "fallback sem hardlink abre arquivo sem O_BINARY: corrompe no Windows"


def test_locked_temp_file_does_not_kill_job(tmp_path, monkeypatch):
    """Windows: antivírus/indexador pode segurar o temporário. PermissionError no unlink não pode
    deixar o job 'running' para sempre."""
    from app import jobs, services
    from app.formats import TMP_PREFIX
    real_unlink = os.unlink
    calls = []

    def locked_unlink(p, *a, **k):
        if TMP_PREFIX in os.fspath(p):
            calls.append(p)
            raise PermissionError(13, "O arquivo está sendo usado por outro processo")
        return real_unlink(p, *a, **k)
    monkeypatch.setattr(jobs.os, "unlink", locked_unlink)
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)  # não esperar os retries
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    make_jpeg(inp / "a.jpg")
    make_jpeg(inp / "b.jpg")
    j = services.wait_job(services.start_job(str(inp), str(out)), timeout=60)
    assert j["status"] == "done" and [r["status"] for r in j["results"]] == ["done", "done"], j
    assert calls, "unlink do temporário não foi chamado"
    assert {"1.jpg", "2.jpg"} <= {p.name for p in out.iterdir()}


# --------------------------------------------------------------------- ffmpeg ao lado do exe
POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="ferramentas falsas em shell script (POSIX)")


def _fake_tool(path: Path, name: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho '{name} version 7.0-bundled'\n")
    path.chmod(0o755)


@POSIX_ONLY
@pytest.mark.parametrize("sub", ["", "ffmpeg", "ffmpeg/bin"])
def test_ffmpeg_next_to_frozen_exe(tmp_path, monkeypatch, sub):
    from app import media
    exe_dir = tmp_path / "Metadata Editor ç"
    exe_dir.mkdir()
    for tool in ("ffmpeg", "ffprobe"):
        _fake_tool(exe_dir / sub / (tool + media._EXE), tool)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "metadata-editor.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(exe_dir / "_internal"), raising=False)
    monkeypatch.delenv("METAEDIT_FFMPEG", raising=False)
    monkeypatch.delenv("METAEDIT_FFPROBE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "vazio"))
    info = media.ffmpeg_info()
    assert info["available"], info
    assert Path(info["path"]).parent == exe_dir / sub if sub else exe_dir
    assert info["version"] == "7.0-bundled"


@POSIX_ONLY
def test_ffmpeg_env_override_wins(tmp_path, monkeypatch):
    from app import media
    _fake_tool(tmp_path / "x" / "ffmpeg", "ffmpeg")
    _fake_tool(tmp_path / "x" / "ffprobe", "ffprobe")
    monkeypatch.setenv("METAEDIT_FFMPEG", str(tmp_path / "x" / "ffmpeg"))
    monkeypatch.setenv("METAEDIT_FFPROBE", str(tmp_path / "x" / "ffprobe"))
    assert media.ffmpeg_info()["path"] == str(tmp_path / "x" / "ffmpeg")


def test_missing_ffmpeg_message_mentions_windows_install():
    from app import media
    assert re.search(r"winget|ffmpeg\.exe|gyan", media.FFMPEG_MISSING_MSG, re.I), media.FFMPEG_MISSING_MSG


# ------------------------------------------------------------------ regras de caminho Windows
def test_windows_path_rules_with_ntpath(monkeypatch):
    """Executa paths.validate_dirs/is_within com ntpath (semântica Windows) sem tocar o disco."""
    import ntpath
    from app import paths
    key = getattr(paths, "_key", None) or getattr(paths, "path_key", None)
    if key is None:
        pytest.skip("paths não expõe função de chave de comparação")
    monkeypatch.setattr(paths.os, "path", ntpath)
    try:
        assert key(r"C:\Users\Ana\Fotos") == key(r"c:/users/ANA/fotos/")
        assert key(r"\\SERVIDOR\Share\Saída") == key(r"\\servidor\share\saída")
        assert key(r"D:\Fotos") != key(r"C:\Fotos")
    finally:
        monkeypatch.undo()


# ------------------------------------------------------------------- scripts Windows (estático)
BAT = ROOT / "metadata-editor.bat"


def test_bat_launcher_static():
    s = BAT.read_bytes().decode("utf-8", errors="strict")
    low = s.lower()
    assert 'cd /d "%~dp0"' in low, "precisa entrar na pasta do script (funciona com espaços e outro drive)"
    assert "-m app" in low
    assert "pythonw" in low or "pyw" in low, "deve abrir sem console"
    assert "--web" not in low, "launcher padrão não pode iniciar o modo web"
    assert not re.search(r"\bpip install\b.*--user", low)
    # caminhos entre aspas (pastas com espaço)
    for m in re.finditer(r"\.venv\\scripts\\pythonw?\.exe", low):
        before = low[max(0, m.start() - 1)]
        assert before in "\"\\", f"caminho do venv sem aspas: {low[m.start()-20:m.end()+5]}"


def _ps1_files():
    return sorted((ROOT / "packaging").rglob("*.ps1"))


@pytest.mark.parametrize("ps1", _ps1_files() or [None], ids=lambda p: p.name if p else "nenhum")
def test_powershell_scripts_static(ps1):
    if ps1 is None:
        pytest.skip("nenhum .ps1 ainda")
    s = ps1.read_text(encoding="utf-8-sig")
    assert "$ErrorActionPreference" in s and "Stop" in s, f"{ps1.name}: falhas devem interromper"
    assert "PSScriptRoot" in s, f"{ps1.name}: caminhos devem ser relativos ao script"
    assert not re.search(r"Invoke-Expression|iex\s|DownloadString|curl\s+http", s, re.I), \
        f"{ps1.name}: não baixar/executar código remoto"
    assert not re.search(r"Set-ExecutionPolicy\s+\S+\s+-Scope\s+(LocalMachine)", s, re.I)
    # aspas balanceadas por linha (heurística barata de sintaxe)
    for i, line in enumerate(s.splitlines(), 1):
        code = line.split("#", 1)[0]
        assert code.count('"') % 2 == 0, f"{ps1.name}:{i}: aspas desbalanceadas"
    assert s.count("{") == s.count("}"), f"{ps1.name}: chaves desbalanceadas"


def test_pyinstaller_spec_static():
    specs = sorted((ROOT / "packaging").rglob("*.spec"))
    if not specs:
        pytest.skip("sem .spec")
    for sp in specs:
        s = sp.read_text(encoding="utf-8")
        compile(s, str(sp), "exec")  # sintaxe Python válida
        assert re.search(r"console\s*=\s*False", s), f"{sp.name}: exe deve ser windowed (sem console)"
        assert "fastapi" not in s.split("excludes", 1)[0] or "excludes" in s


def test_windows_workflow_is_manual_only():
    wfs = sorted((ROOT / ".github" / "workflows").glob("*.y*ml")) if (ROOT / ".github").exists() else []
    if not wfs:
        pytest.skip("sem workflow")
    for wf in wfs:
        s = wf.read_text(encoding="utf-8")
        on = re.search(r"^on:\s*\n((?:[ \t]+.*\n)+)", s, re.M)
        trig = on.group(1) if on else s
        assert "workflow_dispatch" in trig
        for bad in ("push:", "pull_request", "schedule:", "release:"):
            assert bad not in trig, f"{wf.name}: gatilho automático '{bad}' proibido"
        assert "windows" in s.lower()
        assert not re.search(r"secrets\.|gh release|softprops/action-gh-release|twine|upload-release", s), \
            f"{wf.name}: não publicar nada"


# ------------------------------------------------------------- desktop com win32 simulado (X)
_DISP = os.environ.get("METAEDIT_TEST_DISPLAY") or os.environ.get("DISPLAY")


@pytest.mark.skipif(not _DISP, reason="sem display X")
def test_desktop_win32_simulation():
    """Roda tests/win_sim_desktop.py em processo separado (ele altera globais/ctypes):
    display_path C:/→C:\\ e UNC, %APPDATA%, DPI-aware + AppUserModelID com ctypes.windll falso,
    cancelamento do askdirectory devolvendo (), roda do mouse com delta ±120, picker congelado."""
    env = dict(os.environ, DISP=_DISP)
    p = subprocess.run([sys.executable, str(ROOT / "tests" / "win_sim_desktop.py")], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and "FAIL" not in p.stdout, p.stdout + p.stderr


def test_pe_subsystem_parser_used_by_windows_tests(tmp_path):
    """Valida no Linux o leitor de subsistema PE usado em test_windows_native (exe sem console)."""
    import glob
    import importlib.util
    exes = glob.glob(str(ROOT / ".venv" / "lib" / "python*" / "site-packages" / "setuptools" / "cli-64.exe"))
    if not exes:
        pytest.skip("sem PE de referência")
    spec = importlib.util.spec_from_file_location("wn", ROOT / "tests" / "test_windows_native.py")
    wn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wn)
    src = Path(exes[0])
    assert wn._pe_subsystem(src) == 3  # console (confirmado por `file`)
    b = bytearray(src.read_bytes())
    pe = int.from_bytes(b[0x3C:0x40], "little")
    b[pe + 24 + 68] = 2
    gui = tmp_path / "gui.exe"
    gui.write_bytes(bytes(b))
    assert wn._pe_subsystem(gui) == 2
    if shutil.which("file"):
        assert "(GUI)" in subprocess.run(["file", str(gui)], capture_output=True, text=True).stdout
