# Expense Claims System — Agent Build Brief

> Companion to `docs/FINAL-DESIGN.md` (the approved architecture, PART A–D).
> SOURCE OF TRUTH for every rule is FINAL-DESIGN.md — do not re-derive or improvise.
> Execute phases in order; each phase has a gate (tests green + behaviors verified)
> before the next starts. Commit after every phase.
> Repo root: `C:\Users\dhruv\OneDrive\Desktop\AWS\expense-claims-system`

## Global constraints (always true)
- Backend: FastAPI + SQLAlchemy in `backend/app/`, alembic, tests on SQLite
  (`DATABASE_URL=sqlite:///:memory:` in `backend/tests/conftest.py`).
- Keep ALL existing tests passing. Run: `cd backend && DATABASE_URL=sqlite:///:memory: python -m pytest -v`.
- Frontend: Next.js App Router in `frontend/`, auth via `lib/auth.ts`, proxy via `next.config.mjs`.
- Demo auth stays as-is (identity-assertion). Do NOT touch login mechanics.
- Every state change commits claim + audit ticket in ONE transaction (FINAL-DESIGN B3).
- All new env knobs go to `backend/app/db.py` + `backend/.env.example`.
- Update README at the very end (Phase ⑤) to match reality; delete the stale
  PDF-artifacts section, document: send-back workflow, tickets, tiered storage, tiered duplicates.

---

## Phase ① — Core integrity + ticket engine (backend)

Files: `app/services.py`, `app/models.py`, `app/schemas.py`, `app/db.py`, alembic.

1. Migration `xxx_add_receipt_anchors_and_tier`:
   - `claims.receipt_no` (Text, nullable), `claims.trans_time` (Time, nullable)
   - `receipts.receipt_no` (Text, nullable), `receipts.trans_time` (Time, nullable)
   - `receipts.storage_tier` (String, default 'HOT'), `receipts.paid_cache_key` (Text, nullable)
   - Schema drift check: JSON-with-variant pattern consistent with existing migrations.
2. Normalize-on-write helper in one place (`app/duplicates.py::normalize_receipt_no`,
   `normalize_time`) — used by extractor, API create/update, seed, tests
   (uppercase, strip spaces/punct/`no|inv|#` prefixes; time → minute precision).
3. `log_event(db, claim, actor, action, meta=None, transition=None)` envelope (B3).
   Route ALL audit writes through it. Remove the duplicate `_duplicates()` def.
4. `EDITABLE_FIELDS_BY_STATUS` (B2) + rewrite `update_claim`:
   whitelist check → 422 + `CLAIM_EDIT_BLOCKED`; else apply + `CLAIM_UPDATED`
   ticket with JSON diff; single commit (no split commits).
   Validation additions while touching schemas: `ClaimCreate/Update` — min length 1
   on merchant, `currency` Literal["INR"], date bounds sane (>= 1 year old,
   <= today+30), amount > 0. Use `SentBackIn`-style typed schemas; wire reject to
   typed body (`RejectIn` resurrected, not raw dict).
5. Receipt-access logging: `GET /receipts/image|/pages/...` → after `can_view_receipt`
   passes, log `RECEIPT_FILE_ACCESSED {receipt_id, filename, content_type, tier}`.
6. Small fixes swept here: CURRENCY_LINE word boundaries (`\b(?:₹|rs\.?|inr)\b`,
   `extractors.py:12`); Gemini key → `x-goog-api-key` header; `pdf_processor` doc.close()
   + `page_count = doc.page_count` + drop the discarded re-encode loop; S3
   `get_object` FileNotFoundError fix (`storage.py:169-176`).
7. Extraction: Gemini prompt adds `receipt_no` + `time`; text parser regexes
   (`receipt|invoice|bill|trip\s*(no|id|#)|inv\s*#?`, `[A-Z]{2,}-?\d{4,}`, `\d{1,2}:\d{2}`);
   normalized on write. `ClaimCreate/ClaimUpdate` accept optional `receipt_no`/`trans_time`.
