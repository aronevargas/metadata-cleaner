"""Background job runner. One worker thread; jobs run sequentially, files sequentially."""
import os
import queue
import secrets
import threading
import time
from pathlib import Path

import numpy as np

from . import media
from .paths import next_index, publish_exclusive, tmp_path

_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_cancel: dict[str, threading.Event] = {}
_q: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
MAX_KEPT = 50


def _public(job: dict) -> dict:
    j = {k: v for k, v in job.items() if not k.startswith("_")}
    j["results"] = [{k: v for k, v in r.items() if not k.startswith("_")} for r in job["results"]]
    return j


def get(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return _public(j) if j else None


def list_jobs() -> list[dict]:
    with _lock:
        return [{k: j[k] for k in ("job_id", "status", "total", "processed", "output_dir")}
                for j in _jobs.values()]


def active_for_output(out: str) -> bool:
    with _lock:
        key = os.path.normcase(out)
        return any(os.path.normcase(j["output_dir"]) == key and j["status"] in ("queued", "running") for j in _jobs.values())


def submit(files: list[dict], inp: Path, out: Path, opts: dict) -> dict:
    job_id = secrets.token_hex(6)
    job = {
        "job_id": job_id, "status": "queued", "total": len(files), "processed": 0,
        "succeeded": 0, "failed": 0, "current": None, "current_progress": None,
        "input_dir": str(inp), "output_dir": str(out), "error": None,
        "started_at": None, "finished_at": None, "created_at": time.time(),
        "results": [{"source": f["rel_path"], "kind": f["kind"], "output": None, "ok": None,
                     "error": None, "status": "pending", "_path": f["path"], "_ext": f["ext"]}
                    for f in files],
        "_opts": opts,
    }
    with _lock:
        _jobs[job_id] = job
        _cancel[job_id] = threading.Event()
        # prune old finished jobs
        done = [k for k, j in _jobs.items() if j["status"] not in ("queued", "running")]
        for k in done[:-MAX_KEPT] if len(done) > MAX_KEPT else []:
            _jobs.pop(k, None)
            _cancel.pop(k, None)
    _ensure_worker()
    _q.put(job_id)
    return _public(job)


def cancel(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        if not j:
            return None
        if j["status"] in ("queued", "running"):
            _cancel[job_id].set()
            if j["status"] == "queued":
                _finish_cancel(j)
        return {"job_id": job_id, "status": j["status"]}


def _finish_cancel(j: dict) -> None:  # caller holds lock
    for r in j["results"]:
        if r["status"] in ("pending", "processing"):
            r["status"] = "skipped"
            r["error"] = "cancelado"
    j["status"] = "cancelled"
    j["current"] = None
    j["finished_at"] = time.time()


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="metaedit-worker", daemon=True)
            _worker.start()


def _remove_tmp(tmp: Path) -> None:
    """Delete a temp file; on Windows a just-killed ffmpeg/antivirus may hold it briefly."""
    for attempt in range(20):
        try:
            os.unlink(tmp)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.1 * (attempt + 1) if attempt < 5 else 0.5)
        except OSError:
            return


def _loop() -> None:
    while True:
        job_id = _q.get()
        try:
            _run(job_id)
        except Exception as e:  # noqa: BLE001  never let the worker die
            with _lock:
                j = _jobs.get(job_id)
                if j:
                    j["status"], j["error"], j["finished_at"] = "error", f"Erro interno: {e}", time.time()


def _run(job_id: str) -> None:
    with _lock:
        j = _jobs.get(job_id)
        if not j or j["status"] != "queued":
            return
        j["status"], j["started_at"] = "running", time.time()
        ev = _cancel[job_id]
        opts = j["_opts"]
    out = Path(j["output_dir"])
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        with _lock:
            j["status"], j["error"], j["finished_at"] = "error", f"Não foi possível criar a pasta de saída: {e}", time.time()
        return
    rng = np.random.default_rng()
    idx = next_index(out)

    for r in j["results"]:
        if ev.is_set():
            break
        with _lock:
            r["status"] = "processing"
            j["current"], j["current_progress"] = r["source"], 0.0
        src, ext = Path(r["_path"]), r["_ext"].lower()
        tmp = tmp_path(out, ext)
        try:
            if not src.is_file():
                raise media.MediaError("Arquivo de origem não existe mais.")
            if src.stat().st_size == 0:
                raise media.MediaError("Arquivo vazio.")
            common = dict(metadata_mode=opts["metadata_mode"], metadata=opts["metadata"],
                          pixel_noise=opts["pixel_noise"], noise_strength=opts["noise_strength"])
            if r["kind"] == "image":
                media.process_image(src, tmp, ext, rng=rng, **common)
            else:
                def prog(f, _j=j):
                    with _lock:
                        _j["current_progress"] = round(f, 3)
                media.process_video(src, tmp, ext, cancel=ev, progress=prog, **common)
            name, n = publish_exclusive(tmp, out, idx, ext)
            idx = n + 1
            with _lock:
                r.update(status="done", ok=True, output=name)
                j["succeeded"] += 1
        except media.Cancelled:
            with _lock:
                r.update(status="skipped", ok=False, error="cancelado")
            break
        except media.MediaError as e:
            with _lock:
                r.update(status="error", ok=False, error=str(e))
                j["failed"] += 1
        except Exception as e:  # noqa: BLE001
            with _lock:
                r.update(status="error", ok=False, error=f"Erro inesperado: {e}")
                j["failed"] += 1
        finally:
            _remove_tmp(tmp)
            with _lock:
                if r["status"] in ("done", "error"):
                    j["processed"] += 1

    with _lock:
        if ev.is_set():
            _finish_cancel(j)
        else:
            j["status"], j["current"], j["current_progress"] = "done", None, None
            j["finished_at"] = time.time()
