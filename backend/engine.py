"""
engine.py — Greenpack Inspector core inspection engine (web backend)
=====================================================================
Crash-proof, dependency-tolerant inspection engine.

DESIGN PRINCIPLE: every optional capability (barcode decoding, OCR, PDF
rendering) is wrapped so that a missing system library NEVER crashes the
inspection. If pyzbar/libzbar is missing, barcode checking is silently skipped
and reported as "unavailable" — exactly the bug shown in the screenshot.

Public entry point:  run_inspection(master_path, sample_path, config) -> dict
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger("greenpack.engine")

# ---------------------------------------------------------------------------
# Optional dependency probes — done once, never crash
# ---------------------------------------------------------------------------
def _probe(import_fn) -> bool:
    try:
        import_fn()
        return True
    except Exception as exc:  # ImportError, OSError (missing DLL), etc.
        log.warning("Optional dependency unavailable: %s", exc)
        return False


def _has_pyzbar() -> bool:
    def _imp():
        from pyzbar import pyzbar  # noqa
    return _probe(_imp)


def _has_tesseract() -> bool:
    def _imp():
        import pytesseract
        pytesseract.get_tesseract_version()
    return _probe(_imp)


def _has_fitz() -> bool:
    def _imp():
        import fitz  # noqa
    return _probe(_imp)


def _has_pdf2image() -> bool:
    def _imp():
        import pdf2image  # noqa
    return _probe(_imp)


CAPS = {
    "barcode": _has_pyzbar(),
    "ocr": _has_tesseract(),
    "pdf_fitz": _has_fitz(),
    "pdf_poppler": _has_pdf2image(),
}


def capabilities() -> Dict[str, bool]:
    """Return which optional features are available on this machine."""
    return dict(CAPS)


# ===========================================================================
# Input loading & quality
# ===========================================================================
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
PDF_EXTS = {".pdf"}


def load_image(path: str | Path, dpi: int = 300,
               max_dim: int = 2600) -> List[np.ndarray]:
    """Load PDF or image into a list of BGR pages. Crash-proof."""
    path = Path(path)
    ext = path.suffix.lower()
    pages: List[np.ndarray] = []

    if ext in PDF_EXTS:
        if CAPS["pdf_fitz"]:
            try:
                import fitz
                doc = fitz.open(str(path))
                zoom = dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)
                for pg in doc:
                    pix = pg.get_pixmap(matrix=mat, alpha=False)
                    img = np.frombuffer(pix.samples, np.uint8).reshape(
                        pix.height, pix.width, pix.n)
                    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) if pix.n == 3 \
                        else cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
                    pages.append(img)
                doc.close()
            except Exception as exc:
                log.error("fitz render failed: %s", exc)
        if not pages and CAPS["pdf_poppler"]:
            try:
                from pdf2image import convert_from_path
                for p in convert_from_path(str(path), dpi=dpi):
                    pages.append(cv2.cvtColor(np.array(p), cv2.COLOR_RGB2BGR))
            except Exception as exc:
                log.error("pdf2image render failed: %s", exc)
        if not pages:
            raise RuntimeError(
                "Cannot read PDF — no PDF engine available. Upload a PNG/JPG "
                "instead, or install PyMuPDF / poppler.")
    elif ext in IMAGE_EXTS:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"Could not read image file: {path.name}")
        pages.append(img)
    else:
        raise RuntimeError(f"Unsupported file type: {ext}")

    # Downscale for memory safety
    out = []
    for img in pages:
        h, w = img.shape[:2]
        if max(h, w) > max_dim:
            s = max_dim / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        out.append(img)
    return out


def assess_quality(img: np.ndarray) -> Dict[str, Any]:
    """Sharpness / brightness / resolution grade with operator guidance."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    bright = float(gray.mean())
    longest = max(img.shape[:2])

    score = 100
    warnings = []
    if sharp < 25:
        score -= 45; warnings.append("Very soft/blurry — fine text & dots unreliable.")
    elif sharp < 60:
        score -= 25; warnings.append("Somewhat soft — small-text accuracy reduced.")
    elif sharp < 120:
        score -= 10
    if longest < 1500:
        score -= 20; warnings.append("Low resolution — upload a higher-res scan.")
    elif longest < 2200:
        score -= 8
    if bright < 60 or bright > 245:
        score -= 10; warnings.append("Poor exposure (too dark/bright).")
    score = max(0, min(100, score))

    grade = ("excellent" if score >= 85 else "good" if score >= 65
             else "fair" if score >= 45 else "poor")
    rec = {
        "excellent": "Great input — full inspection incl. dot-level checks.",
        "good": "Good input — reliable for colour, text and most dot checks.",
        "fair": "Usable, but dot-level may have false flags. Flatbed 300 DPI is better.",
        "poor": "Low quality (likely phone capture). Colour & layout OK; re-scan on a flatbed at 300 DPI for text/dot accuracy.",
    }[grade]
    return {"sharpness": round(sharp, 1), "brightness": round(bright, 1),
            "resolution_px": longest, "score": score, "grade": grade,
            "warnings": warnings, "recommendation": rec}


