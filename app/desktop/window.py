"""Janela principal (Tkinter/ttk) do Editor de Metadados.

Toda a interação com o processamento passa por `app.services` (in-process, sem HTTP).
Somente a thread principal toca em widgets: a listagem roda numa thread e devolve o
resultado por uma fila; o progresso do job é obtido por polling com `root.after`.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import services
from . import theme as T
from .platform import config_dir, display_path, subprocess_kwargs

POLL_MS = 200
STATUS_LABELS = {
    "pending": "Aguardando",
    "processing": "Processando…",
    "done": "Concluído",
    "error": "Falhou",
    "skipped": "Não processado",
}
STRENGTH_LABELS = {1: "Sutil", 2: "Moderada", 3: "Maior"}
META_LABELS = (("title", "Título"), ("artist", "Autor"), ("copyright", "Direitos autorais"),
               ("software", "Software"), ("comment", "Comentário"))
STATE_FILE = config_dir() / "desktop.json"  # Windows: %APPDATA%\metadata-editor\desktop.json


def format_size(n) -> str:
    if not isinstance(n, (int, float)):
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    v = float(n)
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    s = f"{v:.0f}" if i == 0 else f"{v:.1f}"
    return f"{s.replace('.', ',')} {units[i]}"


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def native_ask_directory(*, parent: tk.Misc, title: str, initialdir: str | None = None, mustexist: bool = False) -> str:
    """Seletor de pastas do sistema.

    Windows/macOS: o diálogo do Tk já é o nativo. Linux: usa zenity (GTK) ou kdialog (KDE) se existirem,
    mantendo a janela responsiva enquanto o diálogo está aberto; senão, o diálogo do Tk.
    Retorna '' se o usuário cancelar.
    """
    cmd = None
    if sys.platform.startswith("linux") and os.environ.get("METAEDIT_TK_PICKER") != "1":
        start = (initialdir or str(Path.home())).rstrip("/") + "/"
        if shutil.which("zenity"):
            cmd = ["zenity", "--file-selection", "--directory", f"--title={title}", f"--filename={start}"]
        elif shutil.which("kdialog"):
            cmd = ["kdialog", "--getexistingdirectory", start, "--title", title]
    if cmd:
        try:
            kw = subprocess_kwargs()
            for k in ("stdout", "stderr", "capture_output", "text", "encoding", "errors"):
                kw.pop(k, None)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                    encoding="utf-8", errors="replace", **kw)
        except OSError:
            proc = None
        if proc is not None:
            while proc.poll() is None:  # mantém a janela redesenhando
                try:
                    parent.update()
                except tk.TclError:
                    proc.kill()
                    return ""
                time.sleep(0.03)
            out = (proc.stdout.read() if proc.stdout else "").strip()
            return out if proc.returncode == 0 else ""
    # Windows: diálogo nativo do Explorer (IFileDialog) · macOS: NSOpenPanel · Linux sem zenity: diálogo Tk
    got = filedialog.askdirectory(parent=parent, title=title, initialdir=initialdir or None, mustexist=mustexist)
    if not got or not isinstance(got, str):  # cancelado: '' ou () conforme a versão do Tk
        return ""
    return display_path(got)


class ScrollFrame(ttk.Frame):
    """Coluna rolável verticalmente (para janelas baixas)."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, background=T.BG, highlightthickness=0, borderwidth=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.inner.bind_all(seq, self._wheel, add="+")

    def _on_scroll(self, lo, hi):
        self.vbar.set(lo, hi)
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self.vbar.grid_remove()
        else:
            self.vbar.grid()

    def _wheel(self, e):
        # rola só se o ponteiro estiver sobre esta coluna (no Windows o evento pode chegar ao widget com
        # foco, não ao que está sob o ponteiro: localiza pelo ponto da tela)
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except (tk.TclError, KeyError):
            w = None
        if w is None:
            w = e.widget if isinstance(e.widget, tk.Misc) else None
        while w is not None:
            if w is self:
                break
            w = getattr(w, "master", None)
        if w is None:
            return
        top, bottom = self.canvas.yview()
        if top <= 0 and bottom >= 1:
            return
        d = getattr(e, "delta", 0) or 0
        if getattr(e, "num", 0) == 4:
            steps = -3
        elif getattr(e, "num", 0) == 5:
            steps = 3
        elif abs(d) >= 120:  # Windows: múltiplos de ±120 por “clique” da roda
            steps = -int(d / 120) * 3
        elif d:  # macOS: deltas pequenos
            steps = -1 if d > 0 else 1
        else:
            return
        self.canvas.yview_scroll(steps, "units")


class Banner(tk.Frame):
    """Aviso inline (erro/atenção), com botão fechar acessível por teclado."""

    def __init__(self, master, kind: str, title: str, variable: tk.StringVar, font=None):
        bg, fg, border = {"error": (T.ERR_BG, T.ERR_TEXT, T.ERR), "warn": (T.WARN_BG, T.WARN, T.WARN)}[kind]
        super().__init__(master, background=bg, highlightbackground=border, highlightthickness=1)
        self.title_label = tk.Label(self, text=title, background=bg, foreground=fg, anchor="w",
                                    font=font or "TkHeadingFont")
        self.msg = tk.Label(self, textvariable=variable, background=bg, foreground=T.TEXT, anchor="w",
                            justify="left", wraplength=600)
        self.close = ttk.Button(self, text="✕", width=3, style="Close.TButton", command=self.hide)
        self.title_label.grid(row=0, column=0, sticky="w", padx=(12, 8), pady=(8, 0))
        self.msg.grid(row=1, column=0, sticky="we", padx=(12, 8), pady=(2, 10))
        self.close.grid(row=0, column=1, rowspan=2, sticky="ne", padx=6, pady=6)
        self.columnconfigure(0, weight=1)
        self.bind("<Configure>", lambda e: self.msg.configure(wraplength=max(200, e.width - 70)))
        self.variable = variable
        self.visible = False

    def show(self):
        self.visible = True
        self.grid()
        self.master.grid()

    def hide(self):
        self.visible = False
        self.variable.set("")
        self.grid_remove()
        # um frame sem filhos visíveis mantém a última altura: oculta o contêiner também
        if not any(getattr(c, "visible", False) for c in self.master.winfo_children()):
            self.master.grid_remove()


