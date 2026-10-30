# Expense Claims System — Improvement Plan (Draft v1)

> **⚠️ SUPERSEDED — kept for history only.**
> This was the draft plan (v1). The agreed design was frozen in [FINAL-DESIGN.md](FINAL-DESIGN.md),
> built in the 5-phase order from [AGENT-BRIEF.md](AGENT-BRIEF.md), and implemented.
> The current implemented/not-implemented ledger lives in [IMPLEMENTATION-STATUS.md](IMPLEMENTATION-STATUS.md).
> Anything here that contradicts those two documents is wrong — the ledger wins.

> Status at time of writing: design agreed, not yet implemented.
> Scope: approval-integrity fix, send-back workflow, JSON ticket logging, receipt
> file visibility + verification tracking, hot/cold storage tiering.

---

## 1. Where we are (current state)

### Stack
```
Browser (Next.js + Tailwind) → /api/* proxied via next.config.mjs rewrites
        ▼
FastAPI backend → SQLAlchemy → PostgreSQL (docker compose) or SQLite (tests)
   ├── extractors: deterministic text parser + optional Gemini vision extraction
   └── storage: LocalStorage or S3Storage (boto3) for receipt files
```

### State machine (today)
```
DRAFT → SUBMITTED → APPROVED → PAID          (PAID terminal)
         SUBMITTED → REJECTED → DRAFT        (owner edit-then-resubmit loop)
```
Centralized in `ALLOWED_TRANSITIONS` (`backend/app/services.py:20-26`).

### What already works (keep as-is)
- Role model STAFF / MANAGER / FINANCE with manager-team self-FK hierarchy
- Server-side authorization on every route (`deps.py`), incl. IDOR tested
- Receipt access: owner + team-manager + finance (`can_view_receipt`, `services.py:72-83`)
- Upload with S3/private objects, checksum (SHA-256), presigned URLs, failure rollback
- Full claim lifecycle audited in `AuditEvent` (JSON `metadata` column)
- Duplicate detection (35/30/20/15 weighted) + duplicate-payment block
- Monthly limits with NORMAL / NEAR_LIMIT / OVER_LIMIT

---

## 2. Issues found in review (what we're fixing)

### 🔴 Correctness bugs
| # | Issue | Location |
|---|---|---|
| 1 | **Approved/SUBMITTED claims are editable by owner** — approval can be bypassed, Finance may pay an edited amount | `services.py:399-410` (only PAID blocks) |
| 2 | Currency regex without word boundaries misclassifies merchant lines ("cars" → amount line) | `extractors.py:12` |
| 3 | `_duplicates()` defined twice (second silently shadows first) | `services.py:242,248` |
| 4 | S3 `get_object`'s FileNotFoundError is swallowed by its own except (dead error contract) | `storage.py:169-176` |
| 5 | LocalStorage download URL `/storage/<key>` — no backend route serves it → legacy local receipts 404 | `storage.py:111-112`, `main.py` |
| 6 | PDF processor leaks MuPDF docs; does full render→JPEG work whose output is discarded; `page_count` from successful renders not `doc.page_count` | `pdf_processor.py:43,62-78`, `services.py:315` |
| 7 | Gemini API key in `?key=` query param (leaks to logs/proxies) | `extractors.py:87-92` |

### 🟠 Concurrency / transactions
- Claim number `max(id)+1000` race → duplicate claim_number 500s (`services.py:54-56`)
- Transition checks on snapshot with no row lock → double-pay race (`services.py:414-433`)
- Split commits: data change committed, audit row committed separately → audit gaps

### 🟡 Smaller items
- `SESSIONS` grows unbounded; tokens deterministic, never expire; no logout
- Size check after full upload read (`services.py:162-166`); unbounded pasted text
- No date/amount bounds; `RejectIn` schema dead code; `reject` takes raw dict
- `date.today()` local-timezone month bounds (`services.py:496`)
- `limit(200)` then Python-side filtering (dropped older claims) + N+1 queries
- `JSON().with_variant(JSONB)` on only one column; `s3_pages_keys`/`audit.meta` raw JSON
- README self-contradictions (PDF artifacts section vs current architecture section)
- Test gaps: assertion-free `test_storage.py`; S3 get_object / delete / prefix untested

---

