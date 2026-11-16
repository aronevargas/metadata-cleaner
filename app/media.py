"""Image (Pillow) and video (ffmpeg) processing: strip/replace metadata + subtle pixel noise.

Never opens the source for writing; always writes to `dst` (a temp path chosen by caller).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, PngImagePlugin

from .proc import IS_WINDOWS, subprocess_kwargs

Image.MAX_IMAGE_PIXELS = 300_000_000  # allow large photos but still guard decompression bombs

META_FIELDS = ("title", "artist", "comment", "copyright", "software")


class MediaError(Exception):
    pass


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------- ffmpeg discovery
FFMPEG_MISSING_MSG = (
    "FFmpeg não encontrado. Instale-o (Windows: 'winget install Gyan.FFmpeg' ou copie ffmpeg.exe e "
    "ffprobe.exe para a pasta 'ffmpeg' ao lado do aplicativo; Linux: 'sudo apt install ffmpeg'; "
    "macOS: 'brew install ffmpeg'; https://ffmpeg.org/download.html) e reinicie o app, "
    "ou defina METAEDIT_FFMPEG/METAEDIT_FFPROBE com o caminho."
)

_EXE = ".exe" if IS_WINDOWS else ""


def bundled_dirs() -> list[Path]:
    """Folders searched for ffmpeg/ffprobe shipped next to the app, before the PATH.

    Frozen (PyInstaller): the folder of the executable and its ``_internal`` folder.
    Source checkout: the project root. In each base: ``.``, ``ffmpeg/``, ``ffmpeg/bin/``, ``bin/``.
    """
    bases: list[Path] = []
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).resolve().parent)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(Path(meipass))
    else:
        bases.append(Path(__file__).resolve().parent.parent)
    out: list[Path] = []
    for b in bases:
        for sub in ("", "ffmpeg", os.path.join("ffmpeg", "bin"), "bin"):
            d = b / sub if sub else b
            if d not in out:
                out.append(d)
    return out


def _is_exe(p: str) -> bool:
    return os.path.isfile(p) and os.access(p, os.X_OK)


def _resolve(env: str, name: str):
    override = os.environ.get(env)
    if override:
        return override if _is_exe(override) else None
    for d in bundled_dirs():
        cand = d / (name + _EXE)
        if _is_exe(str(cand)):
            return str(cand)
    return shutil.which(name)


def ffmpeg_info() -> dict:
    ff = _resolve("METAEDIT_FFMPEG", "ffmpeg")
    fp = _resolve("METAEDIT_FFPROBE", "ffprobe")
    if not ff or not fp:
        missing = "ffmpeg" if not ff else "ffprobe"
        return {"available": False, "path": ff, "version": None,
                "error": FFMPEG_MISSING_MSG + f" (faltando: {missing})"}
    try:
        out = subprocess.run([ff, "-version"], capture_output=True, timeout=10, **subprocess_kwargs()).stdout
        m = re.search(r"ffmpeg version (\S+)", out)
        ver = m.group(1) if m else "desconhecida"
    except Exception as e:  # noqa: BLE001
        return {"available": False, "path": ff, "version": None, "error": f"FFmpeg não executou: {e}"}
    return {"available": True, "path": ff, "ffprobe": fp, "version": ver, "error": None}


# --------------------------------------------------------------------------- images
def _noise(arr: np.ndarray, strength: int, has_alpha: bool, rng: np.random.Generator) -> np.ndarray:
    """Add +/-strength to a sparse random subset of colour samples (alpha untouched)."""
    strength = max(1, min(3, int(strength)))
    fraction = {1: 0.03, 2: 0.06, 3: 0.10}[strength]
    a = arr.astype(np.int16)
    if a.ndim == 2:
        color = a[..., None]
    else:
        color = a[..., :-1] if has_alpha else a
    h, w = color.shape[:2]
    mask = rng.random((h, w)) < fraction
    if has_alpha:
        mask &= arr[..., -1] > 0  # only visible pixels
    if not mask.any():  # tiny images: force at least one visible pixel
        idx = np.argwhere(arr[..., -1] > 0) if has_alpha else np.argwhere(np.ones((h, w), bool))
        if len(idx):
            y, x = idx[rng.integers(len(idx))]
            mask[y, x] = True
    delta = rng.integers(1, strength + 1, size=color.shape, dtype=np.int16)
    delta *= rng.choice(np.array([-1, 1], dtype=np.int16), size=color.shape)
    delta[~mask] = 0
    newc = color + delta
    # if clipping would cancel the change, flip direction
    newc = np.where(newc > 255, color - np.abs(delta), newc)
    newc = np.where(newc < 0, color + np.abs(delta), newc)
    if a.ndim == 2:
        a = newc[..., 0]
    elif has_alpha:
        a[..., :-1] = newc
    else:
        a = newc
    return np.clip(a, 0, 255).astype(np.uint8)


def _exif_for(meta: dict) -> Image.Exif | None:
    tags = {"title": 0x010E, "artist": 0x013B, "software": 0x0131, "copyright": 0x8298}
    exif = Image.Exif()
    for k, tag in tags.items():
        if meta.get(k):
            exif[tag] = str(meta[k])
    if meta.get("comment"):
        exif[0x9C9C] = str(meta["comment"]).encode("utf-16-le") + b"\x00\x00"  # XPComment
    return exif if len(exif) else None


def process_image(src: Path, dst: Path, ext: str, *, metadata_mode: str, metadata: dict,
                  pixel_noise: bool, noise_strength: int, rng: np.random.Generator) -> None:
    try:
        with Image.open(src) as im:
            im.load()
            fmt = im.format
            icc = im.info.get("icc_profile")
            im = ImageOps.exif_transpose(im)  # bake orientation into pixels
    except Image.DecompressionBombError as e:
        raise MediaError(f"Imagem grande demais: {e}") from e
    except Image.UnidentifiedImageError as e:
        raise MediaError("Imagem corrompida ou formato não reconhecido.") from e
    except Exception as e:  # noqa: BLE001
        raise MediaError(f"Não foi possível ler a imagem: {e}") from e

    if getattr(im, "n_frames", 1) > 1:
        raise MediaError("Imagens animadas/multipágina não são suportadas.")

    has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info) \
        or (im.mode in ("RGB", "L") and "transparency" in im.info)
    if im.mode == "CMYK":
        im = im.convert("RGB")
        icc = None  # CMYK profile no longer applies
        has_alpha = False
    if has_alpha:
        target = "LA" if im.mode in ("L", "LA") else "RGBA"
    else:
        target = "L" if im.mode in ("L", "1", "I", "I;16", "F") else "RGB"
    if im.mode.startswith("I;16"):
        im = Image.fromarray((np.asarray(im).astype(np.uint32) >> 8).astype(np.uint8), mode="L")
    elif im.mode in ("I", "F"):
        im = Image.fromarray(np.clip(np.asarray(im), 0, 255).astype(np.uint8), mode="L")
    im = im.convert(target)

    base = np.asarray(im)
    is_jpeg = ext in (".jpg", ".jpeg")

    kwargs: dict = {}
    if icc:
        kwargs["icc_profile"] = icc  # colour fidelity only; not identifying metadata
    replace = metadata_mode == "replace"
    exif = _exif_for(metadata) if replace else None
    lossless_webp = ext == ".webp" and fmt == "WEBP" and _webp_lossless(src)

    def save(arr: np.ndarray) -> str:
        out = Image.fromarray(arr, mode=target)  # fresh image: carries no metadata at all
        if is_jpeg and out.mode == "LA":
            out = out.convert("L")
        if is_jpeg and out.mode == "RGBA":
            out = out.convert("RGB")
        kw = dict(kwargs)
        if is_jpeg:
            kw.update(quality=95, optimize=True)
            if exif:
                kw["exif"] = exif
            out.save(dst, "JPEG", **kw)
        elif ext == ".png":
            if replace:
                info = PngImagePlugin.PngInfo()
                names = {"title": "Title", "artist": "Author", "comment": "Comment",
                         "copyright": "Copyright", "software": "Software"}
                for k, n in names.items():
                    if metadata.get(k):
                        info.add_itxt(n, str(metadata[k]))
                kw["pnginfo"] = info
            out.save(dst, "PNG", optimize=True, **kw)
        elif ext == ".webp":
            if exif:
                kw["exif"] = exif
            out.save(dst, "WEBP", lossless=lossless_webp, quality=95, **kw)
        elif ext in (".tif", ".tiff"):
            if exif:
                kw["exif"] = exif
            out.save(dst, "TIFF", compression="tiff_lzw", **kw)
        elif ext == ".bmp":
            out.save(dst, "BMP")
        else:
            raise MediaError(f"Extensão de imagem não suportada: {ext}")
        return out.mode

    try:
        if not pixel_noise:
            save(base)
            return
        # Lossy codecs (JPEG, lossy WebP) can quantise subtle noise away, e.g. on flat colours.
        # Verify the *decoded* output differs from the source pixels; escalate if it does not.
        for level in range(MAX_NOISE_LEVEL + 1):
            arr = _noise(base, noise_strength, has_alpha, rng)
            if level:
                arr = _block_shift(arr, level, has_alpha, rng)
            mode = save(arr)
            if _decoded_differs(dst, base, target, mode):
                return
    except MediaError:
        raise
    except Exception as e:  # noqa: BLE001
        raise MediaError(f"Falha ao gravar imagem: {e}") from e
    raise MediaError("Não foi possível alterar os pixels de forma detectável após a compressão.")


MAX_NOISE_LEVEL = 5


def _block_shift(arr: np.ndarray, level: int, has_alpha: bool, rng: np.random.Generator) -> np.ndarray:
    """Shift whole 8x8-aligned blocks by +/-(level+1): a low-frequency (DC) change that survives
    JPEG/WebP quantisation, unlike sparse single-pixel noise. Still visually negligible."""
    a = arr.astype(np.int16)
    h, w = a.shape[:2]
    by, bx = max(1, h // 8), max(1, w // 8)
    nblocks = by * bx
    count = max(1, int(nblocks * min(0.5, 0.02 * level * level)))
    amount = level + 1
    picks = rng.choice(nblocks, size=min(count, nblocks), replace=False)
    for p in picks:
        y0, x0 = (p // bx) * 8, (p % bx) * 8
        blk = a[y0:y0 + 8, x0:x0 + 8]
        color = blk if a.ndim == 2 else (blk[..., :-1] if has_alpha else blk)
        sign = 1 if color.mean() < 128 else -1  # move away from the clipping edge
        color += sign * amount
        if has_alpha:  # keep fully transparent pixels untouched
            hidden = blk[..., -1] == 0
            color[hidden] -= sign * amount
    return np.clip(a, 0, 255).astype(np.uint8)


def _decoded_differs(path: Path, base: np.ndarray, base_mode: str, saved_mode: str) -> bool:
    with Image.open(path) as im:
        im.load()
        got = np.asarray(im.convert(saved_mode))
    ref = np.asarray(Image.fromarray(base, mode=base_mode).convert(saved_mode))
    if got.shape != ref.shape:
        return True
    diff = got != ref
    changed = int((diff.any(-1) if diff.ndim == 3 else diff).sum())
    total = ref.shape[0] * ref.shape[1]
    return changed >= max(1, int(total * MIN_CHANGED_FRACTION))


MIN_CHANGED_FRACTION = 0.001  # >= 0.1% of decoded pixels must differ from the source


def _webp_lossless(src: Path) -> bool:
    with open(src, "rb") as f:
        head = f.read(64)
    return b"VP8L" in head


# --------------------------------------------------------------------------- video
VIDEO_CODECS = {
    ".mp4": ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
             "-bsf:v", "filter_units=remove_types=6", "-movflags", "+faststart"],
    ".m4v": ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
             "-bsf:v", "filter_units=remove_types=6"],
    ".mov": ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
             "-bsf:v", "filter_units=remove_types=6"],
    ".mkv": ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
             "-bsf:v", "filter_units=remove_types=6"],
    ".webm": ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-row-mt", "1", "-pix_fmt", "yuv420p"],
}
# audio codecs that the target container accepts for stream copy
AUDIO_COPY_OK = {
    ".mp4": {"aac", "mp3", "ac3", "eac3", "alac", "opus", "flac"},
    ".m4v": {"aac", "mp3", "ac3", "eac3", "alac"},
    ".mov": {"aac", "mp3", "ac3", "eac3", "alac", "pcm_s16le", "pcm_s24le", "pcm_s16be"},
    ".mkv": None,  # anything
    ".webm": {"opus", "vorbis"},
}


def probe(ff: dict, src: Path) -> dict:
    r = subprocess.run([ff["ffprobe"], "-v", "error", "-print_format", "json", "-show_streams",
                        "-show_format", str(src)], capture_output=True, timeout=60, **subprocess_kwargs())
    if r.returncode != 0:
        raise MediaError("Vídeo inválido ou corrompido: " + (r.stderr.strip().splitlines() or ["ffprobe falhou"])[-1])
    return json.loads(r.stdout or "{}")


def process_video(src: Path, dst: Path, ext: str, *, metadata_mode: str, metadata: dict,
                  pixel_noise: bool, noise_strength: int, cancel: threading.Event,
                  progress=None) -> None:
    ff = ffmpeg_info()
    if not ff["available"]:
        raise MediaError(ff["error"])
    info = probe(ff, src)
    streams = info.get("streams", [])
    if not any(s.get("codec_type") == "video" for s in streams):
        raise MediaError("Arquivo não contém faixa de vídeo.")
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    duration = float(info.get("format", {}).get("duration") or 0)

    allowed = AUDIO_COPY_OK.get(ext)
    copy_audio = all(allowed is None or s.get("codec_name") in allowed for s in audio)
    if copy_audio:
        acodec = ["-c:a", "copy"]
    elif ext == ".webm":
        acodec = ["-c:a", "libopus", "-b:a", "160k"]
    else:
        acodec = ["-c:a", "aac", "-b:a", "192k"]

    strength = max(1, min(3, int(noise_strength)))
    vf = [f"noise=alls={strength * 2}:allf=t"] if pixel_noise else []
    vf.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")  # yuv420p needs even dims

    cmd = [ff["path"], "-hide_banner", "-nostdin", "-y", "-i", str(src),
           "-map", "0:v:0", "-map", "0:a?", "-sn", "-dn",
           "-map_metadata", "-1", "-map_chapters", "-1",
           "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
           "-vf", ",".join(vf), *VIDEO_CODECS[ext], *acodec,
           "-metadata:s:v", "encoder=", "-metadata:s:a", "encoder=",
           "-metadata:s:v", "handler_name=", "-metadata:s:a", "handler_name="]
    if metadata_mode == "replace":
        keymap = {"title": "title", "artist": "artist", "comment": "comment",
                  "copyright": "copyright", "software": "encoder"}
        for k, mk in keymap.items():
            if metadata.get(k):
                cmd += ["-metadata", f"{mk}={metadata[k]}"]
    cmd += ["-progress", "pipe:1", "-nostats", str(dst)]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **subprocess_kwargs())
    stderr_buf: list[str] = []
    t = threading.Thread(target=lambda: stderr_buf.extend(proc.stderr), daemon=True)
    t.start()
    try:
        for line in proc.stdout:
            if cancel.is_set():
                proc.kill()
                raise Cancelled()
            if progress and duration and line.startswith("out_time_us="):
                try:
                    progress(min(1.0, int(line.split("=")[1]) / 1e6 / duration))
                except ValueError:
                    pass
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    t.join(timeout=5)
    if proc.returncode != 0:
        tail = "".join(stderr_buf).strip().splitlines()[-3:]
        raise MediaError("FFmpeg falhou: " + " | ".join(tail))
