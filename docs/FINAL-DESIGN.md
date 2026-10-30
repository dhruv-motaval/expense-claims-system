# Expense Claims System — Final Architecture Draft (v2, frozen)

> Hand this document to the implementing agent together with `AGENT-BRIEF.md` (execution order & tasks).
> All decisions below are APPROVED and frozen. Nothing in here is "to discuss" — build exactly this.

---

## PART A — Context and current state

```text
Browser (Next.js + Tailwind) → /api/* proxied via next.config.mjs rewrites
        ▼
FastAPI backend → SQLAlchemy → PostgreSQL (docker compose) / SQLite (tests)
   ├── extractors: deterministic text parser + optional Gemini vision extraction
   └── storage: LocalStorage or S3Storage (boto3) for receipt files
```

Existing machinery that is KEPT as-is:
- Roles: STAFF / MANAGER / FINANCE; manager-team hierarchy via `User.manager_id`
- Auth guards per route (`deps.py`, `services.can_view/require_claim_view/can_view_receipt`)
- Receipt upload: S3/private objects, SHA-256 checksum stored, presigned URLs,
  rollback of uploaded artifacts if anything fails
- `AuditEvent` with JSON `metadata` column (becomes the ticket store, §B3)
- Duplicate warning at submit (fuzzy weighted scan) — stays as advice-only
- Claim numbers `CLM-<1000+id>`; spend = APPROVED+PAID; projected = SUBMITTED
- Duplicate detection detail from v1 (README): merchant 35 / amount 30 / date 20 /
  description 15; strong ≥ 85; possible ≥ 65 (kept only as tier-D advisory signal)

Known defects from code review (to be fixed while implementing the phases):
1. `update_claim` only blocks PAID → SUBMITTED/APPROVED claims still editable (P0)
2. `CURRENCY_LINE` regex lacks word boundaries (merchant lines misread as amounts)
3. `_duplicates()` defined twice in `services.py`
4. S3 `get_object` FileNotFoundError swallowed by its own except block
5. LocalStorage download URL `/storage/<key>` served by no backend route (legacy 404)
6. `pdf_processor`: doc never closed; discarded render work; page_count from
   successful renders instead of `doc.page_count`
7. Gemini API key in `?key=` query param (move to `x-goog-api-key` header)
8. Concurrency: claim_number `max(id)+1000` race; double-pay race; split commits
9. `RejectIn` schema dead code; reject endpoint takes raw dict
10. Test gaps: assertion-free `test_storage.py`; S3 get_object/delete/prefix untested

---

## PART B — Target architecture (all approved)

### B1. Extended state machine

```text
DRAFT ──submit──► SUBMITTED ──approve──► APPROVED ──pay──► PAID   (PAID terminal)
   ▲                 │  │      │             │
   │                 │  │      └─SENT_BACK    └─► SUBMITTED   (Finance returns to
   │                 │  │        (Manager sends        Manager for re-review)
   │                 │  │         to Staff)
   │                 │  └─► REJECTED ─► DRAFT            (existing loop)
   │                 └──── (Manager: approve / reject / send-back)
   └── owner edits in DRAFT / REJECTED / SENT_BACK
```

`ALLOWED_TRANSITIONS` additions:
- `SUBMITTED: {APPROVED, REJECTED, SENT_BACK}`
- `APPROVED:  {PAID, SUBMITTED}`        ← Finance `return-to-manager`
- `SENT_BACK: {DRAFT, SUBMITTED}`       ← owner opens-for-edit / resubmits

Status value: `SENT_BACK`, UI label **"Returned for revision"**.
No DB migration needed for the status itself (string column).

**Authority rules (approved):**
- Manager approves ⇒ claim leaves his authority. Post-approval Manager is
  COMPLETELY INERT (read-only) until Finance pays or returns it to his queue.
- Finance never contacts Staff; Manager is the gate for Staff revision.
- Finance never edits claims.

Capability matrix (server-enforced):