## 3. Where we're going (target state)

### 3.1 Extended state machine

```
DRAFT ──submit──► SUBMITTED ──approve──► APPROVED ──pay──► PAID  (terminal)
   ▲                 │  │      │             │
   │                 │  │      └─SENT_BACK    └─► SUBMITTED   (Finance returns
   │                 │  │        (Manager sends      to Manager for re-review)
   │                 │  │         to Staff for
   │                 │  └─► REJECTED ─► DRAFT          (rejection loop, existing)
   │                 │      rework; owner
   │                 │        edits, resubmits)
   │                 └──── (Manager: approve / reject / send back)
   └────────── owner edits in DRAFT/REJECTED/SENT_BACK back
```

New capability transitions (added to `ALLOWED_TRANSITIONS`):
- `SUBMITTED → SENT_BACK` (Manager only, team-only, not own claim)
- `APPROVED → SUBMITTED` (`CLAIM_RETURNED_TO_MANAGER`, Finance only)
- `SENT_BACK → DRAFT`, `SENT_BACK → SUBMITTED` (owner)

**Authority rules:**
- Manager approves ⇒ claim leaves his authority. Post-approval the Manager is
  completely inert (read-only) until Finance either pays or pulls it back to his queue.
- Finance never contacts Staff directly; the Manager is the gate for Staff revision.
- Finance never edits claims; she approves/pay or returns to Manager.
- Who-can-do-what matrix:

| Action | Staff | Manager | Finance | Precondition |
|---|---|---|---|---|
| Edit fields | ✔ owner (DRAFT/REJECTED/SENT_BACK) | — | — | `EDITABLE_FIELDS_BY_STATUS` |
| Submit / resubmit | ✔ owner (DRAFT/SENT_BACK) | — | — | submit |
| Approve | — | ✔ SUBMITTED, team, not own | — | |
| Send back to Staff (`SENT_BACK`) | — | ✔ SUBMITTED, team, not own | — | |
| Reject | — | ✔ SUBMITTED, team, not own | — | reason required |
| Return to Manager | — | ✘ read-only after approve | ✔ APPROVED | feedback required |
| Pay | — | — | ✔ APPROVED + duplicate check | |

### 3.2 Edit integrity (the P0 fix)

`EDITABLE_FIELDS_BY_STATUS` centralized next to `ALLOWED_TRANSITIONS`:
```
DRAFT / REJECTED  → all fields
SUBMITTED / APPROVED / PAID / SENT_BACK-owner-frozen → see matrix above
```
`update_claim`:
1. owner check (existing)
2. attempted fields ⊆ editable-for-status, else 422 + audit `CLAIM_EDIT_BLOCKED`
   (with attempted fields and current status)
3. apply + `CLAIM_UPDATED` audit **with full JSON diff**, single transaction

### 3.3 JSON ticket logging standard

One `log_event(db, claim, actor, action, meta)` writes every event through the same
envelope, **in the same transaction** as the state change (kills split-commit gaps):

```json
{
  "actor":      { "id": 4, "name": "Arjun Mehta", "role": "MANAGER" },
  "action":     "CLAIM_SENT_BACK",
  "transition": { "from": "SUBMITTED", "to": "SENT_BACK" },
  "fields":     { "amount": { "old": 1450, "new": 1500 } },
  "feedback":   { "reason": "Amount exceeds receipt",
                  "flagged_fields": ["amount"],
                  "comment": "Re-attach correct receipt page" },
  "at":         "2026-10-30T14:22:05Z"
}
```

Event catalogue:

| Action | Writer | Notes |
|---|---|---|
| CLAIM_SENT_BACK | Manager | feedback {reason, flagged_fields, comment} required |
| CLAIM_RETURNED_TO_MANAGER | Finance | feedback required; assigned_to = target manager |
| CLAIM_EDIT_OPENED | Staff (SENT_BACK → edit) | |
| CLAIM_UPDATED | Staff | fields JSON diff old/new |
| CLAIM_EDIT_BLOCKED | server | attempt rejected by whitelist |
| CLAIM_SUBMITTED | Staff | resubmission_count |
| RECEIPT_FILE_ACCESSED | server, every authorized file/page fetch | {receipt_id, filename, content_type, tier, purpose:"verification"} |
| RECEIPT_ARCHIVED / ARCHIVAL_FAILED / CACHE_EXPIRED / SERVED_FROM_COLD | storage/promotion path | see 3.5 |
| existing CLAIM_CREATED/APPROVED/REJECTED/PAID, DUPLICATE_WARNING, PAYMENT_BLOCKED | unchanged, through same envelope | |

