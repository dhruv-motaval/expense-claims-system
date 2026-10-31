# Expense Claims System

A three-role expense-claim workflow system with **AI-powered receipt extraction** and **tiered receipt storage**.
Staff submit claims with photo/PDF receipts, managers review, and Finance pays — with duplicate-proofing,
frozen revision windows and a full audit trail on every step.

Built with **FastAPI + SQLAlchemy + PostgreSQL** on the back end and **Next.js 15 + Tailwind** on the front end.

---

## Table of contents

- [Workflow](#workflow)
- [Key features](#key-features)
- [Receipt storage tiers](#receipt-storage-tiers)
- [Duplicate-proofing](#duplicate-proofing)
- [Quick start](#quick-start)
- [Demo accounts](#demo-accounts)
- [Configuration](#configuration)
- [API overview](#api-overview)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Security notes](#security-notes)

---

## Workflow

```
STAFF                     MANAGER                      FINANCE
─────                     ───────                      ───────
DRAFT ── submit ─▶ SUBMITTED ── approve ─▶ APPROVED ── pay ─▶ PAID (final)
                      │                       │
                      │ send-back (reason +   │ return-to-manager (reason)
                      ▼ flagged fields)       ▼
                   SENT_BACK              SUBMITTED (back in manager's queue)
                      │
                      │ staff edits & resubmits
                      ▼
                   SUBMITTED
```

Rules enforced **server-side** (not just in the UI):

- Staff can only edit their own claims while `DRAFT` / `SENT_BACK`; every other state is frozen (attempts are audited).
- Finance **never contacts staff directly** — an approved claim can only be pulled back to its manager.
- Users cannot approve or pay their own claims.
- Payments to a possible duplicate require a typed justification (stored on the audit trail).

## Key features

- **AI receipt extraction** — upload an image/PDF and the receipt text is parsed into structured fields
  (merchant, receipt no, date, time, category, amount) via the Google **Gemini** API
  (`gemini-3.5-flash-lite` by default). Graceful error mapping for missing/invalid API key, quota
  exhaustion and restricted models.
- **Categorized work queues**
  - Manager dashboard: **↩ Returned by finance — your re-review** (with the finance note shown inline)
    and **🆕 New requests — waiting for approval** as separate tables.
  - Finance dashboard: **Payment queue** with per-row *Mark as Paid* and *Return to manager*.
- **Lazy receipt viewer** — ticket pages load all details in **one** call; the receipt **file bytes are only
  fetched on demand**. "Show original receipt" opens a fullscreen viewer with
  zoom (buttons / wheel / keys, 1×–10×), drag-to-pan, page navigation for multi-page PDFs,
  and download with the original filename. Open/close/reopen as many times as you like.
- **Ticket details where they're needed** — every claim row carries its receipt's location
  (`receipt_file`, `receipt_id`), filename, size and storage tier; manager snapshot summaries
  (spend vs limit, projected spend, near-limit warnings) update across dashboards.
- **Audit trail** — every action writes a ticket: submissions, approvals, rejections, send-backs,
  edit attempts on frozen claims, payment blocks, duplicate dismissals and receipt file access.
- **Monthly limits** — per-user limits with live spend bars and `NEAR_LIMIT` / `OVER_LIMIT` badges.

## Receipt storage tiers

| Tier | Where | When |
|------|-------|------|
| **HOT** (active) | local `storage/` dir or MinIO | Claims in review; chips show 🧾 **hot** |
| **COLD** (archived) | S3 with `STANDARD_IA` | After payment; lifecycle-managed |

The active file location always travels with the claim data (`receipt_file` on every claim payload),
so dashboards, viewers and exports never need a second lookup. Paid receipts additionally get a
time-limited copy in the MinIO `paid-cache/` bucket (git-ignored, runtime-only).

Local hot storage is integrity-protected: every file's SHA-256 is stored in the DB and verified when restored.

## Duplicate-proofing

Four detection tiers run against **paid** claims:

| Tier | Basis | Behaviour |
|------|-------|-----------|
| **A** | identical canonical file (SHA-256) | hard payment block |
| **B** | same normalized receipt number | hard payment block |
| **C** | same merchant + date + amount | payment blocked, pay requires a ≥20-char justification (compare anchors shown log-style) |
| **D** | fingerprint similarity (reworded duplicates) | advisory warning chips everywhere + eligibility notes |

Normalization rules (merchant naming, receipt numbers, times, dates) are applied **on write**, so the
anchors themselves stay clean.

## Quick start

### 1. Backend (FastAPI)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux: source .venv/bin/activate)
pip install -r requirements.txt

# create backend/.env from the template
cp .env.example .env            # then edit (see Configuration below)

# start the API (creates tables and serves on http://localhost:8000)
uvicorn app.main:app --reload
```

The default `DATABASE_URL` expects PostgreSQL at `localhost:5432` (`expense_claims` schema).

### 2. Demo data

```bash
cd backend
.venv\Scripts\python.exe -m app.seed
```

Seeds five demo users (password `password123` — demo only, no real auth in scope),
monthly limits and a handful of claims.

### 3. Frontend (Next.js)

```bash
cd frontend
npm install
npm run dev            # http://localhost:3000 (proxies /api → http://localhost:8000)
```

Production build:

```bash
npm run build && npm start
```

## Demo accounts

All demo accounts use the password `password123`.

| Name | Email | Role | Monthly limit |
|------|-------|------|---------------|
| Riya Patel | `riya@example.com` | STAFF | ₹20,000 |
| Neha Shah | `neha@example.com` | STAFF (near limit) | ₹20,000 |
| Vikram Joshi | `vikram@example.com` | STAFF | ₹15,000 |
| Arjun Mehta | `arjun@example.com` | MANAGER | ₹25,000 |
| Priya Desai | `priya@example.com` | FINANCE | ₹50,000 |

The login page has one-click buttons for each demo account.

## Configuration

Backend environment variables (`backend/.env`, see `.env.example`):

| Variable | Purpose | Example |
|----------|---------|---------|
| `AI_API_KEY` | Google Gemini API key for receipt extraction (**never commit**) | |
| `AI_MODEL` | model to use | `gemini-3.5-flash-lite` |
| `DATABASE_URL` | SQLAlchemy URL | `postgresql+psycopg://...` (SQLite also supported) |
| `STORAGE_BACKEND` | `local` or `s3` | `local` |
| `STORAGE_DIR` | local hot-storage root | `../storage` |
| `MINIO_ENDPOINT` / `MINIO_*` | MinIO (hot S3-compatible storage) | `http://localhost:9000` |
| `S3_*` | AWS S3 (cold archive) config + storage class | `STANDARD_IA` |

Frontend: `BACKEND_URL` (defaults to `http://localhost:8000`) rewrites `/api` in `next.config.js`.

## API overview

FastAPI docs are generated automatically: **http://localhost:8000/docs**

```
POST /api/auth/login,{logout},login/get-me
GET  /api/claims                     # list (role-filtered)
GET  /api/claims/{id}                # full ticket: fields, receipt pointer + OCR data,
                                     # feedback, full audit, duplicate analysis
GET  /api/claims/{id}/receipt-data   # lazy: extracted data + OCR text
GET  /api/claims/{id}/audit          # lazy: full audit with payloads
GET  /api/claims/{id}/duplicates     # lazy: duplicates + anchor comparison
POST /api/claims / PATCH /api/claims/{id}
POST /api/claims/{id}/submit|approve|reject|send-back|return-to-manager|return-to-draft|pay
POST /api/receipts/extract           # text receipt → extract
POST /api/receipts/upload            # image/PDF receipt → extract
GET  /api/receipts/image/{id}        # authorized original file streaming (logged)
GET  /api/receipts/pages/{id}        # authorized page list for PDFs
GET  /api/dashboard/staff|manager|finance
GET  /api/reports                    # month + category aggregation
```

All endpoints enforce role rules server-side; the UI merely mirrors them.

## Testing

```bash
cd backend
.venv\Scripts\python.exe -m pytest -q
```

**72 tests** cover gate rules (edit windows, role logic, self-approval blocks), payment tiers A–C,
send-back/return flows, integrity & hashing, PDF artifact handling and lifecycle happy paths.
The suite never touches real storage — tests run against an isolated temp directory.

## Project structure

```
expense-claims-system/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, /api routers
│   │   ├── api.py             # HTTP endpoints (transparent + thin responsibility)
│   │   ├── services.py        # business rules & workflows
│   │   ├── extractors.py      # Gemini call and error handling
│   │   ├── ocr.py             # scanned-PDF → text fallback (Tesseract, optional)
│   │   ├── pdf_processor.py   # PDF rendering to per-page images
│   │   ├── storage.py         # HOT/COLD tiered storage (local / MinIO / S3)
│   │   ├── duplicates.py      # malformed receipt-no handling, anchor comparison, tiers
│   │   ├── receipt_cache.py   # paid-cache queue with TTL
│   │   ├── models.py          # SQLAlchemy models (claims, receipts, users, audit)
│   │   ├── seed.py            # demo data
│   │   └── schemas.py         # request/response validation (pydantic)
│   ├── tests/                 # 72 gated tests
│   └── .env / .env.example
├── frontend/
│   ├── app/                   # Next.js App Router pages
│   │   ├── claims/[id]        # ticket detail + lazy receipt viewer
│   │   └── dashboard/         # staff / manager / finance queues
│   ├── components/            # claim tables, receipt viewer, UI primitives
│   └── lib/auth.ts            # token handling, fetch wrapper
├── docs/                      # design & implementation status notes
└── storage/                   # runtime HOT storage (git-ignored)
```

## Security notes

- **Never commit API keys or credentials.** `backend/.env` is git-ignored; only `.env.example` is tracked.
- `storage/` is git-ignored — real receipts never enter version control.
- Authentication in this repo is **demo-only** (single demo password); JWT/session hardening is out of scope here — production use would require real auth, HTTPS and Postgres-backed sessions before rollout.
- Receipt files are served exclusively through authorized endpoints that check role/ownership and log each access (`RECEIPT_FILE_ACCESSED`).
- Receipt file paths are validated against the storage root (no path traversal).
- PDF previews render sandboxed (no script execution).

---

*Built as a demo/QA project. Amounts, merchants and "receipts" flow through realistic but synthetic
sample documents; seeded data marks itself as demo/test data.*