| Action | Staff | Manager | Finance | Precondition |
|---|---|---|---|---|
| Edit fields | ✔ owner (DRAFT/REJECTED/SENT_BACK) | — | — | whitelist B2 |
| Submit / resubmit | ✔ owner (DRAFT/SENT_BACK) | — | — | |
| Approve | — | ✔ SUBMITTED, team, not own | — | |
| Send back to Staff | — | ✔ SUBMITTED, team, not own | — | reason required |
| Reject | — | ✔ SUBMITTED, team, not own | — | reason required |
| Return to Manager | — | ✘ read-only after approve | ✔ APPROVED | feedback required |
| Pay | — | — | ✔ APPROVED | tiers B7 + CAS |

### B2. Edit integrity (P0)

`EDITABLE_FIELDS_BY_STATUS` centralized next to `ALLOWED_TRANSITIONS`:

```python
EDITABLE_FIELDS_BY_STATUS = {
    DRAFT:     ALL_FIELDS,        # merchant, transaction_date, trans_time, receipt_no,
    REJECTED:  ALL_FIELDS,        # category, amount, currency, description
    SENT_BACK: ALL_FIELDS,        # owner only, after manager sent back
    SUBMITTED: set(),             # frozen under review
    APPROVED:  set(),             # frozen — approved truth is paid truth
    PAID:      set(),             # frozen (already enforced)
}
```

`update_claim` becomes:
1. owner check (existing)
2. `attempted = set(data.model_dump(exclude_unset=True))`
3. attempted − editable-for-status ≠ ∅ ⇒ 422 `BusinessRuleError` +
   ticket `CLAIM_EDIT_BLOCKED {attempted_fields, status, from→to values}`
4. apply + `CLAIM_UPDATED` ticket **with full JSON diff `{field: {old, new}}`**

### B3. JSON ticket logging standard

One `log_event(db, claim, actor, action, meta, transition=None)` writes all events
through this envelope, IN THE SAME TRANSACTION as the change (no split commits):

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

Full event catalogue:

| Action | Writer | Extra JSON |
|---|---|---|
| CLAIM_SENT_BACK | Manager | feedback {reason, flagged_fields, comment} |
| CLAIM_RETURNED_TO_MANAGER | Finance | feedback; assigned_to = target manager |
| CLAIM_EDIT_OPENED | Staff (SENT_BACK→edit open) | |
| CLAIM_UPDATED | Staff | fields diff {field: {old,new}} |
| CLAIM_EDIT_BLOCKED | server | attempted fields + status |
| CLAIM_SUBMITTED | Staff | resubmission_count |
| RECEIPT_FILE_ACCESSED | server (every authorized file/page fetch) | {receipt_id, filename, content_type, tier, purpose:"verification"} |
| RECEIPT_ARCHIVED / ARCHIVAL_FAILED / CACHE_EXPIRED / SERVED_FROM_COLD | promotion path (B6) | storage details |
| PAYMENT_BLOCKED | payment path (B7) | tier + compared anchors |
| DUPLICATE_DISMISSED | Finance (tier-C gate) | matched claims + justification |
| CLAIM_CREATED / _APPROVED / _REJECTED / _PAID, DUPLICATE_WARNING, PAYMENT_BLOCKED(legacy) | as today, via same envelope | |

**Transparency (approved): every ticket visible to everyone who can see the claim,
including after payment. Finance↔Manager notes are NOT hidden from Staff.**

### B4. Receipt visibility + verification

| Who | Can view receipt file & full ticket history | When |
|---|---|---|
| Staff (owner) | ✔ own claim | always, incl. after paid |
| Manager | ✔ team claims | always (read-only after his approval) |
| Finance | ✔ all | always |

- Viewing NEVER stops at PAID (disputes/audit need the evidence chain).
- Every file view authorized server-side per request + logged `RECEIPT_FILE_ACCESSED`
  (proves reviewer opened the file before approving/paying).
- Frontend gates the same actions client-side (role+status aware) but never trusts it.