**Transparency policy (decided): every ticket visible to everyone who can see the
claim, including after payment.** Finance→Manager notes are not hidden from Staff.

### 3.4 Receipt visibility + verification (decided)

| Who | Can view receipt file & full ticket history | When |
|---|---|---|
| Staff (owner) | ✔ own claim | always, incl. after paid |
| Manager | ✔ team claims | always (read-only after his approval) |
| Finance | ✔ all | always |

- Rationale: the *actions* stop at PAID (terminal), but viewing never stops —
  the evidence chain matters most for disputes after payment.
- Every file view is authorized server-side on each request and logged
  (`RECEIPT_FILE_ACCESSED`) — proves the reviewer actually opened the file
  before approving/paying.
- Frontend gates the same actions client-side (role + status aware) but never
  trusts it; the backend remains the enforcement layer.

### 3.5 Storage tiering: hot / cache / cold (decided: TieredStorage)

Usage pattern driving this:
- Active claims (DRAFT→APPROVED): read-heavy (staff after feedback, manager review,
  finance verify) → **HOT tier, instant and free**
- Paid claims: read-rare (opens on dispute/audit only) → **COLD tier (S3 STANDARD_IA)**

Tiers:
```
storage/s3(MinIO):  receipts/<receipt-id>/original.pdf   HOT    active claims
                    paid-cache/<receipt-id>/original.pdf CACHE  paid ≤ PAID_CACHE_DAYS (7)
s3://bucket:        receipts/<receipt-id>/original.pdf   COLD   paid, long-term
```

Promotion at `pay_claim` success (after commit, **non-blocking**, idempotent):
1. server-side `copy_object` MinIO → S3, checksum verified (vs stored SHA-256)
2. server-side `copy_object` MinIO → MinIO `paid-cache/<id>/…` (copy time = paid time)
3. delete hot object only after both copies verify
4. `storage_tier='COLD'` + `paid_cache_key` on Receipt; audit `RECEIPT_ARCHIVED`
   `{s3_key, cache_key, checksum, expires_at = paid_at + 7d}`

**Three hard rules:**
- payment never depends on promotion (failure ⇒ audit `RECEIPT_ARCHIVAL_FAILED`,
  stays HOT, retried opportunistically on next access — no background worker needed)
- idempotent re-runs (checksum verify or no-op)
- one truth at a time (delete-after-verify; `storage_tier` authoritative)

Cold reads: presigned URL, instant (STANDARD_IA — NOT Glacier Flexible/Deep Archive,
which require restore jobs). Falls back gracefully:
```
COLD → if paid_at + 7d > now AND paid_cache_key: try MinIO paid-cache
       on NoSuchKey → S3 presigned + reconcile record + RECEIPT_CACHE_EXPIRED
```
Every cold read logs `RECEIPT_SERVED_FROM_COLD {tier: "COLD"}`.

**The 7-day timing trap (why paid-cache prefix exists):** S3 lifecycle `Expiration Days`
counts from object **last-modified**, not from tagging. Expiring the *original* hot
object on a tag would fire at the wrong time (uploaded long before payment).
Copying to `paid-cache/` at payment makes last-modified == paid moment ⇒
lifecycle rule on that prefix expires each object **exactly 7 days after payment**.

MinIO lifecycle rule (set once at config time):
```json
{ "Rules": [{ "ID": "expire-paid-cache", "Status": "Enabled",
   "Filter": { "Prefix": "paid-cache/" },
   "Expiration": { "Days": 7 } }] }
```

Config knobs (db.py / .env.example):
```
STORAGE_BACKEND / STORAGE_DIR           # hot = local disk in prod; MinIO in dev
MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY
S3_BUCKET_NAME / AWS_REGION (us-east-1) / S3_STORAGE_CLASS = STANDARD_IA
S3_KEY_PREFIX = receipts
PAID_CACHE_DAYS = 7
```

