# Expense Claims System

An internal expense-claims application: staff file claims from pasted receipt text or receipt
images, managers approve their team's claims, finance pays and tracks monthly spend against
per-employee limits — with tamper-evident JSON audit tickets, a two-gate send-back workflow,
tiered duplicate-payment prevention and tiered receipt storage (hot MinIO → paid-cache → cold S3).

## Features

- Demo login with three roles: Staff, Manager, Finance
- Claim creation with receipt **text extraction** (deterministic parser, always available)
  and **image extraction** (Gemini vision API, optional; API key sent via `x-goog-api-key` header)
- **Duplicate-proof anchors**: every claim carries Receipt No. + transaction Time (minute-truncated),
  normalized on write; duplicate checks match on identity, fuzzy resemblance only advises
- Explicit claim state machine (below) including a **manager send-back** and a
  **finance return-to-manager** gate; paid (and everything after approval) is frozen
- **JSON audit tickets**: every event (create/update/transition/edit-block/reference/file
  access/payment block/duplicate dismissal) is one JSON envelope written in the same DB
  transaction as the change, visible to everyone with claim access, forever
- **Tiered duplicate-payment prevention**: tier A (same file bytes) and tier B (same receipt no.)
  hard-block; tier C (same merchant+date+amount) demands a typed justification; tier D never blocks
- **Tiered storage**: active receipts live on the hot tier (local disk or MinIO); at payment the
  canonical file is promoted (checksum-verified, idempotent, non-blocking) to S3 `STANDARD_IA`,
  with a 7-day `paid-cache/` copy on MinIO that a lifecycle rule expires
- **In-memory receipt cache**: TTL 1h + LRU 64MB for hot serving; authorization is never cached
- Monthly limits with `NORMAL / NEAR LIMIT / OVER LIMIT` status per employee
- Full dashboards for staff, manager (team queue + spend) and finance (payment queue with
  duplicate tier chips, spend analytics)

## Architecture

```
Browser (Next.js + Tailwind)  →  /api/* proxied via next.config.mjs rewrites
        │
        ▼
FastAPI backend  ──►  SQLAlchemy  ──►  PostgreSQL (docker compose / or any SQL DB)
   │
   ├── extractors: deterministic text parser + optional Gemini vision extraction
   ├── duplicates: identity tiers A/B/C + fuzzy advisory (D)
   ├── storage:   TieredStorage(hot=LocalStorage|MinIO, cold=S3) + ReceiptCache
   └── audit:     one JSON ticket per event, same transaction as the change
```

Business rules live entirely in the backend (`app/services.py`, `app/duplicates.py`):
authorization, the state machine, duplicate tiers, monthly limits, storage routing.
The AI layer only turns unstructured receipt input into editable fields — it never decides
permissions, transitions, duplicates or payments.

## Claim state machine

```
DRAFT ──submit──► SUBMITTED ──manager──► APPROVED ──finance──► PAID (terminal)
  ▲                  │        │              │
  └── REJECTED ✓     │        └─ reject      └─ return-to-manager: APPROVED → SUBMITTED
  (REJECTED→DRAFT)   │           (manager only)
                     │
                     └─ send-back (manager only): SUBMITTED → SENT_BACK
SENT_BACK → DRAFT (owner, keep private) | SENT_BACK → SUBMITTED (owner, resubmit)
```

Capability matrix (server-enforced; the UI mirrors it):

| Phase | Staff owner | Manager | Finance |
|---|---|---|---|
| DRAFT | edit, submit | — | — |
| SUBMITTED | — (frozen) | approve / reject / **send back** | — |
| SENT_BACK ("Returned for revision") | edit, resubmit, back-to-draft | — | — |
| APPROVED | — (frozen) | — (inert until Finance acts) | pay / **return to manager** |
| PAID | view only | view only | view only |

Two integrity gates worth noting:

