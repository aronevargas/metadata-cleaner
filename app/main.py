"""LEGACY (optional) FastAPI server: JSON API under /api plus web UI from app/static/.

The default app is the Tkinter desktop window (app.desktop). Both use app.services."""
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, jobs, picker, services

STATIC_DIR = Path(__file__).parent / "static"


class Metadata(BaseModel):
    title: str = Field("", max_length=500)
    artist: str = Field("", max_length=500)
    comment: str = Field("", max_length=2000)
    copyright: str = Field("", max_length=500)
    software: str = Field("", max_length=200)


class FolderReq(BaseModel):
    title: str = Field("Escolha uma pasta", max_length=200)
    initial: str | None = None


class ScanReq(BaseModel):
    input_dir: str
    output_dir: str | None = None
    recursive: bool = False


class JobReq(BaseModel):
    input_dir: str
    output_dir: str
    recursive: bool = False
    metadata_mode: Literal["strip", "replace"] = "strip"
    metadata: Metadata = Field(default_factory=Metadata)
    pixel_noise: bool = True
    noise_strength: int = Field(1, ge=1, le=3)


def create_app(port: int = 8765) -> FastAPI:
    app = FastAPI(title="Metadata Editor", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    app.state.allowed_hosts = allowed_hosts

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Defends against DNS rebinding and cross-site requests from other web pages.
        host = request.headers.get("host", "")
        if host not in app.state.allowed_hosts:
            return JSONResponse({"detail": "Host não permitido."}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin not in {f"http://{h}" for h in app.state.allowed_hosts}:
            return JSONResponse({"detail": "Origem não permitida."}, status_code=403)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return JSONResponse({"detail": "Content-Type deve ser application/json."}, status_code=415)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp


    def call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except services.NotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except services.ConflictError as e:
            raise HTTPException(409, str(e)) from e
        except services.ServiceError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/health")
    def health():
        h = services.health()
        return {"ok": True, **h,
                "picker": {"supported": picker.method() is not None, "method": picker.method()}}

    @app.post("/api/dialog/folder")
    async def dialog_folder(req: FolderReq):
        return await run_in_threadpool(picker.pick, req.title, req.initial)

    @app.post("/api/scan")
    def do_scan(req: ScanReq):
        return call(services.scan, req.input_dir, req.output_dir, req.recursive)

    @app.post("/api/jobs", status_code=202)
    def create_job(req: JobReq):
        job_id = call(services.start_job, req.input_dir, req.output_dir, recursive=req.recursive,
                      metadata_mode=req.metadata_mode, metadata=req.metadata.model_dump(),
                      pixel_noise=req.pixel_noise, noise_strength=req.noise_strength)
        j = services.get_job(job_id)
        return {"job_id": job_id, "status": j["status"], "total": j["total"]}

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": jobs.list_jobs()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return call(services.get_job, job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        return {"job_id": job_id, "status": call(services.cancel_job, job_id)}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True, check_dir=False), name="static")

    return app