Note: docker-compose only currently has Postgres; MinIO service added for the dev
hot endpoint (prod hot endpoint remains local disk — same StorageBackend ABC).

### 3.6 Design decision: no finance → direct-to-staff send-back (decided)

Finance's feedback flows only through the Manager, who decides:
staff must fix → `SENT_BACK`; manager re-checks himself → acts on SUBMITTED;
fine after all → re-approve. `SENT_BACK` actor is therefore always MANAGER —
clean responsible-party trail: *Finance flagged it → Manager routed it → Staff fixed it.*

### 3.7 Tiered duplicate-payment prevention (decided)

Problem with fuzzy-only payment blocking: recurring legitimate expenses (e.g. daily
cab rides — same merchant, same amount) score high and get false-blocked; and reworded
duplicates of a different file can slip through. Fix: identity keys decide, fuzzy advises.

Tiers checked at `pay_claim`:

| Tier | Basis | Verdict |
|---|---|---|
| A | same file — `checksum_sha256` equals a PAID receipt | hard block, no override |
| B | same receipt no — normalized `(merchant, receipt_no)` equals PAID claim | hard block, no override |
| C | same transaction — `(merchant, date, amount, time?)` matches but receipt numbers differ | decision gate: pay requires typed justification → `DUPLICATE_DISMISSED`, else blocked |
| D | fuzzy weighted score (35/30/20/15) vs PAID claims | warning chip only, never blocks |

Matching is **strict equality after one normalization step** (same helper on write for
both sides) — no tolerance windows:

| Field | Comparison |
|---|---|
| receipt_no | normalized equality (uppercase, strip spaces/punct/prefixes) — `UB-984213 X` ≡ `ub984213x` |
| time | minute-truncated equality — `14:22:05` ≡ `14:22` (seconds are capture noise); no ±N windows |
| date / amount / merchant | exact |

Frontend renders a side-by-side anchor card (Receipt No / Time / Date / Amount / Merchant,
monospace, exact-vs-mismatch badges, both raw values on mismatch) on the duplicate panel
at submit and in the pay queue; tier badge names the anchor that fired (A/B/C/D), fuzzy
score shown only as a secondary line for tier D. Tier tickets embed both raw and normalized
values per anchor so the audit trail shows exactly what was compared.

- Old `has_paid_duplicate` auto-block at ≥65 is removed (false-positive risk); submit-time
  fuzzy warnings stay unchanged (advice only).
- Legacy receipts without receipt_no/time degrade to tiers C/D — nothing breaks.
- Every verdict is a JSON ticket (PAYMENT_BLOCKED / DUPLICATE_DISMISSED) including match details.
- Payment final step is CAS: `UPDATE ... WHERE status='APPROVED'` (portable SQLite/Postgres);
  concurrent pay gets 409 — fixes the double-pay race; same pattern backs `transition()`.

Pipeline: build D chips → C gate → B block → A block → CAS pay →
post-commit promotion hook (§3.5).

Schema additions (one alembic migration): `Claim.receipt_no`, `Claim.trans_time`
(nullable, normalized on write: uppercase, strip spaces/punct/prefixes).
Extraction: Gemini prompt + text regexes (`receipt|invoice|bill|trip no|inv #|[A-Z]{2,}-?\d{4,}`)
and `\d{1,2}:\d{2}` for time; new editable form fields, auto-filled, human-correctable.

Example ticket:
```json
{ "action": "PAYMENT_BLOCKED", "meta": { "tier": "B", "basis": "receipt_no",
   "matched": { "claim": "CLM-1005", "receipt_no": "UB984213X",
                "merchant": "UBER", "date": "2026-10-12", "amount": 320 } } }
```
Key regression test: 5 legitimate daily cabs (same merchant+amount, different dates)
all pay; same invoice reworded blocks at B; same photo blocks at A; C requires justification.

---

## 4. Implementation plan

### Phase ①  — Core integrity (backend)
- `EDITABLE_FIELDS_BY_STATUS` + rewritten `update_claim` (whitelist, JSON diff, CLAIM_EDIT_BLOCKED, single transaction)
- `log_event()` envelope; route all existing and new events through it
- Fix dedupe of `_duplicates()`; `page_count` from `doc.page_count`; doc.close(); drop wasted re-encode
- concurrency band-aids where cheap (claim_number sequence-based on Postgres, fallback for SQLite)
- Receipt-access logging (`RECEIPT_FILE_ACCESSED`)

