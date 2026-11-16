"""Testes que SÓ rodam num Windows real (runner windows-latest ou máquina do usuário).

No Linux são pulados — o TEST-REPORT não conta estes como evidência até rodarem no Windows.
Rodar no Windows:
    .venv\\Scripts\\python.exe -m pytest -q tests\\test_windows_native.py -rs
Cobertura: caminhos de drive/UNC (\\\\localhost\\C$ admin share, se acessível)/espaços/Unicode,
comparação case-insensitive (NTFS), gravação sem sobrescrita em NTFS, hardlink e fallback,
cancelamento matando ffmpeg.exe (sem processo órfão nem temporário), ausência de console
(CREATE_NO_WINDOW), abertura do Explorer (os.startfile), Tk + diálogo nativo existente,
launcher .bat abrindo pythonw (sem console) e subsistema GUI do .exe gerado.
"""
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import FFMPEG, ROOT, make_jpeg, make_mp4, make_png, sha256, tree_hashes

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="somente Windows real")


def _job(inp, out, **kw):
    from app import services
    return services.wait_job(services.start_job(str(inp), str(out), **kw), timeout=240)


def test_unicode_spaces_and_no_overwrite_on_ntfs(tmp_path):
    inp = tmp_path / "Fotos de férias ção 日本"
    out = tmp_path / "Saída com espaço"
    inp.mkdir()
    out.mkdir()
    make_jpeg(inp / "Foto 1.JPG")
    make_png(inp / "imagem ü.png")
    (out / "1.jpg").write_bytes(b"ja existia")
    (out / "2.txt").write_bytes(b"nao mexer")
    before = tree_hashes(inp)
    j = _job(inp, out)
    assert j["status"] == "done", j
    names = sorted(p.name for p in out.iterdir())
    assert names == ["1.jpg", "2.txt", "3.jpg", "4.png"], names
    assert (out / "1.jpg").read_bytes() == b"ja existia"
    assert tree_hashes(inp) == before
    assert not [p for p in out.iterdir() if p.name.startswith(".metaedit-tmp")]


def test_case_insensitive_same_folder_rejected(tmp_path):
    from app import services
    inp = tmp_path / "Fotos"
    inp.mkdir()
    make_jpeg(inp / "a.jpg")
    with pytest.raises(services.ServiceError):
        services.start_job(str(inp), str(tmp_path / "FOTOS"))
    with pytest.raises(services.ServiceError):  # entrada dentro da saída, caixa diferente
        services.start_job(str(inp), str(tmp_path).upper())
    # barras normais (como o diálogo do Tk devolve) também são aceitas
    j = _job(str(inp).replace("\\", "/"), str(tmp_path / "out").replace("\\", "/"))
    assert j["status"] == "done"


