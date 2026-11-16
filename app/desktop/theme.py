"""Tema escuro azul/violeta para ttk (base 'clam')."""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# Paleta (fundo quase preto, azul de marca, violeta de destaque)
BG = "#0a0d16"
SURFACE = "#11141c"
SURFACE_2 = "#1a1e29"
SURFACE_3 = "#232838"
BORDER = "#2a3040"
BORDER_STRONG = "#3a4256"
TEXT = "#f1f2f5"
TEXT_2 = "#e5e5e5"
MUTED = "#a1a1aa"
DISABLED = "#6b7280"
BRAND = "#3b82f6"
BRAND_STRONG = "#2563eb"
BRAND_TEXT = "#60a5fa"
BRAND_DIM = "#1e3a5f"
ACCENT = "#8b5cf6"
ACCENT_TEXT = "#a78bfa"
OK = "#22c55e"
OK_BG = "#0f2a1a"
WARN = "#f59e0b"
WARN_BG = "#2b2110"
ERR = "#f43f5e"
ERR_TEXT = "#fb7185"
ERR_BG = "#2d1018"

_PREFERRED = ("Inter", "Segoe UI", "SF Pro Text", "Noto Sans", "Ubuntu", "Cantarell",
              "Open Sans", "DejaVu Sans", "Liberation Sans", "Helvetica")
_PREFERRED_MONO = ("JetBrains Mono", "Cascadia Mono", "SF Mono", "Consolas", "Ubuntu Mono",
                   "DejaVu Sans Mono", "Liberation Mono", "Courier")


def _pick(families, wanted):
    lower = {f.lower(): f for f in families}
    for w in wanted:
        if w.lower() in lower:
            return lower[w.lower()]
    return None


class Fonts:
    def __init__(self, root: tk.Misc):
        fams = set(tkfont.families(root))
        family = _pick(fams, _PREFERRED) or tkfont.nametofont("TkDefaultFont").actual("family")
        mono = _pick(fams, _PREFERRED_MONO) or "TkFixedFont"
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
            try:
                tkfont.nametofont(name).configure(family=family, size=10)
            except tk.TclError:
                pass
        self.family = family
        self.base = tkfont.Font(root, family=family, size=10)
        self.small = tkfont.Font(root, family=family, size=9)
        self.bold = tkfont.Font(root, family=family, size=10, weight="bold")
        self.h1 = tkfont.Font(root, family=family, size=15, weight="bold")
        self.h2 = tkfont.Font(root, family=family, size=12, weight="bold")
        self.big = tkfont.Font(root, family=family, size=18, weight="bold")
        self.button = tkfont.Font(root, family=family, size=10, weight="bold")
        self.mono = tkfont.Font(root, family=mono, size=10) if mono != "TkFixedFont" else tkfont.nametofont("TkFixedFont")


