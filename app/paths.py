"""Path normalisation, scanning and collision-free output naming."""
import os
import re
from pathlib import Path

from .formats import TMP_PREFIX, kind_of


class PathError(ValueError):
    pass


def norm(p: str) -> Path:
    if not p or not str(p).strip():
        raise PathError("Caminho vazio.")
    return Path(os.path.realpath(os.path.expanduser(str(p).strip())))


def _key(p: Path) -> str:
    """Comparison key: case-insensitive on Windows (os.path.normcase), exact elsewhere."""
    return os.path.normcase(os.path.normpath(str(p)))


def same_path(a: Path, b: Path) -> bool:
    return _key(a) == _key(b)


def is_within(child: Path, parent: Path) -> bool:
    c, p = _key(child), _key(parent)
    if c == p:
        return True
    return c.startswith(p if p.endswith(os.sep) else p + os.sep)


_HIDDEN_ATTRS = 0x2 | 0x4  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM (Windows only)


def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    try:
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & _HIDDEN_ATTRS)


def _is_link(entry: os.DirEntry) -> bool:
    if entry.is_symlink():
        return True
    is_junction = getattr(entry, "is_junction", None)  # Windows directory junctions (3.12+)
    return bool(is_junction and is_junction())


def validate_dirs(input_dir: str, output_dir: str | None):
    inp = norm(input_dir)
    if not inp.exists():
        raise PathError(f"Pasta de entrada não existe: {inp}")
    if not inp.is_dir():
        raise PathError(f"Entrada não é uma pasta: {inp}")
    out = None
    if output_dir:
        out = norm(output_dir)
        if same_path(out, inp):
            raise PathError("A pasta de saída deve ser diferente da pasta de entrada.")
        if is_within(inp, out):
            raise PathError("A pasta de entrada não pode estar dentro da pasta de saída.")
        if out.exists() and not out.is_dir():
            raise PathError(f"Saída existe e não é uma pasta: {out}")
    return inp, out


_num = re.compile(r"(\d+)")


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in _num.split(s)]


def scan(inp: Path, out: Path | None, recursive: bool):
    files, skipped = [], []

    def walk(d: Path):
        try:
            entries = sorted(os.scandir(d), key=lambda e: natural_key(e.name))
        except OSError as e:
            skipped.append({"name": d.name, "rel_path": os.path.relpath(d, inp), "reason": f"sem acesso: {e.strerror}"})
            return
        for e in entries:
            p = Path(e.path)
            rel = os.path.relpath(p, inp)
            if _is_hidden(e):
                skipped.append({"name": e.name, "rel_path": rel, "reason": "arquivo oculto/temporário"})
                continue
            if _is_link(e):
                skipped.append({"name": e.name, "rel_path": rel, "reason": "link simbólico ignorado"})
                continue
            if out is not None and is_within(p, out):
                skipped.append({"name": e.name, "rel_path": rel, "reason": "pasta de saída (ignorada)"})
                continue
            if e.is_dir(follow_symlinks=False):
                if recursive:
                    walk(p)
                else:
                    skipped.append({"name": e.name, "rel_path": rel, "reason": "subpasta (ative recursivo)"})
                continue
            if not e.is_file(follow_symlinks=False):
                continue
            ext = p.suffix.lower()
            k = kind_of(ext)
            if k is None:
                skipped.append({"name": e.name, "rel_path": rel, "reason": "formato não suportado"})
                continue
            files.append({"name": e.name, "rel_path": rel, "size": e.stat(follow_symlinks=False).st_size,
                          "kind": k, "ext": ext, "path": str(p)})

    walk(inp)
    files.sort(key=lambda f: natural_key(f["rel_path"]))
    return files, skipped


def next_index(out: Path | None) -> int:
    if out is None or not out.is_dir():
        return 1
    hi = 0
    for name in os.listdir(out):
        stem = name.split(".", 1)[0]
        if stem.isdigit():
            hi = max(hi, int(stem))
    return hi + 1


def publish_exclusive(tmp: Path, out: Path, start: int, ext: str) -> tuple[str, int]:
    """Hard-link tmp to <n><ext> without ever overwriting; returns (name, n)."""
    n = start
    while True:
        # Also treat any existing file with this number (any extension) as taken.
        taken = any(name.split(".", 1)[0] == str(n) for name in os.listdir(out))
        if not taken:
            target = out / f"{n}{ext}"
            try:
                os.link(tmp, target)  # atomic, fails if target exists
                return target.name, n
            except FileExistsError:
                pass
            except OSError:
                # filesystems without hard links: exclusive create + copy
                try:
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
                except FileExistsError:
                    n += 1
                    continue
                with os.fdopen(fd, "wb") as dst, open(tmp, "rb") as src:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
                return target.name, n
        n += 1


def tmp_path(out: Path, ext: str) -> Path:
    import secrets
    return out / f"{TMP_PREFIX}{secrets.token_hex(6)}{ext}"