**B4.1 In-memory receipt cache (approved).** Receipt bytes are immutable after
upload (original preserved byte-for-byte + checksum), so caching them in-process
can never serve stale content. The active phase is read-heavy (staff re-reads after
feedback, manager review, finance verify) — cache once, serve many, no storage call:

- Cache: per-receipt-id → bytes, TTL 1 h (`RECEIPT_CACHE_TTL_SECONDS=3600`),
  LRU bounded at `RECEIPT_CACHE_MAX_MB=64` (receipts are KB–MB scale; budget is
  generous), mutex-guarded (uvicorn runs single-worker; still safe if workers > 1
  — cache is merely per-process).
- Invalidation: only on file delete/archival-cleanup (rare) — bytes never mutate.
- Every request STILL goes through `can_view_receipt` authorization (authorization
  is per-request and never cached) and still logs `RECEIPT_FILE_ACCESSED` with
  `served_from: "memory"|"hot"|"cold"` — the audit trail stays complete and even
  records cache effectiveness.
- Cache applies to HOT (local/MinIO) reads; for COLD, the presigned URL is cached
  per expiry-bucket until `expires − 60 s` (avoids re-signing; bytes never transit
  the app in cold tier).
- Out of cache (approved): claims, dashboards, reports, audit lists — these are
  mutable and role-filtered; caching them risks stale money numbers. DB-with-indexes
  is fast enough at this scale; revisit only under real load.

### B5. Send-back flow (two gates, approved)

- **Manager: `POST /claims/{id}/send-back`** — SUBMITTED only, team-only, not own.
  Schema `SentBackIn { reason: min_length=1, flagged_fields?: list[str] validated
  against real field names, comment?: str }`. ⇒ status SENT_BACK.
- **Finance: `POST /claims/{id}/return-to-manager`** — APPROVED only.
  Schema `ReturnToManagerIn { reason: min_length=1, flagged_fields?, comment? }`.
  ⇒ status SUBMITTED + ticket `CLAIM_RETURNED_TO_MANAGER {feedback, assigned_to}`.
- Owner in SENT_BACK: full edit rights (whitelist) + `SENT_BACK → SUBMITTED`
  (resubmit, ticket includes `resubmission_count`) or `SENT_BACK → DRAFT`.
- Feedback (reason/flagged fields) lives in the audit JSON — latest
  `CLAIM_SENT_BACK`/`CLAIM_RETURNED_TO_MANAGER` ticket IS the current feedback.
  No new columns.
- Reject endpoint switched to typed schema (kill raw dict + dead `RejectIn`).

### B6. Storage tiering: HOT / CACHE / COLD (approved)

Usage pattern (approved rationale):
- Active claims (DRAFT→APPROVED): read-heavy → HOT tier, instant, free
- Paid claims: read-rare (disputes only) → COLD tier (S3 STANDARD IA)

```text
storage/MinIO:  receipts/<receipt-id>/original.pdf      HOT     active claims ∞
                paid-cache/<receipt-id>/original.pdf    CACHE   paid ≤ 7 days
s3://bucket:    receipts/<receipt-id>/original.pdf      COLD    paid, long-term
```

Promotion at `pay_claim` success (post-commit, NON-BLOCKING, idempotent):
1. server-side `copy_object` MinIO→S3, checksum verified vs stored SHA-256
2. server-side `copy_object` MinIO→MinIO `paid-cache/<id>/…`
3. delete HOT object only after both copies verify
4. `receipts.storage_tier='COLD'` + `paid_cache_key`;
   ticket `RECEIPT_ARCHIVED {s3_key, cache_key, checksum, expires_at: paid_at + 7d}`

THREE HARD RULES:
- payment NEVER depends on promotion (fail ⇒ ticket `RECEIPT_ARCHIVAL_FAILED`,
  stays HOT, self-heal on next access / read fallback — no background worker)
- idempotent re-runs (verify checksum or no-op)
- one truth at a time (delete-after-verify; `storage_tier` authoritative)

