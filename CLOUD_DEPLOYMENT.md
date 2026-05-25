# Cloud Deployment Guide — Greenpack Inspector

You chose **"accessible anywhere via browser."** This guide gives you several
ways to put the app online, from easiest to most control. All of them give you a
URL like `https://greenpack.yourcompany.com` that any operator can open from any
device — no install needed on their side.

The Docker image already includes everything for **full features in the cloud**:
Tesseract (Arabic + English), PDF rendering, and barcode decoding — so unlike the
local desktop run, **barcodes and Arabic OCR work out of the box** in the cloud.

---

## Option A — Render.com (easiest, free tier available)

1. Put this project in a GitHub repository.
2. Go to https://render.com → **New → Blueprint**.
3. Point it at your repo. Render reads `render.yaml` and builds the Docker image.
4. Wait ~5 min. You get a URL like `https://greenpack-inspector.onrender.com`.
5. Share that URL with your operators.

*Notes:* the free tier sleeps after inactivity (first request takes ~30s to wake).
For production use the **Standard** plan (always-on, more RAM for large scans).

---

## Option B — Railway / Fly.io (similar, container-based)

Both detect the `Dockerfile` automatically.

**Railway:** https://railway.app → New Project → Deploy from GitHub repo → it
builds the Dockerfile → generates a public URL.

**Fly.io:**
```bash
fly launch          # detects Dockerfile, asks a few questions
fly deploy          # builds & deploys
fly open            # opens the URL
```

---

## Option C — Any server you own (VPS, office server, AWS/Azure VM)

Requires Docker installed on the machine.

```bash
# 1. Copy this folder to the server
# 2. Build & run
docker compose up --build -d

# 3. It's now serving on port 8000
#    http://<server-ip>:8000
```

To give it a proper domain + HTTPS, put it behind a reverse proxy (Caddy is the
simplest — it gets HTTPS certificates automatically):

```caddyfile
# Caddyfile
greenpack.yourcompany.com {
    reverse_proxy localhost:8000
}
```
```bash
caddy run
```

---

## Option D — Without Docker (plain Python on a server)

```bash
pip install -r requirements.txt
pip install PyMuPDF pdf2image pyzbar pytesseract   # optional features
# install system libs:  tesseract-ocr tesseract-ocr-ara poppler-utils libzbar0
cd backend
uvicorn server:app --host 0.0.0.0 --port 8000
```

For production, run it under a process manager (systemd, supervisor, or
`gunicorn -k uvicorn.workers.UvicornWorker`).

---

## Configuration (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `PORT` | 8000 | Port to listen on (cloud platforms set this) |
| `GREENPACK_MAX_UPLOAD_MB` | 40 | Max upload size per file |
| `GREENPACK_JOB_TTL` | 3600 | Seconds before finished jobs are auto-deleted |
| `GREENPACK_CORS` | `*` | Allowed origins (set to your domain in production) |
| `GREENPACK_WORK` | temp dir | Where job files are stored |

---

## Security notes for cloud use

- The app has **no login** by default. For a public URL, either:
  - put it behind your company VPN / firewall, or
  - add HTTP basic-auth at the reverse proxy (Caddy/Nginx one-liner), or
  - ask me to add a simple login page.
- Set `GREENPACK_CORS` to your actual domain instead of `*` in production.
- Uploaded files and results auto-delete after `GREENPACK_JOB_TTL` seconds.

---

## Recommended setup for your case

For a packaging company with operators in different places:

1. **Render Standard** or a small **VPS + Docker + Caddy** (≈$7–15/month).
2. Custom domain `greenpack.yourcompany.com` with automatic HTTPS.
3. Basic-auth or VPN so only your team can reach it.
4. Operators just open the URL, drag the master + sample, get the report.

Tell me which option you want and I'll tailor the exact config (including a login
page if you want one).