# ===========================================================================
# Alignment (two-stage: ORB homography + optical-flow local warp)
# ===========================================================================
def align(master: np.ndarray, sample: np.ndarray) -> Tuple[np.ndarray, float]:
    gm = cv2.cvtColor(master, cv2.COLOR_BGR2GRAY)
    gs = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    h, w = gm.shape
    aligned, conf = None, 0.0
    try:
        orb = cv2.ORB_create(5000)
        k1, d1 = orb.detectAndCompute(gm, None)
        k2, d2 = orb.detectAndCompute(gs, None)
        if d1 is not None and d2 is not None and len(k1) > 10 and len(k2) > 10:
            bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
            matches = sorted(bf.match(d1, d2), key=lambda m: m.distance)
            good = matches[:max(30, len(matches) // 4)]
            if len(good) >= 15:
                src = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
                dst = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
                H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
                if H is not None:
                    aligned = cv2.warpPerspective(sample, H, (w, h),
                                                  borderMode=cv2.BORDER_REPLICATE)
                    conf = min(1.0, int(mask.sum()) / max(len(good), 1)) if mask is not None else 0.3
    except Exception as exc:
        log.warning("alignment failed: %s", exc)

    if aligned is None:
        aligned = cv2.resize(sample, (w, h), interpolation=cv2.INTER_LANCZOS4)
        conf = 0.05

    if conf > 0.15:
        try:
            ga = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
            flow = cv2.calcOpticalFlowFarneback(gm, ga, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            mag = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
            flow[mag > 25] = 0
            flow[..., 0] = cv2.GaussianBlur(flow[..., 0], (0, 0), 9)
            flow[..., 1] = cv2.GaussianBlur(flow[..., 1], (0, 0), 9)
            fmap = np.zeros((h, w, 2), np.float32)
            fmap[..., 0] = np.arange(w)
            fmap[..., 1] = np.arange(h)[:, None]
            fmap += flow
            aligned = cv2.remap(aligned, fmap, None, cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REPLICATE)
        except Exception as exc:
            log.warning("local warp skipped: %s", exc)
    return aligned, float(conf)


# ===========================================================================
# Colour analysis (Delta-E CIE2000, robust median, white-balance)
# ===========================================================================
def _match_wb(master, sample):
    def neutral(img):
        b, g, r = cv2.split(img.astype(np.float32))
        m = (r > 180) & (g > 180) & (b > 180)
        if m.sum() < 50:
            return np.array([b.mean(), g.mean(), r.mean()])
        return np.array([b[m].mean(), g[m].mean(), r[m].mean()])
    gain = np.clip(neutral(master) / np.maximum(neutral(sample), 1e-3), 0.5, 2.0)
    return np.clip(sample.astype(np.float32) * gain.reshape(1, 1, 3), 0, 255).astype(np.uint8)


def analyse_colour(master, sample, threshold=3.0) -> Dict[str, Any]:
    from skimage import color as skcolor
    sample = _match_wb(master, sample)
    rgb_m = cv2.cvtColor(master, cv2.COLOR_BGR2RGB).astype("float64") / 255.0
    rgb_s = cv2.cvtColor(sample, cv2.COLOR_BGR2RGB).astype("float64") / 255.0
    lab_m, lab_s = skcolor.rgb2lab(rgb_m), skcolor.rgb2lab(rgb_s)
    dE = skcolor.deltaE_ciede2000(lab_m, lab_s)

    h, w = dE.shape
    rows = cols = 4
    zones = []
    zh, zw = h // rows, w // cols
    for r in range(rows):
        for c in range(cols):
            patch = dE[r*zh:(r+1)*zh, c*zw:(c+1)*zw]
            if patch.size == 0:
                continue
            med = float(np.median(patch))
            p75 = float(np.percentile(patch, 75))
            zones.append({"median": round(med, 2), "p75": round(p75, 2),
                          "pass": med <= threshold and p75 <= threshold*2})
    fails = sum(1 for z in zones if not z["pass"])
    heat = cv2.applyColorMap(
        np.clip(dE / max(threshold*3, 1e-3) * 255, 0, 255).astype(np.uint8),
        cv2.COLORMAP_JET)
    return {"median_delta_e": round(float(np.median(dE)), 2),
            "mean_delta_e": round(float(np.mean(dE)), 2),
            "max_delta_e": round(float(np.max(dE)), 2),
            "zone_failures": fails, "zone_count": len(zones),
            "threshold": threshold, "heatmap": heat,
            "pass": float(np.median(dE)) <= threshold and fails <= len(zones)*0.4}


# ===========================================================================
# SSIM structural defects (illumination norm + colour diff + edge tolerance)
# ===========================================================================
def detect_defects(master, sample, threshold=0.90, min_area=200) -> Dict[str, Any]:
    from skimage.metrics import structural_similarity as ssim
    gm = cv2.cvtColor(master, cv2.COLOR_BGR2GRAY)
    gs = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    rm, rs = gm.mean(), gm.std()
    if gs.std() > 1e-3:
        gs = np.clip((gs.astype(np.float32)-gs.mean())*(rs/gs.std())+rm, 0, 255).astype(np.uint8)
    score, diff = ssim(gm, gs, full=True, data_range=255)
    diff_u8 = ((1.0 - diff) * 255).astype(np.uint8)
    lab_m = cv2.cvtColor(master, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab_s = cv2.cvtColor(sample, cv2.COLOR_BGR2LAB).astype(np.float32)
    cdist = np.sqrt((lab_m[...,1]-lab_s[...,1])**2 + (lab_m[...,2]-lab_s[...,2])**2)
    diff_u8 = cv2.max(diff_u8, np.clip(cdist*2, 0, 255).astype(np.uint8))
    _, th = cv2.threshold(diff_u8, 50, 255, cv2.THRESH_BINARY)
    edges = cv2.dilate(cv2.Canny(gm, 50, 150),
                       cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    th = cv2.bitwise_and(th, cv2.bitwise_not(edges))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, k)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, k)
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    defects = []
    for c in cnts:
        a = cv2.contourArea(c)
        if a < min_area:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        defects.append({"bbox": [int(x), int(y), int(bw), int(bh)],
                        "area": int(a), "severity": "high" if a > min_area*10 else "medium"})
    defects.sort(key=lambda d: -d["area"])
    return {"ssim": round(float(score), 4), "defects": defects,
            "pass": float(score) >= threshold}


# ===========================================================================
# Text / word / dot inspection (pixel-level, OCR-independent)
# ===========================================================================
def _binarise(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, 25, 12)


def inspect_text(master, sample, dpi=300, rows=4, cols=3) -> Dict[str, Any]:
    h, w = master.shape[:2]
    if sample.shape[:2] != (h, w):
        sample = cv2.resize(sample, (w, h))
    px_mm = dpi / 25.4
    th, tw = h // rows, w // cols
    diffs = []
    for r in range(rows):
        for c in range(cols):
            y0, y1 = r*th, (r+1)*th if r < rows-1 else h
            x0, x1 = c*tw, (c+1)*tw if c < cols-1 else w
            mt, st = master[y0:y1, x0:x1], sample[y0:y1, x0:x1]
            im, isamp = _binarise(mt), _binarise(st)
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
            miss = cv2.bitwise_and(im, cv2.bitwise_not(cv2.dilate(isamp, k)))
            extra = cv2.bitwise_and(isamp, cv2.bitwise_not(cv2.dilate(im, k)))
            k2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
            miss = cv2.morphologyEx(miss, cv2.MORPH_OPEN, k2)
            extra = cv2.morphologyEx(extra, cv2.MORPH_OPEN, k2)
            for mask, kind in ((miss, "missing"), (extra, "extra")):
                n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
                for i in range(1, n):
                    a = int(stats[i, cv2.CC_STAT_AREA])
                    if a < 4:
                        continue
                    bw, bh = int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT])
                    cls = ("dot" if a <= 30 else "mark" if a <= 150
                           else "stroke" if a <= 800 else "word_block")
                    diffs.append({"kind": kind, "class": cls,
                                  "bbox": [int(stats[i, cv2.CC_STAT_LEFT])+x0,
                                           int(stats[i, cv2.CC_STAT_TOP])+y0, bw, bh],
                                  "area": a, "size_mm": round(max(bw, bh)/px_mm, 2)})
    diffs.sort(key=lambda d: -d["area"])
    by_class: Dict[str, int] = {}
    for d in diffs:
        by_class[d["class"]] = by_class.get(d["class"], 0) + 1
    return {"total": len(diffs), "dots": by_class.get("dot", 0),
            "missing": sum(1 for d in diffs if d["kind"] == "missing"),
            "extra": sum(1 for d in diffs if d["kind"] == "extra"),
            "by_class": by_class, "differences": diffs[:300]}


def ocr_compare(master, sample) -> Dict[str, Any]:
    """OCR word comparison — only if Tesseract is available, else skipped safely."""
    if not CAPS["ocr"]:
        return {"available": False, "errors": [], "engine": "none",
                "note": "Tesseract not installed — text checked at pixel level instead."}
    try:
        import pytesseract
        from pytesseract import Output
        langs = pytesseract.get_languages(config="")
        lang = "ara+eng" if ("ara" in langs and "eng" in langs) else \
               ("eng" if "eng" in langs else (langs[0] if langs else ""))

        def words(img):
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            d = pytesseract.image_to_data(g, lang=lang, output_type=Output.DICT)
            return [w.strip() for w in d["text"] if w.strip()]

        import difflib
        mw, sw = words(master), words(sample)
        sm = difflib.SequenceMatcher(None, mw, sw)
        errors = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            errors.append({"type": tag.upper(),
                           "master": " ".join(mw[i1:i2]),
                           "sample": " ".join(sw[j1:j2])})
        return {"available": True, "engine": f"tesseract({lang})",
                "arabic": "ara" in langs, "errors": errors,
                "master_word_count": len(mw), "sample_word_count": len(sw)}
    except Exception as exc:
        log.warning("OCR failed: %s", exc)
        return {"available": False, "errors": [], "engine": "error", "note": str(exc)}


# ===========================================================================
# Barcode (OPTIONAL — the crash fix). Never raises.
# ===========================================================================
def compare_barcodes(master, sample) -> Dict[str, Any]:
    if not CAPS["barcode"]:
        return {"available": False,
                "note": "Barcode scanning unavailable (libzbar not installed). "
                        "Skipped safely — install the ZBar DLL to enable.",
                "match": None, "master_count": 0, "sample_count": 0}
    try:
        from pyzbar import pyzbar
        def decode(img):
            return [{"data": c.data.decode("utf-8", "replace"), "type": c.type}
                    for c in pyzbar.decode(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))]
        mc, sc = decode(master), decode(sample)
        mv = sorted(c["data"] for c in mc)
        sv = sorted(c["data"] for c in sc)
        return {"available": True, "master": mc, "sample": sc,
                "match": mv == sv, "master_count": len(mc), "sample_count": len(sc),
                "scannable": len(sc) >= len(mc)}
    except Exception as exc:
        log.warning("barcode decode failed: %s", exc)
        return {"available": False, "note": f"Barcode check error: {exc}",
                "match": None, "master_count": 0, "sample_count": 0}


