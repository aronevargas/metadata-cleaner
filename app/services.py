"""In-process service layer used by the desktop GUI (and by the legacy HTTP server).

No HTTP, no callbacks: jobs run on a background thread owned by `app.jobs`; callers poll
`get_job()`. Contract: docs/services.md.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from . import __version__, jobs, media
from .formats import IMAGE_EXTS, VIDEO_EXTS
from .proc import subprocess_kwargs
from .paths import PathError, next_index, scan as _scan, validate_dirs

FINISHED_STATUSES = frozenset({"done", "error", "cancelled"})
METADATA_FIELDS = media.META_FIELDS


class ServiceError(Exception):
    """User-facing error; str(e) is a Portuguese message ready to display."""


class ConflictError(ServiceError):
    """A job is already running for the same output folder."""


class NotFoundError(ServiceError):
    """Unknown job id."""


def health() -> dict:
    return {"version": __version__, "ffmpeg": media.ffmpeg_info(),
            "formats": {"image": list(IMAGE_EXTS), "video": list(VIDEO_EXTS)}}


def _dirs(input_dir, output_dir):
    try:
        return validate_dirs(input_dir, output_dir)
    except PathError as e:
        raise ServiceError(str(e)) from e


def scan(input_dir: str, output_dir: str | None = None, recursive: bool = False) -> dict:
    inp, out = _dirs(input_dir, output_dir)
    files, skipped = _scan(inp, out, bool(recursive))
    warnings = []
    if any(f["kind"] == "video" for f in files):
        ff = media.ffmpeg_info()
        if not ff["available"]:
            warnings.append(ff["error"])
    if not files:
        warnings.append("Nenhum arquivo suportado encontrado na pasta de entrada.")
    return {"input_dir": str(inp), "output_dir": str(out) if out else None,
            "files": [{k: v for k, v in f.items() if k != "path"} for f in files],
            "skipped": skipped,
            "counts": {"image": sum(f["kind"] == "image" for f in files),
                       "video": sum(f["kind"] == "video" for f in files),
                       "skipped": len(skipped)},
            "next_index": next_index(out), "warnings": warnings}


def start_job(input_dir: str, output_dir: str, *, recursive: bool = False,
              metadata_mode: str = "strip", metadata: dict | None = None,
              pixel_noise: bool = True, noise_strength: int = 1) -> str:
    if not output_dir or not str(output_dir).strip():
        raise ServiceError("Escolha a pasta de saída.")
    if metadata_mode not in ("strip", "replace"):
        raise ServiceError("Modo de metadados inválido (use 'strip' ou 'replace').")
    try:
        strength = int(noise_strength)
    except (TypeError, ValueError):
        raise ServiceError("Intensidade do ruído deve ser 1, 2 ou 3.") from None
    if not 1 <= strength <= 3:
        raise ServiceError("Intensidade do ruído deve ser 1, 2 ou 3.")
    meta = {k: str((metadata or {}).get(k) or "").strip()[:2000] for k in METADATA_FIELDS}

    inp, out = _dirs(input_dir, output_dir)
    files, _ = _scan(inp, out, bool(recursive))
    if not files:
        raise ServiceError("Nenhum arquivo suportado encontrado na pasta de entrada.")
    if jobs.active_for_output(str(out)):
        raise ConflictError("Já existe um processamento em andamento para essa pasta de saída.")
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ServiceError(f"Não foi possível criar a pasta de saída: {e}") from e
    opts = {"metadata_mode": metadata_mode, "metadata": meta,
            "pixel_noise": bool(pixel_noise), "noise_strength": strength}
    return jobs.submit(files, inp, out, opts)["job_id"]


def get_job(job_id: str) -> dict:
    j = jobs.get(job_id)
    if j is None:
        raise NotFoundError("Processamento não encontrado.")
    return j


def cancel_job(job_id: str) -> str:
    r = jobs.cancel(job_id)
    if r is None:
        raise NotFoundError("Processamento não encontrado.")
    return r["status"]


def is_finished(status: str) -> bool:
    return status in FINISHED_STATUSES


def wait_job(job_id: str, timeout: float | None = None, poll: float = 0.1) -> dict:
    """Block until the job finishes (tests/CLI only; never call on the GUI thread)."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        j = get_job(job_id)
        if is_finished(j["status"]):
            return j
        if deadline is not None and time.monotonic() > deadline:
            raise ServiceError("Tempo esgotado aguardando o processamento.")
        time.sleep(poll)


def open_folder(path: str) -> None:
    p = os.path.realpath(os.path.expanduser(str(path)))
    if not os.path.isdir(p):
        raise ServiceError(f"Pasta não existe: {p}")
    try:
        if sys.platform == "win32":
            os.startfile(p)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **subprocess_kwargs(text=False))
        else:
            subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **subprocess_kwargs(text=False))
    except Exception as e:  # noqa: BLE001
        raise ServiceError(f"Não foi possível abrir a pasta: {e}") from e


def shutdown(timeout: float = 5.0) -> None:
    """Cancel active jobs and wait briefly so ffmpeg is killed and temp files are removed."""
    active = [j["job_id"] for j in jobs.list_jobs() if j["status"] in ("queued", "running")]
    for jid in active:
        jobs.cancel(jid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(is_finished(jobs.get(j)["status"]) for j in active if jobs.get(j)):
            return
        time.sleep(0.05)
