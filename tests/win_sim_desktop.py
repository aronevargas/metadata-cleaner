"""Simula win32 nas rotas de app.desktop/app.picker (Linux + X). NÃO é teste Windows real. Origem: frontend; rodado por tests/test_windows_compat.py."""
import ntpath
import os
import sys
import types

os.environ["DISPLAY"] = os.environ.get("DISP", ":97"); os.environ.pop("WAYLAND_DISPLAY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tkinter as tk

from app.desktop import platform as P

ok = True
def check(name, cond, info=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info else ""))

# 1) display_path / config_dir como no Windows
P.IS_WINDOWS = True
orig = P.os.path
P.os.path = ntpath
check("display_path C:/ -> C:\\", P.display_path("C:/Users/João Silva/Fotos de férias") == r"C:\Users\João Silva\Fotos de férias",
      P.display_path("C:/Users/João Silva/Fotos de férias"))
check("display_path UNC", P.display_path("//servidor/compart/pasta x") == r"\\servidor\compart\pasta x",
      P.display_path("//servidor/compart/pasta x"))
P.os.path = orig
os.environ["APPDATA"] = r"C:\Users\Zoë\AppData\Roaming"
check("config_dir %APPDATA%", str(P.config_dir()).endswith("metadata-editor") and "Roaming" in str(P.config_dir()),
      str(P.config_dir()))
kw = P.subprocess_kwargs()
P.IS_WINDOWS = False
check("subprocess_kwargs tem stdin DEVNULL", "stdin" in kw, str(kw))

# 2) prepare_process com ctypes.windll falso (sem exceções, chamadas feitas)
calls = []
fake = types.SimpleNamespace(
    shcore=types.SimpleNamespace(SetProcessDpiAwareness=lambda v: calls.append(("dpi", v))),
    user32=types.SimpleNamespace(SetProcessDPIAware=lambda: calls.append(("dpi-old",))),
    shell32=types.SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=lambda s: calls.append(("aumid", s))))
import ctypes
ctypes.windll = fake
P.IS_WINDOWS = True
P.prepare_process()
P.IS_WINDOWS = False
check("prepare_process: DPI + AppUserModelID", ("dpi", 1) in calls and ("aumid", P.APP_USER_MODEL_ID) in calls, str(calls))
del ctypes.windll

# 3) janela: diálogo devolvendo 'C:/...' vira caminho Windows; roda do mouse com delta ±120; ícone
from app.desktop import window as W
r = tk.Tk(); app = W.MetadataEditorApp(r)
r.geometry("820x560")
for _ in range(20): r.update(); r.after(10)
W.display_path = lambda p: ntpath.normpath(p)  # como no win32
app.ask_directory = lambda **kw: "C:/Users/João/Mídias com espaço"
app.pick_input()
check("campo recebe caminho com \\", app.input_var.get() == r"C:\Users\João\Mídias com espaço", app.input_var.get())
app.ask_directory = lambda **kw: ()  # Tk devolve () ao cancelar em algumas versões
app.pick_input()
check("cancelamento mantém valor", app.input_var.get() == r"C:\Users\João\Mídias com espaço")
check("ícone definido", len(getattr(app, "_icons", [])) == 3)
lf = app.left
lf.canvas.yview_moveto(0)
r.update()
x = lf.inner.winfo_rootx() + 20; y = lf.inner.winfo_rooty() + 40
ev = types.SimpleNamespace(widget=app.status_var and r, x_root=x, y_root=y, delta=-120, num=0)  # foco em outro widget
before = lf.canvas.yview()[0]
lf._wheel(ev)
r.update()
after = lf.canvas.yview()[0]
check("MouseWheel delta -120 rola a coluna (widget sob o ponteiro)", after > before, f"{before}->{after}")
ev.delta = 240
lf._wheel(ev); r.update()
check("MouseWheel delta +240 volta", lf.canvas.yview()[0] < after, str(lf.canvas.yview()))
app.destroy()

# 4) picker legado congelado (PyInstaller): sem subprocess 'exe -c'
import app.picker as pk
sys.frozen = True
saved = pk.shutil.which
pk.shutil.which = lambda n: None
check("picker congelado sem zenity -> None (não relança o exe)", pk.method() is None, str(pk.method()))
del sys.frozen
check("picker normal -> tkinter", pk.method() == "tkinter", str(pk.method()))
pk.shutil.which = saved
print("OK" if ok else "FALHOU")
sys.exit(0 if ok else 1)