class MetadataEditorApp:
    def __init__(self, root: tk.Tk, *, restore_state: bool = False):
        self.root = root
        self.fonts = T.apply(root)
        self.scale = getattr(self.fonts, "scale", 1.0)
        root.title("Editor de Metadados")
        self._set_icon()
        # tamanho inicial/mínimo escalados por DPI, mas nunca maiores que a tela (notebook 1366x768 a 125%)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w0, h0 = min(self.px(1120), int(sw * 0.94)), min(self.px(760), int(sh * 0.88))
        root.geometry(f"{w0}x{h0}")
        root.minsize(min(self.px(820), w0), min(self.px(560), h0))

        # ---- estado público (usado também pelos testes) ----
        self.ask_directory = native_ask_directory
        self.input_var = tk.StringVar(root)
        self.output_var = tk.StringVar(root)
        self.recursive_var = tk.BooleanVar(root, value=False)
        self.mode_var = tk.StringVar(root, value="strip")
        self.meta_vars = {k: tk.StringVar(root) for k, _ in META_LABELS}
        self.noise_var = tk.BooleanVar(root, value=True)
        self.strength_var = tk.IntVar(root, value=1)
        self.error_var = tk.StringVar(root)
        self.warn_var = tk.StringVar(root)
        self.status_var = tk.StringVar(root, value="Pronto.")
        self.counts_var = tk.StringVar(root)
        self.progress_text_var = tk.StringVar(root, value="Nenhum processamento iniciado.")
        self.progress_count_var = tk.StringVar(root, value="")
        self.current_var = tk.StringVar(root)
        self.start_hint_var = tk.StringVar(root, value="Liste os arquivos primeiro.")
        self.health: dict | None = None
        self.last_scan: dict | None = None
        self._scan_key = None
        self.job_id: str | None = None
        self.last_job: dict | None = None
        self.busy = False          # listagem em andamento
        self.job_running = False
        self._scan_q: "queue.Queue[tuple]" = queue.Queue()
        self._poll_after = None
        self._result_rows: dict[int, tuple] = {}

        self._left_wrap: list[ttk.Label] = []  # dicas da coluna esquerda (quebra acompanha a largura)
        self._build()
        self._bind_keys()
        for v in (self.input_var, self.output_var, self.recursive_var):
            v.trace_add("write", lambda *_: self._invalidate_scan())
        self.mode_var.trace_add("write", lambda *_: self._sync_options())
        self.noise_var.trace_add("write", lambda *_: self._sync_options())
        self.strength_var.trace_add("write", lambda *_: self._sync_options())
        self._restore_state = restore_state
        if restore_state:
            self._load_state()
        self._sync_options()
        self._update_start_state()
        self._render_files()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(50, self._load_health)

    # ================================================================== construção
    def px(self, n: float) -> int:
        """Pixels lógicos (96 DPI) -> pixels reais na escala da tela."""
        return int(round(n * self.scale))

    def _set_icon(self):
        """Ícone da janela/barra de tarefas (logo “M” azul/violeta) desenhado em memória."""
        try:
            imgs = [self._make_icon(n) for n in (16, 32, 64)]
            self.root.iconphoto(True, *imgs)
            self._icons = imgs  # mantém referência (senão o Tk descarta a imagem)
        except tk.TclError:
            pass

    def _make_icon(self, n: int) -> tk.PhotoImage:
        img = tk.PhotoImage(master=self.root, width=n, height=n)
        rows = []
        for y in range(n):
            row = []
            for x in range(n):
                row.append(T.ACCENT if x + y > n and (x + y) % 2 == 0 else T.BRAND)
            rows.append("{" + " ".join(row) + "}")
        img.put(" ".join(rows))
        # “M” branco: pontos (proporcionais ao logo do cabeçalho) ligados por segmentos
        pts = [(11, 28), (11, 12), (20, 21), (29, 12), (29, 28)]
        pts = [(x * n / 40, y * n / 40) for x, y in pts]
        th = max(1, round(n / 13))
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
            for i in range(steps + 1):
                cx = x0 + (x1 - x0) * i / steps
                cy = y0 + (y1 - y0) * i / steps
                xa, ya = int(cx - th / 2), int(cy - th / 2)
                img.put("white", to=(max(0, xa), max(0, ya), min(n, xa + th), min(n, ya + th)))
        return img

    def _card(self, parent, title: str | None = None, step: str | None = None) -> ttk.Frame:
        card = ttk.Frame(parent, style="Card.TFrame", padding=self.px(16))
        if title:
            head = ttk.Frame(card, style="Inner.TFrame")
            head.pack(fill="x", pady=(0, 10))
            if step:
                tk.Label(head, text=step, width=2, background=T.BRAND_DIM, foreground=T.BRAND_TEXT,
                         font=self.fonts.bold, padx=4).pack(side="left", padx=(0, 10))
            ttk.Label(head, text=title, style="H2.TLabel").pack(side="left")
            card.head = head
        return card

    def _build(self):
        r = self.root
        r.columnconfigure(0, weight=1)
        r.rowconfigure(2, weight=1)

        # ---- cabeçalho ----
        header = ttk.Frame(r, padding=(20, 14, 20, 10))
        header.grid(row=0, column=0, sticky="we")
        header.columnconfigure(1, weight=1)
        L = self.px
        logo = tk.Canvas(header, width=L(40), height=L(40), background=T.BG, highlightthickness=0)
        logo.create_rectangle(0, 0, L(40), L(40), fill=T.BRAND, outline="")
        logo.create_polygon(L(20), 0, L(40), 0, L(40), L(40), 0, L(40), fill=T.ACCENT, outline="", stipple="gray50")
        logo.create_line(*(L(v) for v in (11, 28, 11, 12, 20, 21, 29, 12, 29, 28)), fill="white", width=L(3),
                         capstyle="round", joinstyle="round")
        logo.grid(row=0, column=0, rowspan=2, padx=(0, 12))
        ttk.Label(header, text="Editor de Metadados", style="H1.TLabel").grid(row=0, column=1, sticky="w")
        self.subtitle = ttk.Label(header, text="Cria cópias numeradas de imagens e vídeos com metadados e pixels "
                                               "modificados — tudo no seu computador.", style="Muted.TLabel",
                                  justify="left")
        self.subtitle.grid(row=1, column=1, sticky="w", padx=(0, 12))
        chips = ttk.Frame(header)
        chips.grid(row=0, column=2, rowspan=2, sticky="e")
        self.ffmpeg_chip = tk.Label(chips, text="● Verificando FFmpeg…", background=T.SURFACE_2,
                                    foreground=T.MUTED, font=self.fonts.small, padx=10, pady=3)
        self.ffmpeg_chip.pack(side="right")

        # ---- avisos ----
        banners = ttk.Frame(r, padding=(20, 0))
        banners.grid(row=1, column=0, sticky="we")
        self.banners = banners
        banners.columnconfigure(0, weight=1)
        self.error_frame = Banner(banners, "error", "Algo deu errado", self.error_var, font=self.fonts.bold)
        self.error_frame.grid(row=0, column=0, sticky="we", pady=(0, 8))
        self.error_frame.grid_remove()
        self.warn_frame = Banner(banners, "warn", "Atenção", self.warn_var, font=self.fonts.bold)
        self.warn_frame.grid(row=1, column=0, sticky="we", pady=(0, 8))
        self.warn_frame.grid_remove()
        banners.grid_remove()

        # ---- corpo: coluna esquerda (pastas/opções) + direita (arquivos/progresso) ----
        body = ttk.Frame(r, padding=(20, 0, 20, 12))
        body.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=0, minsize=self.px(320))
        body.columnconfigure(1, weight=1)

        left = ScrollFrame(body, width=self.px(380))
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 16))
        self.left = left
        col = left.inner
        col.columnconfigure(0, weight=1)
        self._build_folders(col).grid(row=0, column=0, sticky="we", pady=(0, 12))
        self._build_options(col).grid(row=1, column=0, sticky="we", pady=(0, 12))
        self._build_notice(col).grid(row=2, column=0, sticky="we")

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self._build_lists(right).grid(row=0, column=0, sticky="nsew", pady=(0, 12))
        self._build_progress(right).grid(row=1, column=0, sticky="we")

        # ---- barra de status ----
        status = ttk.Frame(r, style="Card.TFrame")
        status.grid(row=3, column=0, sticky="we")
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(side="left")
        ttk.Label(status, text="Atalhos: F5 listar · Ctrl+Enter iniciar · Esc cancelar",
                  style="Status.TLabel").pack(side="right")

        self.root.bind("<Configure>", self._on_resize, add="+")

    def _build_folders(self, parent):
        card = self._card(parent, "Pastas", "1")
        self.picker_note = ttk.Label(card, style="CardMuted.TLabel", wraplength=self.px(320), justify="left",
                                     text="Use “Escolher…” para abrir o seletor de pastas do sistema "
                                          "ou digite/cole o caminho completo.")
        self.picker_note.pack(fill="x", pady=(0, 10))

        def row(label, hint, var, cmd, ul_text):
            ttk.Label(card, text=label, style="CardBold.TLabel").pack(anchor="w")
            line = ttk.Frame(card, style="Inner.TFrame")
            line.pack(fill="x", pady=(4, 2))
            entry = ttk.Entry(line, textvariable=var)
            btn = ttk.Button(line, text=ul_text, command=cmd, underline=0)
            btn.pack(side="right", padx=(8, 0))  # botão primeiro: o campo encolhe, o botão nunca é cortado
            entry.pack(side="left", fill="x", expand=True)
            hint_lbl = ttk.Label(card, text=hint, style="CardMuted.TLabel", wraplength=self.px(280),
                                 justify="left")
            hint_lbl.pack(anchor="w", pady=(0, 10))
            self._left_wrap.append(hint_lbl)
            return entry, btn

        self.input_entry, self.pick_input_btn = row(
            "Pasta de entrada (originais)", "Somente leitura: nada nesta pasta é modificado.",
            self.input_var, self.pick_input, "Escolher…")
        self.output_entry, self.pick_output_btn = row(
            "Pasta de saída (cópias)", "Diferente da entrada. É criada se não existir; arquivos "
            "existentes nunca são sobrescritos.", self.output_var, self.pick_output, "Selecionar…")

        bottom = ttk.Frame(card, style="Inner.TFrame")
        bottom.pack(fill="x")
        self.recursive_chk = ttk.Checkbutton(bottom, text="Incluir subpastas", variable=self.recursive_var)
        self.recursive_chk.pack(side="left")
        self.scan_btn = ttk.Button(bottom, text="Listar arquivos", style="Primary.TButton",
                                   command=self.scan, underline=0)
        self.scan_btn.pack(side="right")
        return card

    def _build_options(self, parent):
        card = self._card(parent, "Transformação", "2")
        ttk.Label(card, text="Metadados", style="CardBold.TLabel").pack(anchor="w")
        self.mode_strip = ttk.Radiobutton(card, text="Remover todos (EXIF, GPS, datas, câmera…)",
                                          value="strip", variable=self.mode_var)
        self.mode_strip.pack(anchor="w", pady=(4, 0))
        self.mode_replace = ttk.Radiobutton(card, text="Remover e gravar novos campos",
                                            value="replace", variable=self.mode_var)
        self.mode_replace.pack(anchor="w")
        self.meta_frame = ttk.Frame(card, style="Inner.TFrame", padding=(26, 4, 0, 0))
        self.meta_frame.columnconfigure(1, weight=1)
        self.meta_entries = {}
        for i, (key, label) in enumerate(META_LABELS):
            ttk.Label(self.meta_frame, text=label, style="Card.TLabel").grid(row=i, column=0, sticky="w",
                                                                           padx=(0, 8), pady=2)
            e = ttk.Entry(self.meta_frame, textvariable=self.meta_vars[key])
            e.grid(row=i, column=1, sticky="we", pady=2)
            self.meta_entries[key] = e
        self.meta_hint = ttk.Label(self.meta_frame, text="Só os campos preenchidos são gravados.",
                                   style="CardMuted.TLabel")
        self.meta_hint.grid(row=len(META_LABELS), column=0, columnspan=2, sticky="w", pady=(2, 0))
        self._meta_anchor = ttk.Frame(card, style="Inner.TFrame")
        self._meta_anchor.pack(fill="x")

        ttk.Separator(card).pack(fill="x", pady=12)
        ttk.Label(card, text="Pixels", style="CardBold.TLabel").pack(anchor="w")
        self.noise_chk = ttk.Checkbutton(card, text="Variar pixels discretamente", variable=self.noise_var)
        self.noise_chk.pack(anchor="w", pady=(4, 0))
        self.noise_hint = ttk.Label(card, text="Alteração imperceptível a olho nu, verificada em cada cópia.",
                                    style="CardMuted.TLabel", wraplength=280, justify="left")
        self.noise_hint.pack(anchor="w", padx=(26, 0),
                                                                                  pady=(0, 8))
        ttk.Label(card, text="Intensidade", style="Card.TLabel").pack(anchor="w", padx=(26, 0), pady=(0, 4))
        seg_line = ttk.Frame(card, style="Inner.TFrame", padding=(26, 0, 0, 0))
        seg_line.pack(fill="x")
        self.strength_btns = []
        for v in (1, 2, 3):
            b = ttk.Radiobutton(seg_line, text=STRENGTH_LABELS[v], value=v, variable=self.strength_var,
                                style="Seg.TRadiobutton")
            b.pack(side="left", padx=(0, 4))
            self.strength_btns.append(b)
        videos_hint = ttk.Label(card, text="Vídeos são recodificados; o áudio é copiado sem alteração.",
                                style="CardMuted.TLabel", wraplength=self.px(280), justify="left")
        videos_hint.pack(anchor="w", pady=(8, 0))
        self._left_wrap.append(videos_hint)
        return card

    def _build_notice(self, parent):
        card = self._card(parent)
        self.notice_label = ttk.Label(card, text="As cópias recebem nomes numerados (1.jpg, 2.mp4, …) mantendo a extensão. "
                             "Os originais nunca são alterados. Alterar o conteúdo não garante que serviços "
                             "ou sistemas de reconhecimento tratem as cópias como mídias diferentes.",
                  style="CardMuted.TLabel", wraplength=self.px(320), justify="left")
        self.notice_label.pack(fill="x")
        return card

    def _make_tree(self, parent, columns):
        wrap = ttk.Frame(parent, style="Inner.TFrame")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        tree = ttk.Treeview(wrap, columns=[c[0] for c in columns], show="headings", selectmode="browse")
        for cid, title, width, anchor, stretch in columns:
            tree.heading(cid, text=title, anchor=anchor)
            tree.column(cid, width=self.px(width), minwidth=self.px(40), anchor=anchor, stretch=stretch)
        vs = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="we")
        for tag, color in (("image", T.TEXT_2), ("video", T.ACCENT_TEXT), ("ignored", T.DISABLED),
                           ("done", T.OK), ("error", T.ERR_TEXT), ("pending", T.MUTED),
                           ("processing", T.BRAND_TEXT), ("skipped", T.MUTED)):
            tree.tag_configure(tag, foreground=color)
        tree.tag_configure("odd", background=T.SURFACE)
        tree.tag_configure("even", background="#141824")
        return wrap, tree

    def _build_lists(self, parent):
        card = self._card(parent, "Arquivos", "3")
        ttk.Label(card.head, textvariable=self.counts_var, style="CardMuted.TLabel").pack(side="right")
        nb = ttk.Notebook(card)
        nb.pack(fill="both", expand=True)
        self.notebook = nb

        # aba Arquivos
        files_tab = ttk.Frame(nb, style="Inner.TFrame", padding=(0, 8, 0, 0))
        files_tab.rowconfigure(0, weight=1)
        files_tab.columnconfigure(0, weight=1)
        wrap, self.file_tree = self._make_tree(files_tab, [
            ("n", "#", 48, "e", False), ("name", "Arquivo", 260, "w", True), ("kind", "Tipo", 90, "w", False),
            ("size", "Tamanho", 90, "e", False), ("out", "Saída prevista / motivo", 200, "w", True)])
        wrap.grid(row=0, column=0, sticky="nsew")
        self.empty_label = tk.Label(wrap, background=T.SURFACE, foreground=T.MUTED, justify="center",
                                    font=self.fonts.base, wraplength=self.px(420))
        self.files_note = ttk.Label(files_tab, style="CardMuted.TLabel", justify="left", wraplength=self.px(600),
                                    text="A numeração começa no primeiro número livre da pasta de saída. "
                                         "Se um arquivo falhar, os seguintes avançam (números só são usados "
                                         "por cópias geradas com sucesso).")
        self.files_note.grid(row=1, column=0, sticky="we", pady=(8, 0))
        nb.add(files_tab, text="Arquivos encontrados", underline=0)

        # aba Resultado
        res_tab = ttk.Frame(nb, style="Inner.TFrame", padding=(0, 8, 0, 0))
        res_tab.rowconfigure(1, weight=1)
        res_tab.columnconfigure(0, weight=1)
        stats = ttk.Frame(res_tab, style="Inner.TFrame")
        stats.grid(row=0, column=0, sticky="we", pady=(0, 8))
        self.stat_vars = {}
        for i, (key, label, style) in enumerate((("ok", "cópias criadas", "StatOk.TLabel"),
                                                  ("err", "falhas", "StatErr.TLabel"),
                                                  ("left", "não processados", "Stat.TLabel"))):
            box = ttk.Frame(stats, style="Inner.TFrame")
            box.grid(row=0, column=i, sticky="w", padx=(0, 28))
            v = tk.StringVar(self.root, value="0")
            self.stat_vars[key] = v
            ttk.Label(box, textvariable=v, style=style).pack(anchor="w")
            ttk.Label(box, text=label, style="CardMuted.TLabel").pack(anchor="w")
        wrap2, self.results_tree = self._make_tree(res_tab, [
            ("source", "Original", 240, "w", True), ("output", "Cópia gerada", 120, "w", False),
            ("status", "Situação", 120, "w", False), ("detail", "Detalhe", 260, "w", True)])
        wrap2.grid(row=1, column=0, sticky="nsew")
        self.result_empty = tk.Label(wrap2, text="Nenhum processamento executado ainda.", background=T.SURFACE,
                                     foreground=T.MUTED, font=self.fonts.base)
        self.result_empty.place(relx=0.5, rely=0.45, anchor="center")
        self.detail_var = tk.StringVar(self.root)
        self.detail_label = ttk.Label(res_tab, textvariable=self.detail_var, style="CardMuted.TLabel",
                                      wraplength=self.px(600), justify="left")
        self.detail_label.grid(row=2, column=0, sticky="we", pady=(8, 0))
        self.results_tree.bind("<<TreeviewSelect>>", self._on_result_select)
        nb.add(res_tab, text="Resultado", underline=0)
        self.files_tab, self.results_tab = files_tab, res_tab
        return card

    def _build_progress(self, parent):
        card = ttk.Frame(parent, style="Card.TFrame", padding=self.px(16))
        top = ttk.Frame(card, style="Inner.TFrame")
        top.pack(fill="x")
        ttk.Label(top, textvariable=self.progress_text_var, style="CardBold.TLabel").pack(side="left")
        ttk.Label(top, textvariable=self.progress_count_var, style="Card.TLabel",
                  font=self.fonts.mono).pack(side="right")
        self.progress = ttk.Progressbar(card, orient="horizontal", mode="determinate", maximum=100,
                                        style="Brand.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(8, 6))
        ttk.Label(card, textvariable=self.current_var, style="CardMuted.TLabel").pack(anchor="w")

        # ações principais: sempre visíveis (fora da coluna rolável)
        line = ttk.Frame(card, style="Inner.TFrame")
        line.pack(fill="x", pady=(12, 0))
        line.columnconfigure(2, weight=1)
        self.start_btn = ttk.Button(line, text="Iniciar transformação", style="Primary.TButton",
                                    command=self.start, underline=0)
        self.start_btn.grid(row=0, column=0, sticky="w")
        self.cancel_btn = ttk.Button(line, text="Cancelar", style="Danger.TButton", command=self.cancel,
                                     underline=0, width=-8)
        self.cancel_btn.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.open_btn = ttk.Button(line, text="Abrir pasta de saída", command=self.open_output, underline=0,
                                   width=-8)
        self.open_btn.grid(row=0, column=3, sticky="e")
        self._actions_line = line
        self._actions_wrapped = False
        self.start_hint = ttk.Label(card, textvariable=self.start_hint_var, style="CardMuted.TLabel",
                                    wraplength=self.px(600), justify="left")
        self.start_hint.pack(fill="x", pady=(8, 0))
        self.cancel_btn.state(["disabled"])
        return card

    def _bind_keys(self):
        r = self.root
        r.bind("<F5>", lambda e: self.scan())
        r.bind("<Control-Return>", lambda e: self.start())
        r.bind("<Control-KP_Enter>", lambda e: self.start())
        r.bind("<Escape>", lambda e: self.cancel() if self.job_running else self.error_frame.hide())
        r.bind("<Alt-e>", lambda e: self.pick_input())
        r.bind("<Alt-s>", lambda e: self.pick_output())
        r.bind("<Alt-l>", lambda e: self.scan())
        r.bind("<Alt-i>", lambda e: self.start())
        r.bind("<Alt-c>", lambda e: self.cancel())
        r.bind("<Alt-a>", lambda e: self.open_output())
        for e in (self.input_entry, self.output_entry):
            e.bind("<Return>", lambda ev: self.scan())

    def _on_resize(self, event):
        if event.widget is not self.root:
            return
        # janela estreita: coluna esquerda um pouco menor (limiares em pixels lógicos de 96 DPI)
        sc = self.scale
        wl = event.width / sc
        left_w = self.px(320 if wl < 900 else (340 if wl < 980 else 380))
        self.left.canvas.configure(width=left_w)
        w = event.width
        wrap = left_w - self.px(60)
        self.picker_note.configure(wraplength=wrap)
        for lbl in self._left_wrap:
            lbl.configure(wraplength=wrap)
        self.notice_label.configure(wraplength=wrap)
        self.noise_hint.configure(wraplength=wrap - self.px(30))
        if not getattr(self, "_actions_pending", False):
            self._actions_pending = True
            self.root.after_idle(self._layout_actions)
        right_wrap = max(self.px(300), w - left_w - self.px(120))
        self.start_hint.configure(wraplength=right_wrap)
        self.subtitle.configure(wraplength=max(self.px(240), w - self.px(420)))
        self.files_note.configure(wraplength=right_wrap)
        self.detail_label.configure(wraplength=right_wrap)
        # janela baixa: a nota explicativa sai para a lista de arquivos ter espaço
        low = event.height / sc < 700
        if low != getattr(self, "_low", None):
            self._low = low
            if low:
                self.files_note.grid_remove()
                self.start_hint.pack_forget()
            else:
                self.files_note.grid()
                self.start_hint.pack(fill="x", pady=(8, 0))

    def _layout_actions(self):
        """Área estreita: texto curto e, se ainda faltar espaço, “Abrir…” desce de linha (nunca é cortado)."""
        self._actions_pending = False
        try:
            self.root.update_idletasks()
            avail = self._actions_line.winfo_width()
        except tk.TclError:
            return
        if avail <= 1:
            return
        btns = (self.start_btn, self.cancel_btn, self.open_btn)
        self.open_btn.configure(text="Abrir pasta de saída")
        if sum(b.winfo_reqwidth() for b in btns) + 8 > avail:
            self.open_btn.configure(text="Abrir saída")
        wrapped = sum(b.winfo_reqwidth() for b in btns) + 8 > avail
        if wrapped != self._actions_wrapped:
            self._actions_wrapped = wrapped
            if wrapped:
                self.open_btn.grid_configure(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
            else:
                self.open_btn.grid_configure(row=0, column=3, columnspan=1, sticky="e", pady=0)

    # ================================================================== utilidades de UI
    def show_error(self, msg: str):
        self.error_var.set(msg)
        self.error_frame.show()
        self.status_var.set("Erro: " + msg.splitlines()[0][:120])
        self.root.bell()

    def clear_error(self):
        self.error_frame.hide()

    def _set_warn(self, lines: list[str]):
        if lines:
            self.warn_var.set("\n".join(lines))
            self.warn_frame.show()
        else:
            self.warn_frame.hide()

    def _set_enabled(self, widgets, enabled: bool):
        for w in widgets:
            w.state(["!disabled"] if enabled else ["disabled"])

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Para testes: processa eventos até terminar listagem e job. True se ficou ocioso."""
        end = time.monotonic() + timeout
        while (self.busy or self.job_running) and time.monotonic() < end:
            self.root.update()
            time.sleep(0.02)
        self.root.update()
        return not (self.busy or self.job_running)

    # ================================================================== saúde
    def _load_health(self):
        try:
            self.health = services.health()
        except Exception as e:  # noqa: BLE001
            self.health = None
            self.show_error(f"Não foi possível verificar o sistema: {e}")
            return
        ff = self.health.get("ffmpeg") or {}
        if ff.get("available"):
            self.ffmpeg_chip.configure(text=f"● Vídeos: FFmpeg {ff.get('version') or ''}".rstrip(),
                                       background=T.OK_BG, foreground=T.OK)
        else:
            self.ffmpeg_chip.configure(text="● Vídeos indisponíveis (sem FFmpeg)",
                                       background=T.WARN_BG, foreground=T.WARN)
            self._set_warn([ff.get("error") or "FFmpeg não encontrado: vídeos não poderão ser processados."])

    # ================================================================== pastas
    def _pick(self, var: tk.StringVar, title: str, entry):
        if self.job_running:
            return
        if getattr(self, "_picking", False):
            return
        current = var.get().strip()
        initial = os.path.expanduser(current) if current and os.path.isdir(os.path.expanduser(current)) \
            else str(Path.home())
        self._picking = True
        self.status_var.set("Aguardando a escolha da pasta…")
        try:
            path = self.ask_directory(parent=self.root, title=title, initialdir=initial, mustexist=False)
        except Exception as e:  # noqa: BLE001
            self.show_error(f"Não foi possível abrir o seletor de pastas: {e}. Digite o caminho manualmente.")
            entry.focus_set()
            return
        finally:
            self._picking = False
            self.status_var.set("Pronto.")
        if path:
            var.set(display_path(str(path)))
            self._save_state()
        entry.focus_set()

    def pick_input(self):
        self._pick(self.input_var, "Escolha a pasta de entrada (originais)", self.input_entry)

    def pick_output(self):
        self._pick(self.output_var, "Escolha a pasta de saída (cópias)", self.output_entry)

    def _current_key(self):
        return (self.input_var.get().strip(), self.output_var.get().strip(), bool(self.recursive_var.get()))

    def _invalidate_scan(self):
        for e in (self.input_entry, self.output_entry):
            e.state(["!invalid"])
        if self.job_running:
            return
        if self.last_scan is not None and self._scan_key != self._current_key():
            self.last_scan = None
            self._scan_key = None
            self._render_files(message=("Pastas alteradas", "Clique em “Listar arquivos” para atualizar a lista."))
        self._update_start_state()

    # ================================================================== listagem
    def scan(self):
        if self.busy or self.job_running:
            return
        self.clear_error()
        inp, out, rec = self._current_key()
        if not inp:
            self.input_entry.state(["invalid"])
            self.input_entry.focus_set()
            self.show_error("Informe a pasta de entrada.")
            return
        self.busy = True
        self._save_state()
        self.status_var.set("Listando arquivos…")
        self._set_enabled([self.scan_btn, self.start_btn], False)
        self.scan_btn.configure(text="Listando…")
        self.root.configure(cursor="watch")
        key = (inp, out, rec)

        def work():
            try:
                res = services.scan(inp, out or None, rec)
                self._scan_q.put(("ok", key, res))
            except services.ServiceError as e:
                self._scan_q.put(("err", key, str(e)))
            except Exception as e:  # noqa: BLE001
                self._scan_q.put(("err", key, f"Erro inesperado ao listar: {e}"))

        threading.Thread(target=work, name="metaedit-scan", daemon=True).start()
        self.root.after(30, self._check_scan)

    def _check_scan(self):
        try:
            kind, key, payload = self._scan_q.get_nowait()
        except queue.Empty:
            self.root.after(30, self._check_scan)
            return
        self.busy = False
        self.root.configure(cursor="")
        self.scan_btn.configure(text="Listar arquivos")
        self._set_enabled([self.scan_btn], True)
        if kind == "err":
            self.last_scan = None
            self._scan_key = None
            self.input_entry.state(["invalid"]) if "entrada" in payload.lower() else None
            self.output_entry.state(["invalid"]) if "saída" in payload.lower() else None
            self.show_error(payload)
            self._render_files(message=("Não foi possível listar a pasta", "Corrija o problema indicado acima e tente novamente."))
        else:
            res = payload
            self.last_scan = res
            # mostra os caminhos normalizados (sem disparar invalidação)
            if res.get("input_dir"):
                self.input_var.set(res["input_dir"])
            if res.get("output_dir"):
                self.output_var.set(res["output_dir"])
            self.last_scan = res
            self._scan_key = self._current_key()
            self._render_files()
            self._save_state()
            n = len(res.get("files") or [])
            self.status_var.set(f"{plural(n, 'arquivo compatível encontrado', 'arquivos compatíveis encontrados')}.")
            self.notebook.select(self.files_tab)
        self._update_start_state()

    def _render_files(self, message: tuple[str, str] | None = None):
        tree = self.file_tree
        tree.delete(*tree.get_children())
        scan = self.last_scan
        warn = []
        if self.health and not (self.health.get("ffmpeg") or {}).get("available"):
            warn.append((self.health.get("ffmpeg") or {}).get("error") or "FFmpeg não encontrado.")
        if not scan:
            self.counts_var.set("")
            title, text = message or ("Nenhuma pasta listada ainda",
                                      "Escolha a pasta de entrada e clique em “Listar arquivos”.")
            self._show_empty(title, text)
            self._set_warn(warn)
            return
        files = scan.get("files") or []
        skipped = scan.get("skipped") or []
        counts = scan.get("counts") or {}
        for w in scan.get("warnings") or []:
            if w not in warn:
                warn.append(w)
        self._set_warn(warn)
        start = scan.get("next_index") if isinstance(scan.get("next_index"), int) else 1
        parts = []
        if files:
            parts = [plural(counts.get("image", 0), "imagem", "imagens"),
                     plural(counts.get("video", 0), "vídeo", "vídeos")]
        if skipped:
            parts.append(plural(len(skipped), "ignorado", "ignorados"))
        self.counts_var.set(" · ".join(parts))
        for i, f in enumerate(files):
            kind = "video" if f.get("kind") == "video" else "image"
            tree.insert("", "end", values=(i + 1, f.get("rel_path") or f.get("name"),
                                            "Vídeo" if kind == "video" else "Imagem", format_size(f.get("size")),
                                            f"{start + i}{(f.get('ext') or '').lower()}"),
                        tags=(kind, "odd" if i % 2 else "even"))
        for j, s in enumerate(skipped):
            tree.insert("", "end", values=("—", s.get("rel_path") or s.get("name"), "Ignorado", "",
                                            s.get("reason", "")),
                        tags=("ignored", "odd" if (len(files) + j) % 2 else "even"))
        if not files:
            fmts = ""
            if self.health:
                fm = self.health.get("formats") or {}
                fmts = " ".join([*fm.get("image", []), *fm.get("video", [])])
            if skipped:
                text = (f"A pasta tem {plural(len(skipped), 'item', 'itens')}, mas nenhum em formato suportado."
                        + (f"\nFormatos aceitos: {fmts}" if fmts else ""))
            else:
                text = "A pasta está vazia." + ("" if self.recursive_var.get()
                                                else " Se os arquivos estiverem em subpastas, marque “Incluir subpastas”.")
            # com ignorados, a mensagem fica embaixo para não cobrir a lista de motivos
            self._show_empty("Nenhuma imagem ou vídeo compatível", text, top=bool(skipped))
        else:
            self.empty_label.place_forget()

    def _show_empty(self, title: str, text: str, top: bool = False):
        self.empty_label.configure(text=f"{title}\n\n{text}")
        if top:
            self.empty_label.place(relx=0.5, rely=1.0, y=-24, anchor="s")
        else:
            self.empty_label.place(relx=0.5, rely=0.45, anchor="center")
        self.empty_label.lift()

    def _update_start_state(self):
        if self.job_running:
            self.start_btn.state(["disabled"])
            self.start_hint_var.set("Transformação em andamento…")
            return
        has_files = bool(self.last_scan and self.last_scan.get("files") and self._scan_key == self._current_key())
        out = self.output_var.get().strip()
        self._set_enabled([self.start_btn], has_files and bool(out) and not self.busy)
        if self.last_job and services.is_finished(self.last_job.get("status", "")) and self.last_scan is None:
            self.start_hint_var.set("Para processar novamente, clique em “Listar arquivos” (a numeração é atualizada).")
        elif not self.last_scan:
            self.start_hint_var.set("Liste os arquivos primeiro.")
        elif not has_files:
            self.start_hint_var.set("Não há arquivos compatíveis para processar.")
        elif not out:
            self.start_hint_var.set("Informe a pasta de saída.")
        else:
            n = len(self.last_scan["files"])
            self.start_hint_var.set(f"{plural(n, 'cópia será criada', 'cópias serão criadas')} em {out}")
        has_out = bool((self.last_job or {}).get("output_dir") or out)
        self._set_enabled([self.open_btn], has_out)

    # ================================================================== opções
    def _sync_options(self):
        if self.mode_var.get() == "replace":
            self.meta_frame.pack(fill="x", after=self.mode_replace)
        else:
            self.meta_frame.pack_forget()
        enabled = bool(self.noise_var.get()) and not self.job_running
        self._set_enabled(self.strength_btns, enabled)

    def _metadata(self) -> dict:
        return {k: v.get().strip() for k, v in self.meta_vars.items()}

    # ================================================================== execução
    def start(self):
        if self.job_running or self.busy:
            return
        if str(self.start_btn.cget("state")) == "disabled" or self.start_btn.instate(["disabled"]):
            if not self.last_scan:
                self.show_error("Liste os arquivos antes de iniciar.")
            elif not self.output_var.get().strip():
                self.output_entry.state(["invalid"])
                self.output_entry.focus_set()
                self.show_error("Informe a pasta de saída.")
            elif self._scan_key != self._current_key():
                self.show_error("As pastas mudaram desde a última listagem. Clique em “Listar arquivos” novamente.")
            return
        self.clear_error()
        inp, out, rec = self._current_key()
        mode = self.mode_var.get()
        try:
            self.job_id = services.start_job(
                inp, out, recursive=rec, metadata_mode=mode,
                metadata=self._metadata() if mode == "replace" else None,
                pixel_noise=bool(self.noise_var.get()), noise_strength=int(self.strength_var.get()))
        except services.ServiceError as e:
            self.show_error(str(e))
            return
        except Exception as e:  # noqa: BLE001
            self.show_error(f"Não foi possível iniciar: {e}")
            return
        self.last_job = None
        self._save_state()
        self.job_running = True
        self._set_running_ui(True)
        self._result_rows = {}
        self.results_tree.delete(*self.results_tree.get_children())
        self.result_empty.place_forget()
        for k in self.stat_vars:
            self.stat_vars[k].set("0")
        self.detail_var.set("")
        self.progress.configure(style="Brand.Horizontal.TProgressbar", value=0)
        self.progress_text_var.set("Na fila…")
        self.notebook.select(self.results_tab)
        self.status_var.set("Transformação iniciada.")
        self.cancel_btn.focus_set()
        self._poll()

    def _set_running_ui(self, running: bool):
        widgets = [self.input_entry, self.output_entry, self.pick_input_btn, self.pick_output_btn,
                   self.recursive_chk, self.scan_btn, self.mode_strip, self.mode_replace, self.noise_chk,
                   *self.meta_entries.values()]
        self._set_enabled(widgets, not running)
        self._set_enabled([self.cancel_btn], running)
        self._sync_options()
        self._update_start_state()

    def _poll(self):
        self._poll_after = None
        if not self.job_id:
            return
        try:
            job = services.get_job(self.job_id)
        except services.ServiceError as e:
            self.show_error(f"O processamento não foi encontrado: {e}")
            self._finish(None)
            return
        except Exception as e:  # noqa: BLE001
            self.current_var.set(f"Falha ao consultar o progresso ({e}); tentando novamente…")
            self._poll_after = self.root.after(POLL_MS * 3, self._poll)
            return
        self.last_job = job
        self._render_job(job)
        if services.is_finished(job.get("status", "")):
            self._finish(job)
        else:
            self._poll_after = self.root.after(POLL_MS, self._poll)

    def _render_job(self, job: dict):
        total = job.get("total") or 0
        processed = job.get("processed") or 0
        status = job.get("status")
        partial = job.get("current_progress") if status == "running" else None
        partial = min(max(float(partial), 0.0), 1.0) if isinstance(partial, (int, float)) else 0.0
        pct = 100 if status == "done" else (min(100, round((processed + partial) / total * 100)) if total else 0)
        self.progress.configure(value=pct)
        failed = job.get("failed") or 0
        labels = {"queued": "Na fila…", "running": f"Processando… {pct}%",
                  "done": "Concluído com falhas" if failed else "Concluído",
                  "error": "Interrompido por erro", "cancelled": "Cancelado"}
        self.progress_text_var.set(labels.get(status, "Processando…"))
        self.progress_count_var.set(f"{processed} / {total}")
        cur = job.get("current")
        self.current_var.set(f"Arquivo atual: {cur}" if status == "running" and cur else "")

        results = job.get("results") or []
        for i, r in enumerate(results):
            st = r.get("status") or "pending"
            row = (r.get("source", ""), r.get("output") or "—", STATUS_LABELS.get(st, st), r.get("error") or "")
            if self._result_rows.get(i) == row:
                continue
            tags = (st, "odd" if i % 2 else "even")
            iid = str(i)
            if iid in self._result_rows_iids():
                self.results_tree.item(iid, values=row, tags=tags)
            else:
                self.results_tree.insert("", "end", iid=iid, values=row, tags=tags)
            self._result_rows[i] = row
            if st == "processing":
                self.results_tree.see(iid)
        ok = job.get("succeeded")
        if ok is None:
            ok = sum(1 for r in results if r.get("status") == "done")
        if failed is None:
            failed = sum(1 for r in results if r.get("status") == "error")
        left = sum(1 for r in results if r.get("status") in ("pending", "skipped", "processing"))
        self.stat_vars["ok"].set(str(ok))
        self.stat_vars["err"].set(str(failed))
        self.stat_vars["left"].set(str(left))

    def _result_rows_iids(self):
        return set(self.results_tree.get_children())

    def _finish(self, job: dict | None):
        if self._poll_after:
            self.root.after_cancel(self._poll_after)
            self._poll_after = None
        self.job_running = False
        # numeração mudou: exige nova listagem antes de outra execução
        self.last_scan = None
        self._scan_key = None
        self._render_files(message=("Processamento finalizado",
                                    "Clique em “Listar arquivos” para processar novamente com a numeração atualizada."))
        self._set_running_ui(False)
        if job is None:
            return
        status = job.get("status")
        ok, failed = job.get("succeeded") or 0, job.get("failed") or 0
        if status == "error":
            self.progress.configure(style="Err.Horizontal.TProgressbar")
            self.show_error(f"O processamento foi interrompido: {job.get('error') or 'erro desconhecido.'}")
            msg = "Processamento interrompido."
        elif status == "cancelled":
            msg = f"Processamento cancelado. {plural(ok, 'cópia criada', 'cópias criadas')} antes do cancelamento."
        elif failed:
            self.progress.configure(style="Brand.Horizontal.TProgressbar")
            msg = (f"Concluído: {plural(ok, 'cópia criada', 'cópias criadas')}, "
                   f"{plural(failed, 'falha', 'falhas')} (selecione uma linha para ver o motivo).")
        else:
            self.progress.configure(style="Ok.Horizontal.TProgressbar")
            msg = f"Concluído com sucesso: {plural(ok, 'cópia criada', 'cópias criadas')}."
        self.current_var.set(f"Pasta de saída: {job.get('output_dir', '')}")
        self.status_var.set(msg)
        self.detail_var.set(msg)
        self.notebook.select(self.results_tab)
        self.results_tree.focus_set()
        children = self.results_tree.get_children()
        if children:
            self.results_tree.focus(children[0])
        self.root.bell() if failed or status == "error" else None

    def cancel(self):
        if not (self.job_running and self.job_id):
            return
        self.cancel_btn.state(["disabled"])
        self.progress_text_var.set("Cancelando… (o arquivo atual será concluído ou interrompido)")
        try:
            services.cancel_job(self.job_id)
        except services.ServiceError as e:
            self.show_error(f"Não foi possível cancelar: {e}")
            self.cancel_btn.state(["!disabled"])

    def _on_result_select(self, _e=None):
        sel = self.results_tree.selection()
        if not sel:
            return
        src, out, st, detail = self.results_tree.item(sel[0], "values")
        text = f"{src} → {out} · {st}"
        if detail:
            text += f"\nMotivo: {detail}"
        self.detail_var.set(text)

    def open_output(self):
        path = ((self.last_job or {}).get("output_dir") or self.output_var.get()).strip()
        if not path:
            self.show_error("Informe a pasta de saída.")
            return
        try:
            services.open_folder(path)
        except services.ServiceError as e:
            self.show_error(str(e))

    # ================================================================== estado persistente
    def _load_state(self):
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self.input_var.set(data.get("input_dir", "") or "")
            self.output_var.set(data.get("output_dir", "") or "")

    def _save_state(self):
        if not self._restore_state:
            return
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({"input_dir": self.input_var.get().strip(),
                                              "output_dir": self.output_var.get().strip()}), encoding="utf-8")
        except OSError:
            pass

    # ================================================================== encerrar
    def on_close(self):
        if self.job_running:
            if not messagebox.askyesno("Sair", "Há um processamento em andamento.\n"
                                               "Deseja cancelar e sair?", parent=self.root, icon="warning"):
                return
        self.destroy()

    def destroy(self):
        if self._poll_after:
            try:
                self.root.after_cancel(self._poll_after)
            except tk.TclError:
                pass
        try:  # some da tela já; o encerramento (cancelar ffmpeg, limpar temporários) pode levar ~1 s
            self.root.withdraw()
            self.root.update_idletasks()
        except tk.TclError:
            pass
        try:
            services.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass
