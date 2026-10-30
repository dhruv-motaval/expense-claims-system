# Implementation Status — single source of truth

> Updated as work proceeds. Rules: an item flips to ✅ only when it exists in code
> AND its gate tests pass. NYI = not yet implemented. DNP = deliberately not part
> of this changeset (see FINAL-DESIGN Part D).

## Phase ① — Core integrity + ticket engine
| # | Item | Status |
|---|---|---|
| 1 | Migration: claims.receipt_no, claims.trans_time, receipts.receipt_no, receipts.trans_time, receipts.storage_tier (default HOT), receipts.paid_cache_key | ✅ (a3f5c9d1e771) |
| 2 | normalize helpers: normalize_receipt_no / normalize_time used on write everywhere | ✅ |
| 3 | log_event() JSON envelope; all audit writes routed through it; same-transaction commits | ✅ (audit() envelope: actor/transition/fields/feedback/at/claim_number) |
| 4 | EDITABLE_FIELDS_BY_STATUS + update_claim whitelist + CLAIM_EDIT_BLOCKED + JSON diff on CLAIM_UPDATED | ✅ |
| 5 | Schema hardening: merchant min/max-length, currency Literal INR, date bounds (−365d..+30d), amount (>0, ≤10M); typed RejectIn body | ✅ |
| 6 | RECEIPT_FILE_ACCESSED logging on all receipt/page reads (claim-linked receipts) | ✅ |
| 7 | Bug sweep: CURRENCY_LINE \b fix; Gemini key → x-goog-api-key header; pdf_processor doc.close + page_count + opt-in render_pages; S3 get_object FileNotFoundError fix; seed today-cap bug removed | ✅ |
| 8 | Extraction: receipt_no + time (Gemini prompt + labelled/bare-token regexes), normalized on write | ✅ |
| 9 | claim_dict/claim_detail: receipt_no, trans_time, latest_feedback, duplicate_tier, tickets with envelopes | ✅ |

## Phase ② — Send-back workflow + tiered payment
| # | Item | Status |
|---|---|---|
| 1 | Status.SENT_BACK + ALLOWED_TRANSITIONS (SUBMITTED→SENT_BACK, APPROVED→SUBMITTED, SENT_BACK→{DRAFT,SUBMITTED}) | ✅ |
| 2 | POST /claims/{id}/send-back (Manager-only, guards: team/self/blank reason) + SentBackIn | ✅ |
| 3 | POST /claims/{id}/return-to-manager (Finance, APPROVED→SUBMITTED, assigned_to in feedback) + ReturnToManagerIn | ✅ |
| 4 | SENT_BACK → DRAFT (owner); resubmit counts resubmission_count in ticket | ✅ |
| 5 | Manager inert post-approval (transition matrix refuses approve/reject/send-back on APPROVED) | ✅ |
| 6 | identity_match tiers A/B/C (duplicates.py) + normalizers + anchor_compare | ✅ |
| 7 | pay_claim pipeline: A/B hard-block → C gate (typed justification ≥20 chars → DUPLICATE_DISMISSED) → D advisory; old fuzzy auto-block removed | ✅ |
| 8 | Finance payment_queue exposes duplicate_tier / duplicate_compare / matched_claim_number; claim_detail exposes duplicate_tier | ✅ |
| 9 | CAS discharge: UPDATE ... WHERE id AND status=<current>, 409 on race | ✅ |

## Phase ③ — Frontend
| # | Item | Status |
|---|---|---|
| 1 | "Returned for revision" SENT_BACK badge everywhere (StatusBadge uses STATUS_LABELS); manager-inert notice on APPROVED | ✅ |
| 2 | Feedback card + "Returned by Finance" banner from latest ticket (FeedbackCard) | ✅ |
| 3 | Action buttons per capability matrix; tier-C pay-justification modal (detail + finance dashboard) | ✅ |
| 4 | Anchor comparison panel (side-by-side rows, exact-match/mismatch/absent badges) | ✅ |
| 5 | Audit timeline renders JSON tickets (diffs, file-access rows, payment blocks, dismissals) | ✅ |
| 6 | Form: Receipt No. + Time monospace fields (new + edit), populated from extraction | ✅ |
| 7 | Finance dashboard tier chips + queue anchors; 409 "changed by another action" handling | ✅ |
| 8 | Receipt viewer: sandboxed iframe, blob-URL revocation on reload/unmount | ✅ |
| 9 | api() 422 detail array stringify | ✅ |

`npx tsc --noEmit` clean; `next build` green.

## Phase ④ — Tiered storage + cache
| # | Item | Status |
|---|---|---|
| 1 | db.py knobs: MINIO_*, S3_STORAGE_CLASS, PAID_CACHE_DAYS, COLD_S3_BUCKET_NAME, RECEIPT_CACHE_* | ✅ |
| 2 | TieredStorage(hot, cold) composite: get_content routes HOT / paid-cache / COLD and reports served_from | ✅ |
| 3 | promote() on pay success (non-blocking, idempotent, checksum-verified) + RECEIPT_ARCHIVED / ARCHIVAL_FAILED / COLD_TIER_ACCESSED tickets; payment never depends on promotion | ✅ |
| 4 | MinIO lifecycle rule (paid-cache/, 7d) applied at promotion; honest False when not object-store | ✅ |
| 5 | docker-compose MinIO service + .env.example with all new knobs | ✅ |
| 6 | receipt_cache.py TTL(1h)+LRU(64MB) wired into get_receipt_content (keyed on receipt id + checksum); per-request auth still enforced before lookup; delete-invalidation API present | ✅ (invalidation wired via ReceiptCache.invalidate_receipt; receipts app never moves raw files, so expiry is by checksum key) |
| 7 | SERVED_FROM_COLD / CACHE_EXPIRED tickets | ⚠️ COLD_TIER_ACCESSED implemented (equivalent of SERVED_FROM_COLD); CACHE_EXPIRED sweep path exists as TieredStorage.sweep_expired_paid_cache but no scheduled runner — lifecycle rule is the primary expiry |

## Phase ⑤ — Docs/README sync
| # | Item | Status |
|---|---|---|
| 1 | README: new state machine, capability matrix, ticket example, storage tiers, duplicate tiers, cache, demo script, updated tests count | ✅ |
| 2 | README: storage/PDF section rewritten to current design (legacy pages kept, rendering opt-in) | ✅ |
| 3 | IMPROVEMENT-PLAN.md marked superseded (banner overrides to FINAL-DESIGN + IMPLEMENTATION-STATUS) | ✅ |

## Gate test files
| File | Covers | Status |
|---|---|---|
| tests/test_integrity.py (7 tests) | normalization on write, diff tickets, frozen statuses, typed reject, file-access tickets, anchor extraction | ✅ |
| tests/test_sendback.py (4 tests) | send-back loop, guards, inert manager + finance return loop, spend math | ✅ |
| tests/test_payment_tiers.py (6 tests) | tier A checksum block, tier B receipt-no block despite rewording, tier C justification gate, 5-legit-daily-cab regression, tier-D advisory-only, CAS double-pay race | ✅ |
| tests/test_receipt_cache.py (5 tests) | TTL, LRU, auth semantics, delete-invalidation | ✅ |
| tests/test_storage_tiers.py (6 tests) | promote/verify/idempotent, tier routing + cache fallback, lifecycle rule install | ✅ |

**Current suite: 71 passed (43 baseline + 28 new).**