- **Finance never contacts staff directly.** After approval, feedback flows Finance → Manager
  (`return-to-manager` tickets `CLAIM_RETURNED_TO_MANAGER` with `assigned_to`), and only the
  Manager can push work back to staff (`send-back` → `SENT_BACK`).
- **A manager is inert after approving.** Approve/reject/send-back on an APPROVED claim are all
  refused — the claim is exactly what Finance will pay.

## Audit tickets

Every event is a JSON envelope in `audit_events.metadata`:

```json
{
  "actor": {"id": 4, "name": "Arjun Mehta", "role": "MANAGER"},
  "transition": {"from": "SUBMITTED", "to": "SENT_BACK"},
  "feedback": {"reason": "Amount exceeds receipt",
               "flagged_fields": ["amount", "merchant"],
               "comment": "Attach the correct receipt page"},
  "at": "2026-10-30T03:33:05+00:00",
  "claim_number": "CLM-1011"
}
```

Ticket types: `CLAIM_CREATED/UPDATED/SUBMITTED/APPROVED/REJECTED/PAID/SENT_BACK`,
`CLAIM_RETURNED_TO_MANAGER`, `CLAIM_EDIT_BLOCKED` (frozen-status edit attempts),
`RECEIPT_FILE_ACCESSED` / `COLD_TIER_ACCESSED` (every receipt view, authorized server-side),
`DUPLICATE_WARNING`, `PAYMENT_BLOCKED`, `DUPLICATE_DISMISSED` (tier C, with justification),
`RECEIPT_ARCHIVED`, `ARCHIVAL_FAILED`. Feedback lives in the ticket JSON — the latest
`CLAIM_SENT_BACK` / `CLAIM_RETURNED_TO_MANAGER` ticket *is* the current feedback.

## Duplicate-payment tiers

All matching is strict equality after exactly one normalization step
(receipt no upper-cased/un-punctuated/label-stripped; time minute-truncated, am/pm folded to
24h — `14:22:05` ≡ `14:22`, `2:30 pm` ≡ `14:30`). A missing anchor is "absent", never a mismatch.

| Tier | Fires when… | Effect |
|---|---|---|
| **A** | the claim's canonical file bytes (SHA-256) match an already-paid receipt | hard block, `PAYMENT_BLOCKED` |
| **B** | the normalized receipt number matches an already-paid receipt | hard block, `PAYMENT_BLOCKED` |
| **C** | merchant + date + amount all match a paid claim (receipt nos not both-present-equal) | Finance must type a ≥20-char justification → `DUPLICATE_DISMISSED`, then pays |
| **D** | weighted fuzzy similarity (merchant 35 / amount 30 / date 20 / description 15) ≥ 65 | advisory chip only — never blocks |

The daily-cab regression is covered by a test: five legit same-merchant same-amount claims on
five different days all pay freely.

## Tiered storage

```
upload ──► HOT tier (local disk, or MinIO hot bucket)          ← active claims serve from here
payment ──► promote (checksum-verified, idempotent, non-blocking)
              ├─► COLD: S3 STANDARD_IA  receipts/<id>/original.<ext>   ← archive of record
              └─► paid-cache: MinIO paid-cache/<id>/… , lifecycle-expired after 7 days
read (post-payment) ──► paid-cache hit? serve (ticket) : cold fetch (COLD_TIER_ACCESSED ticket)
```

- **Payment never depends on promotion.** Promotion runs *after* the pay transaction commits;
  a failed promote writes `ARCHIVAL_FAILED` and the payment stands. Retrying promote is safe.
- Served-from provenance (`hot` / `paid-cache` / `cold`) is visible in the file-access tickets.
- MinIO config: `MINIO_ENDPOINT`, `MINIO_ROOT_USER/PASSWORD`, `MINIO_BUCKET_NAME`;
  cold: `COLD_S3_BUCKET_NAME`, `S3_STORAGE_CLASS` (default `STANDARD_IA`), `PAID_CACHE_DAYS`.
  `docker compose up -d` starts the MinIO hot tier (db + minio).

