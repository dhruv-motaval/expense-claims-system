# Expense Claims System

An internal expense-claims application: staff file claims from pasted receipt text or receipt
images, managers approve their team's claims, finance pays and tracks monthly spend against
per-employee limits.

## Features

- Demo login with three roles: Staff, Manager, Finance
- Claim creation with receipt **text extraction** (deterministic parser, always available)
  and **image extraction** (OpenAI-compatible vision API, optional)
- Editable extracted drafts — nothing is submitted automatically; the user reviews first
- Explicit claim state machine: `DRAFT → SUBMITTED → APPROVED/REJECTED → PAID` (paid is terminal)
- Server-enforced authorization: managers cannot approve their own claims or claims outside
  their team; staff cannot see others' claims
- Duplicate receipt detection via normalization + weighted similarity
  (merchant 35 / amount 30 / date 20 / description 15, strong ≥ 85, possible ≥ 65)
- Duplicate **payment** prevention: paying a claim matching an already-paid receipt is blocked
- Monthly limits with `NORMAL / NEAR LIMIT / OVER LIMIT` status per employee
- Finance dashboards: monthly spend, spend by category, spend by employee, payment queue
- Full audit trail per claim (created / extracted / submitted / approved / rejected / paid /
  duplicate warnings / payment blocked)
- Realistic seed data including a near-limit employee, a paid Uber receipt and a reworded
  duplicate of it

## Architecture

```
Browser (Next.js + Tailwind)  →  /api/* proxied via next.config.mjs rewrites
        │
        ▼
FastAPI backend  ──►  SQLAlchemy  ──►  PostgreSQL (docker compose / or any SQL DB)
   │
   ├── extractors: deterministic text parser + optional vision-LLM extraction
   └── storage/receipts/  (uploaded receipt images on local disk)
```

Business rules live entirely in the backend (`app/services.py`, `app/duplicates.py`):
authorization, the state machine, duplicate detection, monthly limits. The AI layer only
turns unstructured receipt input into editable fields — it never decides permissions,
transitions, duplicates or payments.

## Setup

Requirements: Docker (for Postgres), Python 3.11+, Node 18+.

```bash
git clone <repo> && cd expense-claims

# 1. Database
docker compose up -d

# 2. Backend
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows   (bash/macOS: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            # adjust DATABASE_URL if needed
alembic upgrade head
python -m app.seed

# 3. Frontend (new terminal)
cd ../frontend
npm install
npm run dev
```

Open http://localhost:3000 — API docs at http://localhost:8000/docs.

Without Docker, set `DATABASE_URL=sqlite:///./demo.db` in `backend/.env`; everything else
works identically (that is what the automated tests use).

## Demo accounts

All demo accounts share the password `password123` (demo auth only — any non-empty password
is accepted; real auth is out of scope, see decisions below).

| Name         | Email               | Role    | Limit     |
|--------------|---------------------|---------|-----------|
| Riya Patel   | riya@example.com    | Staff   | ₹20,000   |
| Neha Shah    | neha@example.com    | Staff   | ₹20,000  |
| Vikram Joshi | vikram@example.com  | Staff   | ₹15,000  |
| Arjun Mehta  | arjun@example.com   | Manager | ₹25,000  |
| Priya Desai  | priya@example.com   | Finance | —         |