8. `claim_dict`/`claim_detail` responses add: `receipt_no`, `trans_time`,
   `latest_feedback` (from latest CLAIM_SENT_BACK / CLAIM_RETURNED_TO_MANAGER ticket),
   full `audit` list already exists — ensure JSON envelopes included.

GATE ①: existing tests green + new tests:
- owner edit SUBMITTED → 422 + CLAIM_EDIT_BLOCKED ticket; APPROVED same; DRAFT/REJECTED ok
- `CLAIM_UPDATED` ticket contains old/new diff
- normalize: "UB-984213 X" ≡ "ub984213x"; "14:22:05" ≡ "14:22"
- reject with empty reason → 422 (typed schema)
- receipt access logs a `RECEIPT_FILE_ACCESSED` ticket
- extraction pulls receipt_no + time from pasted sample text ("Receipt No: UB984213X", "Time: 14:22")

---

## Phase ② — Send-back workflow + tiered payment (backend + API)

Files: `app/models.py`, `app/services.py`, `app/api.py`, `app/schemas.py`.

1. `Status.SENT_BACK = "SENT_BACK"` + language "Returned for revision".
   `ALLOWED_TRANSITIONS` updates per B1. `STATUSES` Literal in schemas updated
   (frontend mirrors).
2. `send_back_claim(db, manager, claim, data: SentBackIn)`:
   - Manager role, status == SUBMITTED, `claim.user_id != manager.id`,
     `claim.user.manager_id == manager.id`, reason non-empty (B5)
   - transition to SENT_BACK + ticket `CLAIM_SENT_BACK {feedback}`
3. `return_to_manager(db, finance_user, claim, data: ReturnToManagerIn)`:
   - Finance role, status == APPROVED, reason non-empty
   - transition APPROVED→SUBMITTED + ticket `CLAIM_RETURNED_TO_MANAGER
     {feedback, assigned_to: {manager of claim owner}}`
