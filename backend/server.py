"""
server.py — Greenpack Inspector web backend (FastAPI)
=====================================================
Cloud-ready: configurable host/port via env, CORS, upload size limits,
automatic job cleanup, and a /healthz endpoint for cloud load balancers.

Serves the single-page web UI and exposes a small JSON API:

  GET  /                       -> the web app (index.html)
  GET  /healthz                -> liveness probe (cloud)
  GET  /api/health             -> server + capability status
  POST /api/inspect            -> upload master + sample, run inspection
  GET  /api/result/{job_id}    -> poll job status / result
  GET  /api/overlay/{job_id}   -> difference overlay PNG
  GET  /api/heatmap/{job_id}   -> colour heatmap PNG
  GET  /api/report/{job_id}    -> downloadable PDF report

Jobs run on a background thread so the UI gets live progress via polling.
Crash-proof: any engine error is captured and returned as a failed job, never
a 500 that kills the page.

Run locally:  uvicorn server:app --host 127.0.0.1 --port 8000
Run in cloud: uvicorn server:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict

import cv2

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import engine
import report as report_mod

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("greenpack.server")

BASE = Path(__file__).resolve().parent
FRONTEND = BASE.parent / "frontend"
WORK = Path(os.environ.get("GREENPACK_WORK", Path(tempfile.gettempdir()) / "greenpack_jobs"))
WORK.mkdir(parents=True, exist_ok=True)

# Config from environment (cloud-friendly)
MAX_UPLOAD_MB = int(os.environ.get("GREENPACK_MAX_UPLOAD_MB", "40"))
JOB_TTL_SECONDS = int(os.environ.get("GREENPACK_JOB_TTL", "3600"))   # auto-clean after 1h
ALLOW_ORIGINS = os.environ.get("GREENPACK_CORS", "*").split(",")

app = FastAPI(title="Greenpack Inspector", version="4.1")

# CORS — needed if the frontend is served from a different domain/CDN in cloud
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store  {job_id: {...}}
JOBS: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Housekeeping: delete old jobs so the server doesn't fill up in the cloud
# ---------------------------------------------------------------------------
def _cleanup_loop():
    while True:
        try:
            now = time.time()
            for jid in list(JOBS.keys()):
                created = JOBS[jid].get("created", now)
                if now - created > JOB_TTL_SECONDS:
                    JOBS.pop(jid, None)
                    shutil.rmtree(WORK / jid, ignore_errors=True)
        except Exception as exc:
            log.warning("cleanup error: %s", exc)
        time.sleep(300)


threading.Thread(target=_cleanup_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Background inspection worker
# ---------------------------------------------------------------------------
def _run_job(job_id: str, master_path: str, sample_path: str, cfg: Dict[str, Any]):
    job = JOBS[job_id]
    try:
        def progress(msg: str, pct: int):
            job["progress"] = pct
            job["message"] = msg

        result = engine.run_inspection(master_path, sample_path, cfg, progress=progress)

        # Save overlay + heatmap + report to the job dir
        jdir = WORK / job_id
        jdir.mkdir(exist_ok=True)
        cv2.imwrite(str(jdir / "overlay.png"), result.pop("_overlay"))
        cv2.imwrite(str(jdir / "heatmap.png"), result.pop("_heatmap"))
        master_img = result.pop("_master")
        aligned_img = result.pop("_aligned")

        # Build PDF report (crash-proof)
        try:
            pdf_path = report_mod.build_pdf(job_id, jdir, result,
                                            job["master_name"], job["sample_name"])
            result["report_available"] = bool(pdf_path)
        except Exception as exc:
            log.error("Report build failed: %s", exc)
            result["report_available"] = False

        job["result"] = result
        job["status"] = "done"
        job["progress"] = 100
    except Exception as exc:
        log.exception("Job %s failed", job_id)
        job["status"] = "error"
        job["error"] = str(exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    idx = FRONTEND / "index.html"
    if idx.exists():
        return HTMLResponse(idx.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Greenpack Inspector</h1><p>frontend/index.html missing</p>")


@app.get("/healthz")
def healthz():
    """Liveness probe for cloud load balancers / orchestrators."""
    return {"status": "ok"}


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "4.1", "capabilities": engine.capabilities()}


@app.post("/api/inspect")
async def inspect(master: UploadFile = File(...), sample: UploadFile = File(...),
                  delta_e_threshold: float = Form(3.0),
                  ssim_threshold: float = Form(0.90),
                  dpi: int = Form(300)):
    job_id = uuid.uuid4().hex[:12]
    jdir = WORK / job_id
    jdir.mkdir(parents=True, exist_ok=True)

    max_bytes = MAX_UPLOAD_MB * 1024 * 1024

    def _save(upload: UploadFile, name: str) -> str:
        ext = Path(upload.filename or "").suffix.lower() or ".png"
        dest = jdir / f"{name}{ext}"
        size = 0
        with open(dest, "wb") as fh:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413,
                        f"File too large (max {MAX_UPLOAD_MB} MB). "
                        f"Reduce the scan resolution or split the file.")
                fh.write(chunk)
        return str(dest)

    try:
        master_path = _save(master, "master")
        sample_path = _save(sample, "sample")
    except HTTPException:
        shutil.rmtree(jdir, ignore_errors=True)
        raise

    JOBS[job_id] = {"status": "running", "progress": 0, "message": "Queued…",
                    "created": time.time(),
                    "master_name": master.filename, "sample_name": sample.filename}

    cfg = {"delta_e_threshold": delta_e_threshold,
           "ssim_threshold": ssim_threshold, "dpi": dpi}
    threading.Thread(target=_run_job, args=(job_id, master_path, sample_path, cfg),
                     daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/result/{job_id}")
def result(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    if job["status"] == "running":
        return {"status": "running", "progress": job["progress"],
                "message": job["message"]}
    if job["status"] == "error":
        return {"status": "error", "error": job["error"]}
    return {"status": "done", "result": job["result"]}


@app.get("/api/overlay/{job_id}")
def overlay(job_id: str):
    p = WORK / job_id / "overlay.png"
    if not p.exists():
        raise HTTPException(404, "No overlay")
    return FileResponse(str(p), media_type="image/png")


@app.get("/api/heatmap/{job_id}")
def heatmap(job_id: str):
    p = WORK / job_id / "heatmap.png"
    if not p.exists():
        raise HTTPException(404, "No heatmap")
    return FileResponse(str(p), media_type="image/png")


@app.get("/api/report/{job_id}")
def report(job_id: str):
    p = WORK / job_id / "report.pdf"
    if not p.exists():
        raise HTTPException(404, "No report")
    return FileResponse(str(p), media_type="application/pdf",
                        filename=f"Greenpack_Report_{job_id}.pdf")


# Serve any static assets (if added later)
if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")