### Receipt in-memory cache

Raw receipt bytes are cached in-process keyed on receipt id + checksum: TTL 1 hour, LRU-capped
at 64 MB (`RECEIPT_CACHE_TTL_SECONDS` / `RECEIPT_CACHE_MAX_BYTES`). Authorization is checked on
*every* request before the cache is consulted — the cache never grants access, and
`ReceiptCache.invalidate_receipt` drops all derived keys for a receipt. Dashboards and claims
data are never cached.

## Setup

Requirements: Docker (for Postgres + optional MinIO), Python 3.11+, Node 18+.

```bash
git clone <repo> && cd expense-claims

# 1. Database + MinIO hot tier
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

Without Docker, set `DATABASE_URL=sqlite:///./demo.db` in `backend/.env` and leave `MINIO_ENDPOINT`
empty — the hot tier becomes the local disk (paid-cache unavailable, promotion copy-to-cold only);
everything else works identically (that is what the automated tests use).

## Demo accounts

All demo accounts share the password `password123` (demo auth only — any non-empty password
is accepted; real auth is out of scope).

| Name         | Email               | Role    | Limit     |
|--------------|---------------------|---------|-----------|
| Riya Patel   | riya@example.com    | Staff   | ₹20,000   |
| Neha Shah    | neha@example.com    | Staff   | ₹20,000   |
| Vikram Joshi | vikram@example.com  | Staff   | ₹15,000   |
| Arjun Mehta  | arjun@example.com   | Manager | ₹25,000   |
| Priya Desai  | priya@example.com   | Finance | —         |