Cold reads: presigned URL, instant (STANDARD_IA — never Glacier Flexible/Deep
Archive: no restore-waiting). Routing:

```text
COLD → if paid_at + PAID_CACHE_DAYS > now AND paid_cache_key:
           try MinIO paid-cache (instant)
           on NoSuchKey → S3 presigned + reconcile record (cache_key=None)
                        + ticket RECEIPT_CACHE_EXPIRED
       else → S3 presigned + ticket RECEIPT_SERVED_FROM_COLD {tier:"COLD"}
```

**The 7-day timing trap (why paid-cache prefix exists):** S3 lifecycle
`Expiration.Days` counts from object last-modified, NOT from tagging. Copying to
`paid-cache/` at payment makes last-modified == paid moment ⇒ prefix-scoped
lifecycle expires exactly 7 days after payment.

MinIO lifecycle rule (applied once at startup/config script):
```json
{ "Rules": [{ "ID": "expire-paid-cache", "Status": "Enabled",
   "Filter": { "Prefix": "paid-cache/" },
   "Expiration": { "Days": 7 } }] }
```

Config (backend/app/db.py + .env.example):
```
STORAGE_BACKEND / STORAGE_DIR           # hot = local disk in prod; MinIO in dev
MINIO_ENDPOINT / MINIO_ACCESS_KEY / MINIO_SECRET_KEY
S3_BUCKET_NAME / AWS_REGION (us-east-1) / S3_STORAGE_CLASS = STANDARD_IA
S3_KEY_PREFIX = receipts
PAID_CACHE_DAYS = 7
```
Migration: `receipts.storage_tier` (default 'HOT') + `receipts.paid_cache_key` (nullable).
docker-compose gains a MinIO service (dev hot endpoint; prod hot endpoint = local disk;
same StorageBackend ABC either way).

### B7. Tiered duplicate-payment prevention (approved — replaces fuzzy auto-block)

Problem: fuzzy-only blocking false-positives on recurring legit expenses (daily cabs)
and can be reworded past. Fix: **identity keys decide, fuzzy only advises**.

Tiers, checked at `pay_claim`:

| Tier | Basis | Verdict |
|---|---|---|
| A | same file — `checksum_sha256` equals a PAID receipt | hard block, no override |
| B | same receipt no — normalized `(merchant, receipt_no)` equals PAID claim | hard block, no override |
| C | same transaction — `(merchant, date, amount, time?)` matches, receipt numbers differ | decision gate: pay requires typed justification ⇒ `DUPLICATE_DISMISSED`; else blocked |
| D | fuzzy weighted score (35/30/20/15) vs PAID claims | warning chip ONLY, never blocks |

- Old `has_paid_duplicate` auto-block at ≥65: REMOVED.
- Submit-time fuzzy warnings stay unchanged (advice only, ≥65/≥85 messaging).
- Receipts without receipt_no/time degrade to C/D — nothing breaks.
- Pipeline: build D chips → C gate → B block → A block → CAS pay.

**Strict equality after one normalization step** (same helper on write for both
sides) — NO tolerance windows:

| Field | Comparison |
|---|---|
| receipt_no | normalized equality (uppercase, strip spaces/punct/`NO/INV/#` prefixes); `UB-984213 X` ≡ `ub984213x` |
| time | minute-truncated equality — `14:22:05` ≡ `14:22` (seconds = capture noise); never ±N windows |
| date / amount / merchant | exact |

Schema additions (ONE migration): `Claim.receipt_no`, `Claim.trans_time` (nullable).
Extraction: Gemini strict-JSON prompt adds `receipt_no` + `time`; text-regex family
`receipt|invoice|bill|trip\s*(no|id|#)|inv\s*#?` + generic `[A-Z]{2,}-?\d{4,}`;
time from `\d{1,2}:\d{2}`. New editable form fields, auto-filled, human-correctable.