def test_drive_letter_lowercase_and_trailing_sep(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    make_jpeg(inp / "a.jpg")
    drive, rest = os.path.splitdrive(str(inp))
    j = _job(drive.lower() + rest + "\\", str(tmp_path / "out") + "\\")
    assert j["status"] == "done" and (tmp_path / "out" / "1.jpg").exists()


def test_unc_admin_share_if_available(tmp_path):
    drive, rest = os.path.splitdrive(str(tmp_path))
    if not drive.endswith(":"):
        pytest.skip("tmp não está numa letra de drive")
    unc = f"\\\\localhost\\{drive[0]}${rest}"
    if not os.path.isdir(unc):
        pytest.skip(f"compartilhamento administrativo indisponível: {unc}")
    inp = Path(unc) / "unc in"
    inp.mkdir()
    make_jpeg(inp / "a.jpg")
    j = _job(inp, Path(unc) / "unc out")
    assert j["status"] == "done", j
    assert sha256(tmp_path / "unc out" / "1.jpg") != sha256(tmp_path / "unc in" / "a.jpg")


def test_publish_hardlink_and_fallback_binary(tmp_path, monkeypatch):
    from app import paths
    payload = bytes(range(256)) * 32 + b"\n\r\n\x1a"
    for force_fallback in (False, True):
        out = tmp_path / f"o{force_fallback}"
        out.mkdir()
        tmp = out / ".metaedit-tmp-x.bin"
        tmp.write_bytes(payload)
        if force_fallback:
            monkeypatch.setattr(paths.os, "link", lambda *a: (_ for _ in ()).throw(OSError(1, "no link")))
        name, _ = paths.publish_exclusive(tmp, out, 1, ".bin")
        assert (out / name).read_bytes() == payload, f"fallback={force_fallback}: bytes alterados (O_BINARY?)"
        monkeypatch.undo()


def _ffmpeg_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return {line.split(",")[1].strip('"') for line in out.splitlines() if line.startswith('"ffmpeg')}


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg ausente")
def test_cancel_kills_ffmpeg_exe_and_removes_temp(tmp_path):
    from app import services
    inp, out = tmp_path / "longo ç", tmp_path / "out"
    inp.mkdir()
    make_mp4(inp / "long.mp4", seconds=60)
    make_jpeg(inp / "z.jpg")
    before = _ffmpeg_pids()
    jid = services.start_job(str(inp), str(out))
    deadline = time.time() + 60
    while time.time() < deadline and not (_ffmpeg_pids() - before):
        time.sleep(0.2)
    assert _ffmpeg_pids() - before, "ffmpeg.exe não iniciou"
    services.cancel_job(jid)
    j = services.wait_job(jid, timeout=30)
    assert j["status"] == "cancelled"
    time.sleep(1)
    assert not (_ffmpeg_pids() - before), "ffmpeg.exe órfão após cancelar"
    assert not list(out.iterdir()) if out.exists() else True


def test_subprocesses_have_no_console_flag(monkeypatch):
    from app import media, proc
    assert proc.subprocess_kwargs()["creationflags"] & subprocess.CREATE_NO_WINDOW
    seen = []
    real = subprocess.run

    def spy(*a, **k):
        seen.append(k.get("creationflags", 0))
        return real(*a, **k)
    monkeypatch.setattr(media.subprocess, "run", spy)
    media.ffmpeg_info()
    if seen:
        assert all(f & subprocess.CREATE_NO_WINDOW for f in seen)


def test_open_folder_uses_startfile(tmp_path, monkeypatch):
    from app import services
    called = []
    monkeypatch.setattr(os, "startfile", lambda p, *a: called.append(p))
    services.open_folder(str(tmp_path))
    assert called and os.path.normcase(called[0]) == os.path.normcase(os.path.realpath(tmp_path))


def test_tk_and_native_dialog_available():
    import tkinter as tk
    root = tk.Tk()
    try:
        root.withdraw()
        assert root.tk.call("info", "commands", "tk_chooseDirectory")
        assert root.tk.call("tk", "windowingsystem") == "win32"
    finally:
        root.destroy()


def test_bat_launcher_opens_pythonw_without_console(tmp_path):
    if not (ROOT / ".venv" / "Scripts" / "pythonw.exe").exists():
        pytest.skip(".venv ausente (rode packaging\\windows\\install.bat)")
    env = dict(os.environ, APPDATA=str(tmp_path))
    subprocess.run(["cmd", "/c", str(ROOT / "metadata-editor.bat"), "--no-restore"], cwd=tmp_path, env=env,
                   timeout=30, check=True)
    try:
        deadline = time.time() + 20
        found = ""
        while time.time() < deadline:
            found = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
                 "ForEach-Object { $_.CommandLine + ' ' + $_.ProcessId }"],  # wmic foi removido no Win11 24H2
                capture_output=True, text=True).stdout
            if "-m app" in found:
                break
            time.sleep(0.5)
        assert "-m app" in found, found
        assert "--web" not in found
    finally:
        for line in found.splitlines():
            if "-m app" in line and "--no-restore" in line:
                pid = line.split()[-1]
                subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)


def _pe_subsystem(exe: Path) -> int:
    b = exe.read_bytes()[:4096]
    pe = struct.unpack_from("<I", b, 0x3C)[0]
    assert b[pe:pe + 4] == b"PE\0\0"
    opt = pe + 24
    return struct.unpack_from("<H", b, opt + 68)[0]


def test_built_exe_is_gui_subsystem():
    exe = ROOT / "dist" / "metadata-editor" / "metadata-editor.exe"
    if not exe.exists():
        pytest.skip("exe não gerado (packaging\\windows\\build-exe.ps1)")
    assert _pe_subsystem(exe) == 2, "IMAGE_SUBSYSTEM_WINDOWS_GUI esperado (sem console)"