Neha is seeded at ₹18,700 approved spend (near limit). Riya has a **paid** ₹320 Uber claim from
12 Aug carrying receipt anchor `UB984213X`; Vikram holds a draft rewording of it ("Uber
Technologies / 12/08/26 / Airport ride / INR 320.00") with the **same receipt no** for the
tier-B demo.

### Suggested demo script

1. **Neha** logs in: monthly limit, current spend, recent claims.
2. Paste `UBER / 12 Aug 2026 14:22 / Trip to Airport / Rs 1,450 / Receipt No: UB984213X`,
   Extract — receipt no and time auto-fill; correct a field, Submit.
3. **Arjun** approves — then tries to edit/approve again: refused (frozen + inert).
4. Send-back demo: Arjun **sends a claim back** with `flagged_fields=[amount]`; Neha sees the
   orange "returned for revision" card, fixes the amount, resubmits (`resubmission_count: 2`).
5. Return-to-manager demo: Priya returns an approved claim with feedback; the ticket shows
   `assigned_to: Arjun Mehta`; Arjun re-reviews.
6. Tier-B duplicate demo: Vikram's reworded Uber draft — if approved, Priya's payment is
   **blocked (`PAYMENT_BLOCKED`, tier B)** with the anchor comparison in the ticket.
7. Tier-C demo: pay a claim that matches merchant+date+amount — Finance is asked for a typed
   justification; the justification and the anchor card land on the audit trail.
8. Post-payment: open the paid claim's receipt file — every view is ticketed
   (`RECEIPT_FILE_ACCESSED`), at every phase, for staff owner / manager / finance.

## Business decisions & assumptions

- **Currency**: INR only; **monthly period**: calendar month of the claim's transaction date.
- **Actual spend** = `APPROVED + PAID`; **projected** = `SUBMITTED` (shown separately). `SENT_BACK`
  claims count as neither (the test suite pins this).
- **Over-limit behavior**: claims are *not* blocked automatically; Finance sees a clear OVER LIMIT flag.
- **Frozen statuses**: `SUBMITTED` / `APPROVED` / `PAID` refuse edits server-side
  (`EDITABLE_FIELDS_BY_STATUS`); the attempt is ticketed (`CLAIM_EDIT_BLOCKED`).
- **CAS transitions**: every status change is a compare-and-swap
  (`UPDATE … WHERE status = <current>`); a concurrent approve/pay yields **409**, never a silent
  double transition.
- **Duplicate UX**: warn + audit at submission; only paid *identity* matches block payment.
- **Payments**: simulated by Finance (no gateway, per spec).
- **Rejected → draft is explicit** ("return to draft" action) so the audit trail shows the loop.
- **Auth**: HMAC token in localStorage + in-memory server sessions; demo-only. Sessions reset on
  backend restart. Password checking is intentionally skipped; authorization is not.
- **Receipts without AI**: paste-text extraction is fully deterministic and always works.
  Image upload requires `AI_API_KEY`; the UI directs the user to enter the claim manually otherwise.

## AI usage

- **What**: receipt OCR/interpretation. `app/extractors.py` calls Google's Gemini
  `generateContent` API (free tier, vision-capable `gemini-2.0-flash` default) with strict JSON
  prompt asking for merchant, date, **time**, amount, currency, **receipt_no**, description and
  category. The key travels in the `x-goog-api-key` header, never in the URL.
- **Where**: `backend/app/extractors.py`, wired in `backend/app/services.py`.
- **Never used for**: permissions, approval, payment, status transitions, spend math, duplicate
  enforcement — all deterministic backend code.
- To enable: set `AI_API_KEY` (free key from aistudio.google.com/apikey).

## Tests

```bash
cd backend
DATABASE_URL=sqlite:///:memory: python -m pytest
```

**71 tests** — the original 43 (business rules + workflows + PDF/IDOR regressions, all kept
passing) plus 28 new gate tests:

- `test_integrity.py` — anchor normalization on write, claim-update diffs in tickets,
  frozen statuses, typed reject, receipt file-access tickets, anchor extraction
- `test_sendback.py` — the full send-back loop, guardrails, inert manager + finance
  return-to-manager loop, spend math excludes `SENT_BACK`
- `test_payment_tiers.py` — tier A/B/C/D behavior, the daily-cab regression, CAS double-pay race
- `test_receipt_cache.py` — TTL, LRU, invalidation, auth semantics
- `test_storage_tiers.py` — promote verify/idempotence, tier routing + paid-cache fallback,
  lifecycle rule installation, payment-never-depends-on-promotion

All run on SQLite with local storage and mocked S3 — no AWS, no MinIO, no network.

## PDF receipts

PDF, JPEG, PNG and WebP receipts are accepted by the same upload endpoint. PDFs are validated by
signature, limited to 10 MB and `PDF_MAX_PAGES` pages, and processed with PyMuPDF: embedded text
is extracted and the accurate `page_count` recorded; page *rendering* is opt-in
(`render_pages`, used only by the legacy artifact flows). New uploads persist ONE private object
per receipt (`receipts/<receipt-id>/original.pdf` under either storage backend) plus the
structured `extracted_data` JSON in Postgres; extracted JSON (merchant, date, time, receipt_no,
amount, currency, description, category, extraction_status, page_count) lives in
`receipts.extracted_data`. Legacy receipts with persisted page images remain readable via
`/api/receipts/pages`. PDF preview uses the authorized original-PDF endpoint in a sandboxed
iframe; blob object URLs are revoked on reload/unmount.

Migrations run with `alembic upgrade head`. Recent migrations: `0df2dc126555` (receipt ownership +
S3 keys), `45cc8add3d05` (s3_pages_keys), `b2f90471b17c` (file metadata + extracted_data),
`a3f5c9d1e771` (claim/receipt receipt_no + trans_time anchors, receipts.storage_tier +
paid_cache_key). The full implemented/not-implemented ledger lives in
[docs/IMPLEMENTATION-STATUS.md](docs/IMPLEMENTATION-STATUS.md); the frozen architecture spec is
[docs/FINAL-DESIGN.md](docs/FINAL-DESIGN.md).
