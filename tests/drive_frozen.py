"""Dirige o executável PyInstaller (ou qualquer instância da janela) via Tk `send` no X11.

Uso: tests/drive_frozen.py [--launch CAMINHO_DO_EXECUTAVEL]
Cria fixtures (JPG com EXIF/GPS, PNG com tEXt, MP4 com tags, JPG corrompido), preenche as pastas,
clica Listar/Iniciar como o usuário faria e verifica a saída. Depois testa o cancelamento com um vídeo longo.
Requer X11 (DISPLAY) com `send` habilitado (xhost com controle de acesso ativo) e ffmpeg.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from conftest import ffprobe, make_jpeg, make_mp4, make_png, sha256, tree_hashes  # noqa: E402
from PIL import Image  # noqa: E402

APP = "metadata-editor"
r = tk.Tk()
r.withdraw()


def S(*a):
    return r.tk.call("send", APP, r.tk.call("list", *a))  # list: quoting seguro (espaços, Unicode)


def find(cls, text=None):
    out = []

    def walk(w):
        c = S("winfo", "class", w)
        if c == cls:
            t = None
            try:
                t = S(w, "cget", "-text")
            except tk.TclError:
                pass
            if text is None or t == text:
                out.append(w)
        for ch in r.tk.splitlist(S("winfo", "children", w)):
            walk(ch)
    walk(".")
    return out


def var_of(entry):
    return S(entry, "cget", "-textvariable")


def wait(cond, timeout, what):
    t = time.time() + timeout
    while time.time() < t:
        if cond():
            return
        time.sleep(0.2)
    raise SystemExit(f"FALHA: timeout aguardando {what}")


def main():
    proc = None
    if "--launch" in sys.argv:
        exe = sys.argv[sys.argv.index("--launch") + 1]
        home = tempfile.mkdtemp()
        proc = subprocess.Popen([exe, "--no-restore"], env=dict(os.environ, HOME=home))
        wait(lambda: APP in r.tk.splitlist(r.tk.call("winfo", "interps")), 30, "janela")
    try:
        run()
    finally:
        if proc:
            proc.terminate()
            proc.wait(15)


def run():
    tmp = Path(tempfile.mkdtemp(prefix="frozen-"))
    inp, out = tmp / "entrada ç", tmp / "saida"
    inp.mkdir()
    make_jpeg(inp / "a.jpg")
    make_png(inp / "b.png")
    make_mp4(inp / "c.mp4", seconds=1)
    (inp / "d.jpg").write_bytes(b"\xff\xd8corrompido")
    before = tree_hashes(inp)

    entries = [e for e in find("TEntry") if str(var_of(e))]
    S("set", var_of(entries[0]), str(inp))
    S("set", var_of(entries[1]), str(out))
    S(find("TButton", "Listar arquivos")[0], "invoke")
    trees = find("Treeview")
    wait(lambda: len(r.tk.splitlist(S(trees[0], "children", ""))) >= 4, 30, "listagem")
    start = find("TButton", "Iniciar transformação")[0]
    S(start, "invoke")
    pb = find("TProgressbar")[0]
    wait(lambda: out.exists() and len(list(out.iterdir())) >= 3
         and float(S(pb, "cget", "-value")) >= float(S(pb, "cget", "-maximum")), 120, "fim do processamento")
    print("estado do botão Iniciar ao fim:", S(start, "state"), "| cancelar:", S(find("TButton", "Cancelar")[0], "state"))
    time.sleep(1)
    names = sorted(p.name for p in out.iterdir())
    print("saídas:", names, "progresso:", S(pb, "cget", "-value"), "/", S(pb, "cget", "-maximum"))
    assert names == ["1.jpg", "2.png", "3.mp4"], names
    assert tree_hashes(inp) == before, "originais alterados!"
    assert not Image.open(out / "1.jpg").getexif(), "EXIF remanescente"
    assert not {k for k in Image.open(out / "2.png").info if k not in ("dpi", "icc_profile")}, Image.open(out / "2.png").info
    tags = ffprobe(out / "3.mp4")["format"].get("tags", {})
    assert not {"title", "comment", "artist", "location"} & {k.lower() for k in tags}, tags
    assert sha256(out / "1.jpg") != sha256(inp / "a.jpg")
    # erro por arquivo visível na tabela de resultados
    res = trees[-1]
    rows = [S(res, "item", i, "-values") for i in r.tk.splitlist(S(res, "children", ""))]
    print("linhas de resultado:", rows)
    assert any("d.jpg" in str(v) for v in rows)

    # cancelamento: vídeo longo
    inp2, out2 = tmp / "longo", tmp / "saida2"
    inp2.mkdir()
    make_mp4(inp2 / "long.mp4", seconds=40)
    make_jpeg(inp2 / "z.jpg")
    S("set", var_of(entries[0]), str(inp2))
    S("set", var_of(entries[1]), str(out2))
    S(find("TButton", "Listar arquivos")[0], "invoke")
    # após um job o Iniciar fica desabilitado até listar de novo (numeração atualizada) — por design
    wait(lambda: str(S(start, "instate", "disabled")) == "0", 30, "Iniciar habilitado após nova listagem")
    S(start, "invoke")
    cancel = find("TButton", "Cancelar")[0]
    wait(lambda: str(S(cancel, "instate", "disabled")) == "0", 15, "Cancelar habilitado")
    wait(lambda: "ffmpeg" in subprocess.run(["pgrep", "-af", "long.mp4"], capture_output=True, text=True).stdout,
         30, "ffmpeg em execução")
    S(cancel, "invoke")
    wait(lambda: str(S(cancel, "instate", "disabled")) == "1" and "ffmpeg" not in subprocess.run(
        ["pgrep", "-af", str(inp2 / "long.mp4")], capture_output=True, text=True).stdout, 30, "cancelamento")
    def rows2_now():
        return [str(S(res, "item", i, "-values")) for i in r.tk.splitlist(S(res, "children", ""))]
    wait(lambda: not any("Processando" in x or "Aguardando" in x for x in rows2_now()), 15,
         "tabela refletir o cancelamento")
    rows2 = rows2_now()
    print("resultado após cancelar:", rows2)
    time.sleep(1)
    left = sorted(p.name for p in out2.iterdir()) if out2.exists() else []
    print("após cancelar:", left)
    assert not [n for n in left if not n.split(".")[0].isdigit()], f"temporários sobraram: {left}"
    ff = subprocess.run(["pgrep", "-af", str(inp2 / "long.mp4")], capture_output=True, text=True).stdout
    assert "ffmpeg" not in ff, ff
    print(json.dumps({"ok": True, "tmp": str(tmp)}))


if __name__ == "__main__":
    main()
