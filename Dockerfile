# ===========================================================================
#  Greenpack Inspector — Cloud container image
# ===========================================================================
#  Includes the system libraries that the optional features need, so in the
#  cloud everything works: Tesseract (eng+ara), poppler (PDF), zbar (barcode).
# ===========================================================================
FROM python:3.12-slim

# --- System dependencies for optional features ---
#   tesseract-ocr + ara/eng  -> OCR word naming (Arabic + English)
#   poppler-utils            -> PDF rendering fallback (pdf2image)
#   libzbar0                 -> barcode decoding (fixes the libzbar crash)
#   libgl1 / libglib         -> OpenCV runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-ara \
        tesseract-ocr-eng \
        poppler-utils \
        libzbar0 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Python dependencies (include the optional ones in cloud) ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir PyMuPDF pdf2image pyzbar pytesseract

# --- App code ---
COPY backend/ ./backend/
COPY frontend/ ./frontend/

ENV GREENPACK_WORK=/tmp/greenpack_jobs \
    GREENPACK_MAX_UPLOAD_MB=40 \
    GREENPACK_CORS=* \
    PORT=8000

EXPOSE 8000
WORKDIR /app/backend

# Cloud platforms inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