Payment ends in CAS (portable SQLite+Postgres, no FOR UPDATE):
```python
res = db.execute(
    update(Claim)
    .where(Claim.id == claim.id, Claim.status == Status.APPROVED.value)
    .values(status="PAID", paid_at=now, paid_by_id=user.id))
if res.rowcount != 1:
    db.rollback(); raise HTTPException(409, "Already transitioned.")
```
Same CAS pattern backs `transition()` generally (fixes double-pay race).

Tier ticket embeds raw + normalized values of every anchor:
```json
{ "action": "PAYMENT_BLOCKED", "meta": { "tier": "B", "basis": "receipt_no",
   "compared": { "receipt_no": { "this": "UB-984213 X", "other": "ub984213x",
                                 "normalized_eq": true },
                 "time": { "this": "14:22", "other": "14:22:05", "normalized_eq": true } },
   "matched": { "claim": "CLM-1005", "receipt_no": "UB984213X", "merchant": "UBER",
                "date": "2026-10-12", "amount": 320 } } }
```

### B8. Frontend (Next.js) — approved UI spec

1. **Badges/status**: `SENT_BACK` → label "Returned for revision" (distinct color).
   Manager on APPROVED claim: read-only message "Approved — with Finance now".
   SUBMITTED queue item whose latest ticket is CLAIM_RETURNED_TO_MANAGER:
   banner **"Returned by Finance — <reason>"** (derived from latest audit ticket).
2. **Feedback card** (SENT_BACK): renders ticket JSON — reason, flagged fields
   (highlighted on the edit form), comment, actor name/role, timestamp.
   Staff sees the full trail too (transparency policy).
3. **Action buttons per capability matrix**: Edit only for owner + editable status;
   Manager "Send back" + "Reject" (reason modal) on own-team SUBMITTED;
   Finance "Return to Manager" + "Pay" on APPROVED queue; nothing for frozen states.
4. **Duplicate/anchor panel** (submit time + Finance pay queue): side-by-side card
   `This claim vs CLM-xxxx`: rows Receipt No / Time / Date / Amount / Merchant —
   monospace IDs, exact ✓ / mismatch ✗ badges, both raw values shown on mismatch;
   tier badge (A/B/C/D) names the anchor that fired; fuzzy score only as a
   secondary line for tier D. Tier-C row shows the justification modal.
5. **Audit timeline**: renders every ticket from the JSON envelope (transition chips,
   field diffs, file-access rows with tier, payment blocks with tier + anchors).
6. **Receipt viewer**: role+status-gated client-side; no receipt access after-paid
   restrictions; adds sandbox attr to PDF iframe; fixes blob-URL leaks (revoke on
   reload/unmount); keeps the whole list of available preview types
   (PDF, JPEG, PNG, WebP originals).
7. **Claim form**: new monospace fields `Receipt No.` + `Time` (auto-filled, editable).

---

## PART C — Acceptance criteria (Definition of Done)

- [ ] No field of a SUBMITTED/APPROVED/PAID claim is mutable; attempts audited (CLAIM_EDIT_BLOCKED)
- [ ] Manager send-back → staff sees reason + flagged fields → edit → resubmit; fully ticketed
- [ ] Finance return-to-manager works on APPROVED only; Manager inert post-approval
- [ ] Every claim event = one JSON ticket in the same transaction; no audit gaps
- [ ] Receipt viewable by owner/manager/finance at every phase incl. paid; every view authorized + logged
- [ ] Paid receipts promoted to S3 (checksum-verified); MinIO cache expires 7d after payment; cold reads instant + logged; self-healing fallback
- [ ] Payment never fails due to storage ops; promotion idempotent
- [ ] Duplicate payment: A/B hard-block, C decision-gated with justification, D advisory only; daily-cab 5×legit claims all pay
- [ ] Concurrent pay → exactly one PAID, other 409 (CAS)
- [ ] All existing tests green; new test suite green (see AGENT-BRIEF §T)

## PART D — Out of scope (do NOT build)
Real auth (hashed passwords/persistent sessions), claim versioning tables,
notifications, delegated approvals, CSV export, multi-currency, deep-archive tiers.