# ===========================================================================
# Overlay + scoring + verdict
# ===========================================================================
def build_overlay(master, aligned, colour, threshold=3.0) -> np.ndarray:
    h, w = master.shape[:2]
    gm = cv2.cvtColor(master, cv2.COLOR_BGR2GRAY)
    ga = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    base = cv2.cvtColor((gm*0.5+200*0.5).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    _, im = cv2.threshold(gm, 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    _, ia = cv2.threshold(ga, 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    dele = cv2.bitwise_and(im, cv2.bitwise_not(ia))
    add = cv2.bitwise_and(ia, cv2.bitwise_not(im))
    base[dele > 0] = (0, 0, 230)
    base[add > 0] = (0, 200, 0)
    return base


def _score(colour, ocr, text, ssim_r, barcodes, dot_reliable) -> Dict[str, float]:
    zf = colour["zone_failures"] / max(colour["zone_count"], 1)
    colour_s = max(0.0, 100.0 - zf*100)
    pen = 0.0
    if ocr.get("available"):
        for e in ocr["errors"]:
            pen += {"REPLACE": 15, "DELETE": 12, "INSERT": 10}.get(e["type"], 10)
    for d in text["differences"]:
        if not dot_reliable and d["class"] == "dot":
            continue  # ignore dot noise on poor scans
        pen += {"word_block": 8, "stroke": 3, "mark": 1.5, "dot": 1}.get(d["class"], 1)
    text_s = max(0.0, 100.0 - pen)
    ssim_s = max(0.0, min(100.0, ssim_r["ssim"]*100))
    # barcode score only counts if available
    if barcodes.get("available"):
        barcode_s = 100.0 if barcodes["match"] else 40.0
        weights = (0.33, 0.30, 0.20, 0.12, 0.05)
        comps = (text_s, colour_s, ssim_s, barcode_s, 100.0)
    else:
        barcode_s = None
        weights = (0.40, 0.35, 0.25)
        comps = (text_s, colour_s, ssim_s)
    overall = sum(w*c for w, c in zip(weights, comps))
    out = {"overall": round(max(0, min(100, overall)), 1),
           "colour": round(colour_s, 1), "text": round(text_s, 1),
           "ssim": round(ssim_s, 1)}
    if barcode_s is not None:
        out["barcode"] = round(barcode_s, 1)
    return out


def run_inspection(master_path: str, sample_path: str,
                   config: Optional[Dict[str, Any]] = None,
                   progress: Optional[Callable[[str, int], None]] = None) -> Dict[str, Any]:
    """Full inspection. Returns a JSON-serialisable dict (no numpy arrays except
    overlay/heatmap which are popped by the caller)."""
    cfg = config or {}
    dpi = cfg.get("dpi", 300)
    de_thr = cfg.get("delta_e_threshold", 3.0)
    ssim_thr = cfg.get("ssim_threshold", 0.90)
    maxd = cfg.get("max_dim", 2600)

    def step(msg, pct):
        log.info(msg)
        if progress:
            progress(msg, pct)

    step("Loading master…", 5)
    m_pages = load_image(master_path, dpi, maxd)
    step("Loading sample…", 12)
    s_pages = load_image(sample_path, dpi, maxd)
    master, sample = m_pages[0], s_pages[0]

    step("Assessing input quality…", 18)
    mq, sq = assess_quality(master), assess_quality(sample)
    dot_reliable = min(mq["score"], sq["score"]) >= 60

    step("Aligning sample to master…", 30)
    aligned, conf = align(master, sample)

    step("Analysing colour (ΔE CIE2000)…", 45)
    colour = analyse_colour(master, aligned, de_thr)

    step("Reading text (OCR)…", 58)
    ocr = ocr_compare(master, aligned)

    step("Inspecting words, letters & dots…", 68)
    text = inspect_text(master, aligned, dpi)

    step("Detecting structural defects (SSIM)…", 78)
    ssim_r = detect_defects(master, aligned, ssim_thr)

    step("Verifying barcodes…", 86)
    barcodes = compare_barcodes(master, aligned)

    step("Scoring…", 92)
    scores = _score(colour, ocr, text, ssim_r, barcodes, dot_reliable)

    # verdict
    critical = 0
    reasons = []
    big = sum(1 for d in text["differences"] if d["class"] == "word_block")
    if big and dot_reliable:
        critical += big; reasons.append(f"{big} missing/extra word block(s)")
    if ocr.get("available") and ocr["errors"]:
        critical += len(ocr["errors"]); reasons.append(f"{len(ocr['errors'])} OCR text difference(s)")
    if barcodes.get("available") and barcodes["match"] is False:
        critical += 1; reasons.append("Barcode mismatch")
    if colour["zone_failures"] > colour["zone_count"]*0.4:
        reasons.append("Widespread colour shift")
    passed = critical <= cfg.get("max_critical", 0) and scores["overall"] >= 80
    verdict = {"pass": passed, "critical": critical,
               "dot_reliable": dot_reliable,
               "reasons": reasons or ["All checks within tolerance"]}
    if not dot_reliable:
        verdict["quality_note"] = (
            "Input quality is " + sq["grade"] + " — dot/letter findings may include "
            "false positives. Re-scan on a flatbed at 300 DPI to confirm.")

    step("Building overlay…", 97)
    overlay = build_overlay(master, aligned, colour, de_thr)

    step("Done.", 100)
    return {
        "capabilities": capabilities(),
        "master_quality": mq, "sample_quality": sq,
        "alignment_confidence": round(conf, 3),
        "colour": {k: v for k, v in colour.items() if k != "heatmap"},
        "ocr": ocr, "text": text, "ssim": ssim_r, "barcodes": barcodes,
        "scores": scores, "verdict": verdict,
        "_overlay": overlay, "_heatmap": colour["heatmap"],
        "_master": master, "_aligned": aligned,
    }