def apply(root: tk.Tk) -> Fonts:
    from .platform import ui_scale

    fonts = Fonts(root)
    scale = ui_scale(root)
    fonts.scale = scale

    def P(*v):  # pixels escalados por DPI (Windows 125%/150%…); 1.0 em 96 DPI
        r = tuple(max(0, int(round(x * scale))) for x in v)
        return r if len(r) > 1 else r[0]

    root.configure(background=BG)
    # widgets Tk clássicos (menus, listas, diálogos simples)
    root.option_add("*Background", SURFACE)
    root.option_add("*Foreground", TEXT_2)
    root.option_add("*selectBackground", BRAND)
    root.option_add("*selectForeground", "white")
    root.option_add("*insertBackground", TEXT)

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=BG, foreground=TEXT_2, fieldbackground=SURFACE_2,
                bordercolor=BORDER, lightcolor=SURFACE_2, darkcolor=SURFACE_2,
                troughcolor=SURFACE_2, focuscolor=BRAND_TEXT, selectbackground=BRAND,
                selectforeground="white", insertcolor=TEXT, font=fonts.base,
                arrowcolor=MUTED)
    s.map(".", foreground=[("disabled", DISABLED)])

    # Superfícies
    s.configure("TFrame", background=BG)
    s.configure("Card.TFrame", background=SURFACE, bordercolor=BORDER, relief="solid", borderwidth=1)
    s.configure("Inner.TFrame", background=SURFACE)
    s.configure("TLabel", background=BG, foreground=TEXT_2)
    s.configure("Card.TLabel", background=SURFACE, foreground=TEXT_2)
    s.configure("CardBold.TLabel", background=SURFACE, foreground=TEXT, font=fonts.bold)
    s.configure("Muted.TLabel", background=BG, foreground=MUTED, font=fonts.small)
    s.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED, font=fonts.small)
    s.configure("H1.TLabel", background=BG, foreground=TEXT, font=fonts.h1)
    s.configure("H2.TLabel", background=SURFACE, foreground=TEXT, font=fonts.h2)
    s.configure("Stat.TLabel", background=SURFACE, foreground=TEXT, font=fonts.big)
    s.configure("StatOk.TLabel", background=SURFACE, foreground=OK, font=fonts.big)
    s.configure("StatErr.TLabel", background=SURFACE, foreground=ERR_TEXT, font=fonts.big)
    s.configure("Status.TLabel", background=SURFACE, foreground=MUTED, font=fonts.small, padding=P(12, 5))

    s.configure("TSeparator", background=BORDER)

    # Botões
    common = dict(padding=P(14, 8), relief="flat", borderwidth=1, font=fonts.button, focusthickness=2)
    s.configure("TButton", background=SURFACE_2, foreground=TEXT_2, bordercolor=BORDER_STRONG,
                lightcolor=SURFACE_2, darkcolor=SURFACE_2, **common)
    s.map("TButton",
          background=[("disabled", SURFACE), ("pressed", SURFACE_3), ("active", SURFACE_3)],
          foreground=[("disabled", DISABLED), ("active", BRAND_TEXT)],
          bordercolor=[("focus", BRAND_TEXT), ("active", BRAND)],
          lightcolor=[("active", SURFACE_3)], darkcolor=[("active", SURFACE_3)])
    s.configure("Primary.TButton", background=BRAND, foreground="white", bordercolor=BRAND,
                lightcolor=BRAND, darkcolor=BRAND, **common)
    s.map("Primary.TButton",
          background=[("disabled", BRAND_DIM), ("pressed", BRAND_STRONG), ("active", BRAND_STRONG)],
          foreground=[("disabled", "#8ea3c4")],
          bordercolor=[("focus", "#bfdbfe"), ("disabled", BRAND_DIM)],
          lightcolor=[("disabled", BRAND_DIM), ("active", BRAND_STRONG)],
          darkcolor=[("disabled", BRAND_DIM), ("active", BRAND_STRONG)])
    s.configure("Danger.TButton", background=SURFACE_2, foreground=ERR_TEXT, bordercolor="#7f1d2d",
                lightcolor=SURFACE_2, darkcolor=SURFACE_2, **common)
    s.map("Danger.TButton",
          background=[("disabled", SURFACE), ("active", ERR_BG)],
          foreground=[("disabled", DISABLED)],
          bordercolor=[("focus", ERR_TEXT), ("active", ERR)])
    s.configure("Close.TButton", background=ERR_BG, foreground=ERR_TEXT, padding=P(8, 2),
                bordercolor=ERR_BG, lightcolor=ERR_BG, darkcolor=ERR_BG, relief="flat")
    s.map("Close.TButton", background=[("active", "#4a1622")], bordercolor=[("focus", ERR_TEXT)])

    # Campos
    s.configure("TEntry", fieldbackground=SURFACE_2, foreground=TEXT, bordercolor=BORDER_STRONG,
                lightcolor=SURFACE_2, darkcolor=SURFACE_2, padding=P(8, 7), insertcolor=TEXT)
    s.map("TEntry",
          bordercolor=[("invalid", ERR), ("focus", BRAND)],
          lightcolor=[("focus", BRAND)], darkcolor=[("focus", BRAND)],
          fieldbackground=[("disabled", SURFACE), ("readonly", SURFACE)],
          foreground=[("disabled", DISABLED)])

    for name in ("TCheckbutton", "TRadiobutton"):
        s.configure(name, background=SURFACE, foreground=TEXT_2, indicatorbackground=SURFACE_2,
                    indicatorforeground="white", indicatormargin=P(0, 2, 8, 2), padding=P(2, 3),
                    focuscolor=BRAND_TEXT, upperbordercolor=BORDER_STRONG, lowerbordercolor=BORDER_STRONG)
        s.map(name,
              background=[("active", SURFACE)],
              foreground=[("disabled", DISABLED), ("active", TEXT)],
              indicatorbackground=[("disabled", SURFACE), ("selected", BRAND), ("active", SURFACE_3)],
              indicatorforeground=[("selected", "white")])
    # botões segmentados (intensidade)
    s.configure("Seg.TRadiobutton", background=SURFACE_2, foreground=TEXT_2, padding=P(12, 6),
                anchor="center", font=fonts.base, bordercolor=BORDER_STRONG, relief="flat",
                lightcolor=SURFACE_2, darkcolor=SURFACE_2)
    s.map("Seg.TRadiobutton",
          background=[("disabled", SURFACE), ("selected", BRAND), ("active", SURFACE_3)],
          foreground=[("disabled", DISABLED), ("selected", "white")],
          bordercolor=[("focus", BRAND_TEXT), ("selected", BRAND)])
    s.layout("Seg.TRadiobutton", s.layout("Toolbutton"))

    # Tabela
    s.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT_2,
                bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE, rowheight=P(28), font=fonts.base)
    s.map("Treeview", background=[("selected", BRAND_DIM)], foreground=[("selected", TEXT)])
    s.configure("Treeview.Heading", background=SURFACE_2, foreground=MUTED, relief="flat",
                bordercolor=BORDER, lightcolor=SURFACE_2, darkcolor=SURFACE_2, font=fonts.small,
                padding=P(8, 6))
    s.map("Treeview.Heading", background=[("active", SURFACE_3)])

    # Abas
    s.configure("TNotebook", background=SURFACE, bordercolor=BORDER, tabmargins=(0, 0, 0, 0),
                lightcolor=SURFACE, darkcolor=SURFACE)
    s.configure("TNotebook.Tab", background=SURFACE, foreground=MUTED, padding=P(16, 7),
                bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE, font=fonts.bold)
    s.map("TNotebook.Tab",
          background=[("selected", SURFACE_2), ("active", SURFACE_2)],
          foreground=[("selected", BRAND_TEXT), ("active", TEXT)],
          lightcolor=[("selected", BRAND)], bordercolor=[("selected", BORDER)])

    # Progresso
    for name, color in (("Brand", BRAND), ("Ok", OK), ("Err", ERR)):
        s.configure(f"{name}.Horizontal.TProgressbar", troughcolor=SURFACE_2, background=color,
                    bordercolor=BORDER, lightcolor=color, darkcolor=color, thickness=P(12))

    # Rolagem
    for orient in ("Vertical", "Horizontal"):
        s.configure(f"{orient}.TScrollbar", background=SURFACE_3, troughcolor=SURFACE,
                    bordercolor=SURFACE, lightcolor=SURFACE_3, darkcolor=SURFACE_3, arrowcolor=MUTED,
                    gripcount=0)
        s.map(f"{orient}.TScrollbar", background=[("active", BORDER_STRONG)])
    return fonts
