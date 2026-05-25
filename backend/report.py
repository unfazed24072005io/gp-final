"""
report.py — PDF report builder for the web backend (crash-proof)
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("greenpack.report")


def build_pdf(job_id: str, jdir: Path, result: Dict[str, Any],
              master_name: str, sample_name: str) -> Optional[str]:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle, Image as RLImage, HRFlowable)
    import cv2

    out = jdir / "report.pdf"
    NAVY = colors.HexColor("#0D1B2A"); CYAN = colors.HexColor("#00C2CB")
    GREEN = colors.HexColor("#16A34A"); RED = colors.HexColor("#DC2626")
    AMBER = colors.HexColor("#D97706")
    LIGHT = colors.HexColor("#F0F6FF"); SILVER = colors.HexColor("#E2E8F0")

    def ps(n, **k):
        d = dict(fontName="Helvetica", fontSize=10, leading=14); d.update(k)
        return ParagraphStyle(n, **d)

    h1 = ps("h1", fontName="Helvetica-Bold", fontSize=13, textColor=NAVY)
    body = ps("body", fontSize=9.5, leading=13)

    v = result["verdict"]; sc = result["scores"]
    col = result["colour"]; txt = result["text"]; ssim = result["ssim"]
    ocr = result["ocr"]; bc = result["barcodes"]
    mq = result["master_quality"]; sq = result["sample_quality"]

    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=1.5*cm,
                            rightMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    S = []

    S.append(Table([[Paragraph('<font color="#FFFFFF"><b>GREENPACK INSPECTOR — INSPECTION REPORT</b></font>',
              ps("t", fontSize=15, alignment=TA_CENTER, textColor=colors.white))]],
              colWidths=[A4[0]-3*cm],
              style=TableStyle([("BACKGROUND",(0,0),(-1,-1),NAVY),
                                ("TOPPADDING",(0,0),(-1,-1),12),("BOTTOMPADDING",(0,0),(-1,-1),12)])))
    S.append(Spacer(1, 12))

    # Verdict
    passed = v["pass"]
    vcol = GREEN if passed else RED
    vtxt = "PASS — APPROVED FOR PRINT" if passed else "FAIL — REVIEW REQUIRED"
    S.append(Table([[Paragraph(f'<font color="#FFFFFF"><b>{vtxt}</b></font>',
              ps("v", fontSize=17, alignment=TA_CENTER, textColor=colors.white))]],
              colWidths=[A4[0]-3*cm],
              style=TableStyle([("BACKGROUND",(0,0),(-1,-1),vcol),
                                ("TOPPADDING",(0,0),(-1,-1),9),("BOTTOMPADDING",(0,0),(-1,-1),9)])))
    S.append(Spacer(1, 6))
    S.append(Paragraph("Reason(s): " + "; ".join(v["reasons"]), body))
    if v.get("quality_note"):
        S.append(Paragraph(f'<font color="#D97706"><i>{v["quality_note"]}</i></font>', body))
    S.append(Spacer(1, 12))

    # Summary
    S.append(Paragraph("Job Summary", h1))
    S.append(HRFlowable(width="100%", thickness=1, color=CYAN)); S.append(Spacer(1, 6))
    rows = [["Master:", master_name, "Date:", datetime.now().strftime("%Y-%m-%d %H:%M")],
            ["Sample:", sample_name, "Overall score:", f'{sc["overall"]}/100'],
            ["Alignment:", f'{int(result["alignment_confidence"]*100)}%',
             "OCR engine:", ocr.get("engine", "n/a")]]
    t = Table(rows, colWidths=[2.6*cm, 6.4*cm, 3.5*cm, 4*cm])
    t.setStyle(TableStyle([("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),
                           ("FONTNAME",(2,0),(2,-1),"Helvetica-Bold"),
                           ("FONTSIZE",(0,0),(-1,-1),9),
                           ("ROWBACKGROUNDS",(0,0),(-1,-1),[colors.white,LIGHT]),
                           ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    S.append(t); S.append(Spacer(1, 12))

    # Input quality
    S.append(Paragraph("Input Quality", h1))
    S.append(HRFlowable(width="100%", thickness=1, color=CYAN)); S.append(Spacer(1, 6))
    qrows = [["", "Master", "Sample"],
             ["Grade", mq["grade"].upper(), sq["grade"].upper()],
             ["Score", f'{mq["score"]}/100', f'{sq["score"]}/100'],
             ["Sharpness", str(mq["sharpness"]), str(sq["sharpness"])]]
    qt = Table(qrows, colWidths=[5*cm, 5.75*cm, 5.75*cm])
    qt.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),NAVY),("TEXTCOLOR",(0,0),(-1,0),colors.white),
                            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),
                            ("FONTSIZE",(0,0),(-1,-1),9),("GRID",(0,0),(-1,-1),0.3,SILVER),
                            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,LIGHT]),
                            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    S.append(qt)
    S.append(Spacer(1, 3))
    S.append(Paragraph(f'<i>{sq["recommendation"]}</i>', ps("r", fontSize=8.5, textColor=colors.HexColor("#555"))))
    S.append(Spacer(1, 12))

    # Scores
    S.append(Paragraph("Scores", h1))
    S.append(HRFlowable(width="100%", thickness=1, color=CYAN)); S.append(Spacer(1, 6))
    score_keys = [k for k in ("text","colour","ssim","barcode","overall") if k in sc]
    head = [k.capitalize() for k in score_keys]
    vals = [f'{sc[k]}' for k in score_keys]
    stbl = Table([head, vals], colWidths=[(A4[0]-3*cm)/len(score_keys)]*len(score_keys))
    stbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),NAVY),("TEXTCOLOR",(0,0),(-1,0),colors.white),
                              ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("ALIGN",(0,0),(-1,-1),"CENTER"),
                              ("GRID",(0,0),(-1,-1),0.3,SILVER),("FONTSIZE",(0,0),(-1,-1),9),
                              ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    S.append(stbl); S.append(Spacer(1, 12))

    # Findings
    S.append(Paragraph("Findings", h1))
    S.append(HRFlowable(width="100%", thickness=1, color=CYAN)); S.append(Spacer(1, 6))
    findings = [
        f"<b>Colour:</b> median ΔE {col['median_delta_e']}, max {col['max_delta_e']}, "
        f"{col['zone_failures']}/{col['zone_count']} zones out of tolerance (ΔE>{col['threshold']}).",
        f"<b>Text (pixel/dot):</b> {txt['total']} ink differences "
        f"({txt['dots']} dot-level; {txt['missing']} missing, {txt['extra']} extra). Classes: {txt['by_class']}.",
        f"<b>OCR words:</b> " + (f"{len(ocr['errors'])} mismatch(es) [{ocr['engine']}]."
                                  if ocr.get("available") else ocr.get("note","unavailable")),
        f"<b>Structure (SSIM):</b> {ssim['ssim']} — {len(ssim['defects'])} structural defect(s).",
        f"<b>Barcodes:</b> " + ("match" if bc.get("match") else "mismatch" if bc.get("match") is False
                                 else bc.get("note","unavailable")),
    ]
    for f in findings:
        S.append(Paragraph("•  " + f, body)); S.append(Spacer(1, 3))
    S.append(Spacer(1, 10))

    # Overlay image
    ov = jdir / "overlay.png"
    if ov.exists():
        img = cv2.imread(str(ov))
        iw = A4[0]-3*cm
        ih = iw * img.shape[0] / img.shape[1]
        max_h = A4[1]-6*cm
        if ih > max_h:
            ih = max_h; iw = ih * img.shape[1] / img.shape[0]
        S.append(Paragraph("Difference overlay (grey=same, green=added, red=deleted):", body))
        S.append(Spacer(1, 4))
        S.append(RLImage(str(ov), width=iw, height=ih))

    S.append(Spacer(1, 10))
    S.append(Paragraph("<i>Generated by Greenpack Inspector v4.0. Pixel-level text "
                       "inspection covers Arabic & English by shape; install the Arabic "
                       "Tesseract pack to also name Arabic words.</i>",
                       ps("note", fontSize=7.5, textColor=colors.HexColor("#666"))))

    doc.build(S)
    log.info("Report written: %s", out)
    return str(out)