### Phase ②  — Send-back workflow (backend + API)
- `Status.SENT_BACK`; `ALLOWED_TRANSITIONS` additions (no DB migration needed — status is a string column)
- `POST /claims/{id}/send-back` (Manager) + `POST /claims/{id}/return-to-manager` (Finance)
  with typed `SentBackIn { reason: min_length=1, flagged_fields?: list, comment? }`
- typed reject body (replace raw dict + dead RejectIn)
- Manager read-only on APPROVED; Finance cannot touch SENT_BACK path (no route)
- `can_view`/`can_view_receipt` unchanged (already correct)

### Phase ③  — TieredStorage (backend + infra)
- alembic migration: `receipts.storage_tier ('HOT')` + `paid_cache_key`
- `TieredStorage(LocalStorage|MinIO, S3)` composite implementing the same ABC, routing by tier
- promotion hook in `pay_claim` (non-blocking, idempotent) + audit events
- MinIO in docker-compose + lifecycle config for `paid-cache/` (7d)
- env knobs in db.py and .env.example

### Phase ④  — Frontend
- status badge for SENT_BACK ("Returned for revision") + "Returned by Finance" banner on
  re-queued SUBMITTED claims (derived from latest audit ticket)
- feedback card rendering the JSON envelope (reason, flagged fields, comment, actor, at)
- action buttons per capability matrix (Edit only owner+editable status; Manager inert on
  APPROVED — message "Approved — with Finance now"; Finance "Return to Manager" on APPROVED)
- edit page enabled for SENT_BACK; reads `sent_back_feedback` from latest audit
- audit timeline renders tickets (transition chips, field diffs, file-access rows)
- minor: fix blob-URL leaks + iframe sandbox in claim preview while touching that page

### Tests (each phase, all on SQLite unless noted)
1. owner cannot edit SUBMITTED / APPROVED (422) + CLAIM_EDIT_BLOCKED ticket
2. owner can edit DRAFT / REJECTED / SENT_BACK; resubmit from SENT_BACK
3. Manager send-back happy path + JSON envelope assert; empty reason 422; self/team/role denials
4. Finance return-to-manager happy path + envelope; manager inert on APPROVED (all endpoints)
5. full loop integration: submit → sent back → edit (diff ticket) → resubmit(count 2) →
   approve → return-to-manager → re-approve → pay → promote
6. promotion success (checksum verify, hot deleted, tickets); promotion failure (paid anyway,
   FAILED ticket, HOT retained); idempotent re-promote; cache-expired fallback (NoSuchKey → S3)
7. spend math excludes SENT_BACK; dashboards unaffected; IDOR matrix extended to new endpoints

### Rollout order
① → ② → ④ (badge/feedback UI so the flow is demonstrable) → ③ (storage last: needs
MinIO + migration; independent of ①/② behavior) → bug-bash small fixes (🔴#2 #4 #5 #6 #7)
→ README rewrite (delete stale PDF-artifact section, document tiering + workflow).

---

## 5. Acceptance criteria

- [ ] No field of a SUBMITTED/APPROVED/PAID claim is mutable; attempts audited
- [ ] Manager can send back → staff sees reason + flagged fields → edit → resubmit; loop fully ticketed
- [ ] Finance can return an APPROVED claim to Manager (feedback required); Manager is otherwise inert post-approval
- [ ] Every claim event is one JSON ticket in the same transaction (no audit gaps)
- [ ] Receipt file viewable by owner/manager/finance at every phase incl. after paid; every view authorized + logged
- [ ] Paid receipts promoted to S3 (checksum-verified), MinIO cache expires 7d after payment via lifecycle rule, cold reads instant + logged
- [ ] Payment never fails due to storage ops; promotion self-heals
- [ ] All new tests green; existing 22+ tests still pass

---

## 6. Out of scope (for this changeset)
- Real auth (hashed passwords, persistent sessions, password reset)
- Claim versioning table (Option C — revisit if long-term auditability is required)
- Notifications (email/Slack on submit/approval/rejection/payment)
- Delegated approvals, CSV export, multi-currency
- Glue/region-locked storage optimization, Deep Archive tiering
