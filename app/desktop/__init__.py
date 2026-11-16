"""Interface desktop nativa (Tkinter/ttk) do Editor de Metadados."""
from __future__ import annotations

import sys
import tkinter as tk


def create_app(root: tk.Tk | None = None, *, restore_state: bool = False):
    """Cria a janela sem entrar no mainloop (útil para testes)."""
    from .window import MetadataEditorApp

    if root is None:
        from .platform import prepare_process

        prepare_process()
        root = tk.Tk(className="metadata-editor")
    return MetadataEditorApp(root, restore_state=restore_state)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from .platform import prepare_process

    prepare_process()  # Windows: DPI-aware + ícone próprio na barra de tarefas (antes do Tk)
    try:
        root = tk.Tk(className="metadata-editor")
    except tk.TclError as e:
        print("Não foi possível abrir a janela (sem ambiente gráfico?): " + str(e), file=sys.stderr)
        return 2
    app = create_app(root, restore_state="--no-restore" not in argv)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        app.destroy()
    return 0


__all__ = ["create_app", "main"]