Neha is seeded at ₹18,700 approved spend (near limit). Riya has a **paid** ₹320 Uber claim
from 12 Aug, and Vikram holds a draft of the same receipt reworded ("Uber Technologies /
12/08/26 / Airport ride / INR 320.00") for the duplicate demo.

### Suggested demo script (matches the spec scenario)

1. **Neha** logs in: monthly limit, current spend, recent claims.
2. Paste `UBER / <today> / Trip to Airport / Rs 1,450`, Extract, correct a field, Submit.
3. **Arjun** logs in: team claim pending; his own submitted claim shows self-approval is
   blocked (button hidden *and* server rejects it).
4. Approve Neha's claim → Neha is now ₹20,150 / ₹20,000 → **OVER LIMIT** on her dashboard
   and in Finance's employee table.
5. **Priya** logs in: dashboards, category spend, payment queue → Mark as Paid.
6. Submit the reworded Uber duplicate → **strong duplicate warning** (score ≥ 85);
   if it gets approved, Finance's payment is **blocked** with `PAYMENT_BLOCKED` audited.
7. Try approving or editing the paid claim → refused: paid is terminal.

## Business decisions & assumptions

- **Currency**: INR only (single-currency demo).
- **Monthly period**: calendar month of the claim's transaction date.
- **Actual spend** = `APPROVED + PAID` claims; **projected** = `SUBMITTED` (shown separately).
- **Over-limit behavior**: claims are *not* blocked automatically; Finance sees a clear
  OVER LIMIT flag (spec-recommended behavior).
- **Rejected claims**: the owner can move them back to DRAFT, edit and resubmit.
- **Duplicate scoring**: 35% merchant (normalized: case, punctuation, currency words and
  noise words like "Technologies/Pvt/Ltd" removed) + 30% amount (exact within ₹0.01) +
  20% date (exact) + 15% description (fuzzy). ≥ 85 = strong, 65–84 = possible, < 65 = none.
  These weights are implementation decisions from the build spec.
- **Duplicate UX**: warn + audit (`DUPLICATE_WARNING`), never silently block submission;
  but payment *is* blocked if the receipt matches an already-paid claim (audit `PAYMENT_BLOCKED`).
- **Payments**: simulated by Finance (no gateway, per spec).
- **State machine**: centralized in `ALLOWED_TRANSITIONS` (`app/services.py`); `PAID` is
  terminal — `DRAFT→SUBMITTED→APPROVED/REJECTED`, `REJECTED→DRAFT`, `APPROVED→PAID`.
- **Auth**: HMAC token in localStorage + in-memory server sessions; demo-only. Sessions
  reset on backend restart (by design for a demo). Password checking is intentionally
  skipped; authorization is *not* — every route re-derives the user from the token.
- **Receipts without AI**: paste-text extraction is fully deterministic and always works.
  Image upload requires `AI_API_KEY`; without it the UI tells the user to enter the claim
  manually (spec's offline strategy).

## AI usage

- **What**: receipt OCR/interpretation. `app/extractors.py` calls Google's Gemini
  `generateContent` API (free tier, vision-capable `gemini-3.6-flash` default) with the
  receipt image and a strict JSON prompt (spec §17). The response is validated with
  Pydantic; on any failure the user is told to enter the claim manually. Text extraction
  is pure regex/date parsing — no AI needed.
- **Where**: `backend/app/extractors.py::extract_from_image`, wired in
  `backend/app/services.py::extract_upload`.
- **Never used for**: permissions, approval, payment, status transitions, spend math,
  duplicate enforcement — all deterministic backend code.
- To enable: set `AI_API_KEY` (free key from aistudio.google.com/apikey) in
  `backend/.env`; optionally `AI_MODEL` (must be a vision-capable Gemini model).
  Provider notes: current Gemini models are only served via the native
  `generateContent` endpoint (the OpenAI-compat layer hangs on them), and Groq's free
  tier has no vision models, so image extraction requires a Gemini key.

## Tests

```bash
cd backend
pytest
```

11 tests cover the spec's §46 list: claim creation, text extraction (clean + messy receipts),
editable extraction, self-approval block, wrong-manager block, paid-is-terminal, duplicate
detection despite rewording, double-payment block, near-limit and over-limit math, and the
AI-unavailable fallback. Tests run on SQLite in-memory — no Postgres needed.

## What would be built next

- Email/Slack notifications on submit/approve/pay
- Per-company policy config (approval chains, category limits)
- Multi-currency with FX rates
- Delegated approvals (out-of-office)
- Image-hash duplicate matching for photographed receipts
- Real auth (hashed passwords, persistent sessions), CSV export, ERP/accounting export
