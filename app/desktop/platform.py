"""Ajustes por sistema operacional para a interface desktop (Windows/macOS/Linux).

Nada aqui depende de bibliotecas externas; no Windows usa `ctypes` apenas quando disponível,
e toda chamada é protegida para nunca impedir a janela de abrir.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "metadata-editor"
APP_USER_MODEL_ID = "MetadataEditor.Desktop"
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def prepare_process() -> None:
    """Chamar ANTES de criar `tk.Tk()`.

    Windows: declara o processo como DPI-aware (sem isso o Windows estica a janela inteira em telas
    125%/150% e tudo fica borrado) e define um AppUserModelID para a barra de tarefas mostrar o ícone
    do aplicativo em vez do ícone do Python.
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE (Win 8.1+)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()  # Vista/7
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001
        pass


def config_dir() -> Path:
    """Pasta de configuração por usuário (Windows: %APPDATA%\\metadata-editor)."""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        return Path(base) / APP_DIR_NAME if base else Path.home() / "AppData" / "Roaming" / APP_DIR_NAME
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / APP_DIR_NAME


def display_path(path: str) -> str:
    """Caminho no formato do sistema. O diálogo do Tk no Windows devolve 'C:/Users/...';
    convertemos para 'C:\\Users\\...' (o que o usuário espera ver e colar no Explorer)."""
    if not path:
        return ""
    path = str(path)
    if IS_WINDOWS:
        return os.path.normpath(path)
    return path


def ui_scale(root) -> float:
    """Fator de escala de pixels (1.0 = 96 DPI). Fontes em pontos já escalam sozinhas; medidas fixas
    em pixels (larguras, espaçamentos, altura de linhas) devem ser multiplicadas por este fator."""
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:  # noqa: BLE001
        return 1.0
    s = dpi / 96.0
    if s < 1.05:  # X11/Xwayland reportam ~95,9 DPI: trate como 1.0 para não alterar o layout
        return 1.0
    return min(s, 3.0)


def subprocess_kwargs() -> dict:
    """Argumentos extras para subprocessos: sem janela de console no Windows (app --windowed)."""
    try:
        from ..proc import subprocess_kwargs as _kw  # helper do backend, quando existir

        return dict(_kw())
    except Exception:  # noqa: BLE001
        pass
    kw: dict = {"stdin": __import__("subprocess").DEVNULL}
    if IS_WINDOWS:
        kw["creationflags"] = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0x08000000)
    return kw