4. `return-to-draft` from SENT_BACK allowed (owner) → ticket `CLAIM_EDIT_OPENED`.
5. Resubmit = transition SENT_BACK→SUBMITTED, ticket includes `resubmission_count`
   (# of prior SUBMITTED tickets for claim + 1).
6. Manager on APPROVED: approve/reject/send-back routes must refuse (422/409),
   issue: manager is inert post-approval (B1) — service-level guards, not just UI.
7. Tiered duplicates in `app/duplicates.py`:
   - `identity_match(db, claim) -> tier|None` implementing A/B/C per B7
     (A: checksum vs PAID receipts; B: normalized (merchant, receipt_no) vs PAID;
     C: (merchant, date, amount, trans_time?) match with differing receipt_nos)
   - keep fuzzy `find_duplicates` for D (submit warnings) — unchanged behavior
   - REMOVE payment auto-block from fuzzy (delete `has_paid_duplicate` usage in pay path)
8. Rewrite `pay_claim` per B7 pipeline:
   - D chips collected (for queue payload) → C gate: if tier C and no
     `PayIn { justification: min_length=20 }` → 422 (message names tier + matched claim)
     with justification → proceed + ticket `DUPLICATE_DISMISSED {matched, justification}`
   - B / A → 422 + ticket `PAYMENT_BLOCKED {tier, basis, compared, matched}`
   - final step CAS `UPDATE ... WHERE status='APPROVED'` (B7 code), 409 on rowcount != 1
   - queue endpoint (`approve`-queue / finance dashboard) returns duplicate_flags
     [{tier, basis, matched_claim_number, anchor_equalities}] per payable claim
9. CAS in `transition()` (same pattern) — fixes approve/reject races the same way.

GATE ②: all tests green + new tests:
- manager send-back happy path + envelope JSON; empty reason 422; self-claim 422;
  other-team 403; staff hitting endpoint 403
- finance return-to-manager happy path + envelope; manager inert on APPROVED
  (approve/reject/send-back all refuse)
- SENT_BACK: owner edit ok, resubmit ok with resubmission_count=2, return-to-draft ok
- spend math: SENT_BACK excluded from actual AND projected
- tier A: upload same bytes twice → second pay blocked, ticket has tier:"A"
- tier B: reworded text + same receipt_no → blocked, compared anchors embedded
- tier C without justification → 422; with justification → PAID + DUPLICATE_DISMISSED
- tier D only (merchant+amount overlap, different date/receipt) → pays, chips returned
- DAILY-CAB REGRESSION (critical): 5 claims, same merchant+amount, different dates
  and receipt numbers → all pay, no block
- CAS: two overlapping sessions pay same claim → one PAID, one 409; ticket history
  shows exactly one CLAIM_PAID

---

## Phase ③ — Frontend (Next.js)

Files: `lib/format.ts` (STATUS_COLORS + SENT_BACK), `components/ui.tsx`,
`components/claims.tsx`, `app/claims/[id]/page.tsx`, `app/claims/[id]/edit/page.tsx`,
`app/claims/new/page.tsx`, `app/dashboard/manager/page.tsx`,
`app/dashboard/finance/page.tsx`, `lib/api` error handling.

1. Status badge for SENT_BACK: "Returned for revision".
2. Claim detail page:
   - Feedback card when `latest_feedback` exists (reason, flagged fields highlighted
     on the form, comment, actor, timestamp) — same card for staff view
     (transparency: no filtering).
   - "Returned by Finance — <reason>" banner on SUBMITTED claims whose latest ticket
     is CLAIM_RETURNED_TO_MANAGER (manager view).
   - Manager on APPROVED: no action buttons + info line "Approved — with Finance now".
   - Action buttons per matrix (B8.3): Edit (owner+editable), Submit/Resubmit,
     Manager: Approve / Send back / Reject (reason + flagged-fields modal),
     Finance: Return to Manager / Pay (justification modal only when tier-C chip present).
   - Anchor comparison panel per B8.4 (side-by-side, exact/mismatch badges, tier badge).
   - Audit timeline renders JSON tickets (transition chips, field diffs,
     RECEIPT_FILE_ACCESSED rows with tier, PAYMENT_BLOCKED with tier + anchors).
3. Receipt viewer: no after-paid restrictions; add `sandbox` to PDF iframe;
   revoke blob URLs on reload/unmount (existing bug).
4. Form: `Receipt No.` + `Time` monospace inputs (new claim + edit), auto-filled from
   extract, editable; extra anchor fields update `claim_dict` consumers.
5. Finance dashboard: duplicate tier chips per payable claim (A/B/C/D from queue
   endpoint), justification modal → POST pay body, 409 → "Claim was just paid by
   another action." toast + refresh.
6. `lib/auth.ts` `api()`: 422 detail is sometimes an array — stringify gracefully
   ((existing bug) while touching error paths.

GATE ③: `npm run build` clean; manual script below works end-to-end
(demo accounts: neha/staff, vikram/staff, arjun/manager, priya/finance):
1. Vikram: new claim with Receipt No UB984213X, Time 14:22, ₹450 cab → submit
2. Arjun: send back with reason + flagged amount → Vikram sees card, edits amount,
   resubmits, resubmission ticket count = 2
3. Arjun approves → tries any action on APPROVED claim → UI shows inert + server refuses
4. Priya: return-to-manager with reason → claim back in Arjun's queue with banner
5. Arjun re-approves → Priya pays → promotion ticket appears on the timeline
6. Duplicate demo: Riya's paid ₹320 Uber vs Vikram re-worded claim with same
   receipt_no → pay blocked with tier-B card; without receipt_no → tier-C gate.

---

## Phase ④ — Tiered storage (backend + infra)

Files: `app/db.py`, `app/storage.py`, `app/services.py`, `app/receipt_cache.py`,
`docker-compose.yml`, `backend/.env.example`.

0. `app/receipt_cache.py`: tiny TTL+LRU in-memory cache (B4.1) —
   `RECEIPT_CACHE_TTL_SECONDS=3600`, `RECEIPT_CACHE_MAX_MB=64`, mutex-guarded;
   `get_or_load(receipt_id, loader)`; also presigned-URL mini-cache
   (keyed by expiry bucket, re-sign when < 60 s left).
   Wire it into the receipt/page read paths in `services.py` /
   `get_receipt_content`: authorize per request (never cached) → cache-or-load →
   log ticket with `served_from: memory|hot|cold`.

1. `db.py`: MINIO_* knobs, `S3_STORAGE_CLASS`, `PAID_CACHE_DAYS`
   (hot endpoint resolution: `MINIO_ENDPOINT` set → MinIO client for HOT tier;
   else local disk). Keep `get_storage()` factory signature.
2. `storage.py`: add `TieredStorage(hot: StorageBackend, cold: S3Storage)` with the
   same ABC; `route_read(receipt)` by `storage_tier` (+ cache fallback per B6);
   `promote(receipt_id)`: cold copy → cache copy → verify checksums → delete HOT →
   update row + tickets. All idempotent. Presigned URLs for cold reads
   (`S3_STORAGE_CLASS=STANDARD_IA`).
3. `services.py`: after `CLAIM_PAID` commit → run promotion in try/except;
   failure ⇒ ticket `RECEIPT_ARCHIVAL_FAILED {error}` and HOT retained
   (next read/serve triggers retry opportunistically). Cache-expiry fallback:
   NoSuchKey on paid-cache → S3 presigned + reconcile `paid_cache_key=None`
   + ticket `RECEIPT_CACHE_EXPIRED`; success + tier C read ⇒
   `RECEIPT_SERVED_FROM_COLD`.
4. Startup (or config script) applies the MinIO lifecycle rule on `paid-cache/`
   (B6 JSON) if MinIO endpoint configured; log the applied rule.
5. docker-compose: MinIO service (access/secret `minioadmin/minioadmin` or env) +
   healthcheck; document in .env.example: `STORAGE_BACKEND` remains for cold tier,
   MINIO_* for hot tier; `storage/` stays the prod-fallback hot dir.
6. Legacy local receipts: `get_object` on HOT local path resolves as before;
   `storage_tier` defaults 'HOT' via migration — no behavior change for them.

GATE ④ (uses local fake: mock S3 client like tests do today):
- promote: copies verified, HOT deleted, `storage_tier='COLD'`, RECEIPT_ARCHIVED ticket
- promote failure: claim stays PAID, HOT retained, ARCHIVAL_FAILED ticket
- idempotent re-promote: no-op, no duplicate tickets
- cold read within 7d: served from paid-cache (no S3 presign)
- cache expired: NoSuchKey → S3 URL + record reconciled + CACHE_EXPIRED ticket
- hot claims: no MinIO/S3 involvement on reads
- lifecycle rule script: put_bucket_lifecycle_configuration called with B6 JSON

GATE ④b (cache, `tests/test_receipt_cache.py`):
- fetch receipt twice → storage loader called ONCE, both responses identical bytes
- frontend-visible ticket still logged per request with served_from flags
- auth check still enforced per request (change user → 403 even if bytes cached)
- TTL expiry → second fetch after expiry reloads from storage
- LRU eviction: fill over MAX_MB with big fake objects → oldest evicted, re-fetch works
- delete-then-read → cache invalidated, 404 (cache cannot resurrect a deleted file)

---

## Phase ⑤ — README + docs sync
- Rewrite README architecture + storage sections: new state machine diagram,
  capability matrix, ticket envelope example, tiers, MinIO/lifecycle, tiered
  duplicates (A–D), demo script updated with send-back + anchor panel steps.
- Delete stale PDF-artifact band (line ~180-195) — superseded by current-section.
- `docs/IMPROVEMENT-PLAN.md`: mark superseded by FINAL-DESIGN.md (or delete), keep
  AGENT-BRIEF in `docs/`.

## Final acceptance (run all)
```bash
cd backend && DATABASE_URL=sqlite:///:memory: python -m pytest -v      # all green
cd ../frontend && npm run build                                        # clean
docker compose up -d                                                    # postgres + minio
# run Phase ③ manual script against dev stack
```

## Test numbering / naming convention
`tests/test_integrity.py` (white-list edits), `tests/test_sendback.py` (workflow matrix),
`tests/test_payment_tiers.py` (A/B/C/D + CAS + daily-cab), `tests/test_storage_tiers.py`
(promotion/cache/cold). Every business-rule test ALSO asserts the ticket JSON it must
have produced (actions + key fields) — tickets are acceptance evidence, not decoration.
