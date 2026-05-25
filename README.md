# Greenpack Inspector v4.0 — Web App

A full-stack, browser-based pre-press inspection tool. Compares an approved
**master** against a **scanned sample** and detects colour shifts, missing/changed
text (English **and** Arabic), missing dots on letters, missing graphics, barcode
mismatches and structural defects — then produces a downloadable PDF report.

This replaces the old tkinter desktop window with a modern web interface, and —
critically — it is **crash-proof**: missing optional libraries (the
`libzbar-64.dll` barcode error you hit) no longer crash the inspection. They are
detected, skipped, and clearly marked **N/A** in the UI and report.

---

## What's fixed vs the old desktop version

| Problem in the screenshot | Fix in v4.0 |
|---------------------------|-------------|
| `libzbar-64.dll` not found → whole app crashes | Barcode is **optional**; wrapped in try/except, skipped safely, shown as N/A |
| Bare tkinter UI | Modern responsive web UI (drag-drop, live progress, visual results) |
| Hard dependency on every library | OCR, PDF engine, barcode are all optional and probed at startup |
| One window, no detail | Verdict banner, score cards, side-by-side overlay + heatmap, detail tables, PDF download |

---

## Architecture (full-stack)

```
Browser (frontend/index.html)         <- modern single-page UI, no build step
        |  HTTP/JSON
        v
FastAPI server (backend/server.py)    <- REST API, background jobs, file uploads
        |
        v
Inspection engine (backend/engine.py) <- align, colour ΔE, OCR, pixel text/dots,
                                          SSIM, barcode (optional), scoring
        |
        v
Report builder (backend/report.py)    <- ReportLab PDF
```

---

## Quick start (Windows)

1. Install **Python 3.12** (64-bit) from python.org — tick "Add to PATH".
   *(Avoid Python 3.14 for now — some libraries don't have wheels yet.)*
2. Double-click **`run.bat`**.
   - First run installs dependencies automatically.
   - It starts the server and opens your browser at `http://127.0.0.1:8000`.
3. Drag in the **master** and **sample**, click **Start Inspection**.

### Mac / Linux
```bash
chmod +x run.sh
./run.sh
```

---

## Optional features (the app works without them)

| Feature | Needs | If missing |
|---------|-------|-----------|
| **PDF input** | `PyMuPDF` (pip) or poppler+`pdf2image` | Upload PNG/JPG instead |
| **OCR word naming** | Tesseract engine (+ `ara` for Arabic) | Text still checked at pixel/dot level |
| **Barcode check** | `pyzbar` + ZBar DLL | Skipped, marked N/A — **no crash** |

Install Tesseract (with Arabic): https://github.com/UB-Mannheim/tesseract/wiki

---

## What it checks (A → Z)

- **Colour** — ΔE CIE2000 per pixel, robust per-zone pass/fail, heatmap
- **Text** — Tesseract OCR word diff (EN/AR) **plus** OCR-independent pixel-level
  word / letter / **single-dot** detection (works for Arabic and English)
- **Structure** — SSIM with illumination normalisation, colour-diff channel, edge tolerance
- **Barcodes** — decode + compare (optional, never crashes)
- **Alignment** — two-stage ORB homography + optical-flow local warp (handles skew/phone scans)
- **Input quality** — sharpness/resolution grading with operator guidance

---

## Files

```
greenpack_web/
  run.bat / run.sh              # one-click launcher
  requirements.txt
  backend/
    server.py                   # FastAPI app + REST API + job queue
    engine.py                   # crash-proof inspection engine
    report.py                   # PDF report builder
  frontend/
    index.html                  # the whole web UI (no build step needed)
```

---

## Best file to upload (for the operator)

**Flatbed-scanner PNG or TIFF at 300 DPI** gives the best results, including
single-dot Arabic/English checks. Phone-app scans (iScanner) work for colour and
layout but are soft/skewed — the app grades every upload and tells the operator
when to re-scan.

---

## API (for integration)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/health` | status + capabilities |
| POST | `/api/inspect` | upload master+sample, returns `job_id` |
| GET | `/api/result/{job_id}` | poll progress / get result JSON |
| GET | `/api/overlay/{job_id}` | difference overlay PNG |
| GET | `/api/heatmap/{job_id}` | colour heatmap PNG |
| GET | `/api/report/{job_id}` | download PDF report |

---

© Greenpack. Offline pre-press quality control.
