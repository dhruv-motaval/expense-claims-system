"""Services: auth, claims workflow, state machine, reporting, audit."""
import hashlib
import hmac
import json
import os
import time as _time
from datetime import date, datetime, time as dtime, timedelta, timezone

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from . import db as db_config
from .duplicates import (content_hash, duplicate_flags, fingerprint, find_duplicates, identity_match,
                         match_dict, normalize_merchant, normalize_receipt_no, normalize_time,
                         parse_date, categorize, parse_amount,
                         anchor_compare, TIER_A, TIER_B, TIER_C, TIER_D)
from .extractors import ExtractionError, extract_from_text, extract_from_image
from .models import AuditEvent, Category, Claim, Receipt, Role, SourceType, Status, User, utcnow
from .schemas import (ClaimCreate, ClaimUpdate, ExtractedReceipt, PayIn, RejectIn,
                      ReturnToManagerIn, SentBackIn)
from .storage import get_storage, get_tiered_storage
from .receipt_cache import cache

CLAIM_FIELDS = ("merchant", "transaction_date", "trans_time", "receipt_no",
                "category", "amount", "currency", "description")

ALLOWED_TRANSITIONS = {
    Status.DRAFT: {Status.SUBMITTED},
    Status.SUBMITTED: {Status.APPROVED, Status.REJECTED, Status.SENT_BACK},
    Status.REJECTED: {Status.DRAFT},
    Status.APPROVED: {Status.PAID, Status.SUBMITTED},   # Finance return-to-manager
    Status.PAID: set(),
    Status.SENT_BACK: {Status.DRAFT, Status.SUBMITTED},
}

# Edit whitelist: only DRAFT / REJECTED / SENT_BACK are editable (SENT_BACK is the
# manager-granted revision window). SUBMITTED / APPROVED / PAID are frozen on the
# server, so an approver always sees the record Finance eventually pays.
EDITABLE_FIELDS_BY_STATUS = {
    Status.DRAFT: set(CLAIM_FIELDS),
    Status.REJECTED: set(CLAIM_FIELDS),
    Status.SENT_BACK: set(CLAIM_FIELDS),
    Status.SUBMITTED: set(),
    Status.APPROVED: set(),
    Status.PAID: set(),
}

# ponytail: in-memory sessions; restart logs users out, fine for a demo
SESSIONS: dict[str, User] = {}


class BusinessRuleError(HTTPException):
    def __init__(self, msg: str):
        super().__init__(status_code=422, detail=msg)


def login(db: Session, email: str) -> tuple[User, str]:
    user = db.scalar(select(User).where(func.lower(User.email) == email.lower().strip()))
    if not user:
        raise HTTPException(status_code=401, detail="Unknown user. Use a demo account.")
    token = hmac.new(os.getenv("SECRET_KEY", "dev").encode(), email.lower().encode(), hashlib.sha256).hexdigest()
    SESSIONS[token] = user
    return user, token


def get_user_by_token(token: str) -> User | None:
    return SESSIONS.get(token)


# --- JSON ticket engine -----------------------------------------------------

def _jsonable(v):
    """Recursively coerce DB-native types (Decimal/date/datetime) into JSON-safe
    primitives so ticket envelopes never blow up the audit insert."""
    from decimal import Decimal
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, dtime)):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def audit(db: Session, claim: "Claim | int", actor: User | None, action: str,
          meta: dict | None = None, *, transition: tuple[str, str] | None = None,
          fields: dict | None = None, feedback: dict | None = None,
          user_id: int | None = None):
    """One JSON ticket per event, written IN THE SAME TRANSACTION as the change.

    Envelope (docs/FINAL-DESIGN.md §B3): actor, transition, fields-diff, feedback.
    Accepts legacy int claim_id + user_id for compatibility with raw seeding paths.
    """
    if isinstance(claim, int):
        cid, num = claim, None
    else:
        cid, num = claim.id, claim.claim_number
    envelope: dict = {}
    if actor is not None:
        envelope["actor"] = {"id": actor.id, "name": actor.name, "role": actor.role}
    if user_id is not None and actor is None:
        envelope["actor_id"] = user_id
    if transition is not None:
        envelope["transition"] = {"from": transition[0], "to": transition[1]}
    for label, blob in (("fields", fields), ("feedback", feedback)):
        if blob:
            envelope[label] = _jsonable(blob)
    if meta:
        envelope.update(_jsonable(meta))
    envelope["at"] = utcnow().isoformat()
    envelope["claim_number"] = num
    db.add(AuditEvent(claim_id=cid, user_id=user_id if actor is None else actor.id,
                      action=action, meta=envelope))


def get_ticket_feedback(db: Session, claim: Claim, actions: tuple[str, ...]) -> dict | None:
    """Latest feedback ticket for the given actions — the current feedback IS the
    latest ticket; feedback lives in the audit JSON, not in a new column."""
    ev = db.scalars(select(AuditEvent).where(AuditEvent.claim_id == claim.id,
                                             AuditEvent.action.in_(actions)
                                             ).order_by(AuditEvent.id.desc()).limit(1)).first()
    if not ev or not isinstance(ev.meta, dict):
        return None
    return {"action": ev.action, "meta": ev.meta,
            "user_name": ev.user.name if ev.user else None,
            "created_at": ev.created_at.isoformat()}


def next_claim_number(db: Session) -> str:
    n = (db.scalar(select(func.max(Claim.id))) or 0) + 1000
    return f"CLM-{n + 1}"


def can_view(user: User, claim: Claim) -> bool:
    if user.role == Role.FINANCE.value:
        return True
    if claim.user_id == user.id:
        return True
    return user.role == Role.MANAGER.value and claim.user.manager_id == user.id


def require_claim_view(user: User, claim: Claim):
    if not can_view(user, claim):
        raise HTTPException(status_code=403, detail="You do not have access to this claim.")


def can_view_receipt(user: User, receipt: Receipt) -> bool:
    if user.role == Role.FINANCE.value:
        return True
    if receipt.user_id == user.id:
        return True
    if user.role == Role.MANAGER.value:
        if receipt.user and receipt.user.manager_id == user.id:
            return True
        for claim in getattr(receipt, "claims", []) or []:
            if claim.user and claim.user.manager_id == user.id:
                return True
    return False


def get_receipt_content(db: Session, user: User, receipt: Receipt) -> tuple[bytes, str]:
    """Authorized retrieval of receipt bytes through the tier routing.
    Auth check comes first and is NEVER cached: a cache hit can only occur
    after permission has already passed for this user's request."""
    if not can_view_receipt(user, receipt):
        raise HTTPException(status_code=403, detail="You do not have access to this receipt.")
    key = receipt.s3_image_key or receipt.image_path
    if not key:
        raise HTTPException(status_code=404, detail="No receipt image.")
    ck = f"receipt:{receipt.id}:{(receipt.checksum_sha256 or '')[:16]}" if receipt.checksum_sha256 else None
    if ck:
        cached = cache.get(ck)
        if cached is not None:
            return cached, "memory"
    tiered = get_tiered_storage()
    paid_cache_key = receipt.paid_cache_key
    tier = getattr(receipt, "storage_tier", "HOT") or "HOT"
    try:
        content, served_from = tiered.get_content(
            key, tier=tier, paid_cache_key=paid_cache_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No receipt image.")

    claims = list(db.scalars(select(Claim).where(Claim.receipt_id == receipt.id)))
    if tier == "COLD" and paid_cache_key and served_from == "cold":
        # A missing paid-cache object is an observable lifecycle event. Reconcile
        # the pointer so later reads do not repeatedly probe an expired key, then
        # retain the permanent archive as the authoritative source.
        receipt.paid_cache_key = None
        for c in claims:
            audit(db, c, user, "CACHE_EXPIRED", {
                "receipt_id": receipt.id,
                "expired_cache_key": paid_cache_key,
                "served_from": served_from,
            })
        db.commit()

    if served_from == "cold":
        # Visibility: serving straight from the archive is worth a ticket so the
        # trail shows when the paid-cache copy is gone/expired.
        for c in claims:
            audit(db, c, user, "COLD_TIER_ACCESSED", {
                "receipt_id": receipt.id, "served_from": served_from,
                "hint": "paid-cache expired or missing"})
        db.commit()
    if ck:
        cache.put(ck, content)
    return content, served_from


def maybe_promote_on_pay(db: Session, claim: Claim, receipt: Receipt | None):
    """After a successful pay: archive the receipt to the cold S3 tier
    (checksum-verified) and keep a paid-cache copy for PAID_CACHE_DAYS.
    Best-effort by design — payment never depends on promotion, and a retry
    is safe because promote() is idempotent."""
    if not receipt:
        return
    key = receipt.s3_image_key or receipt.image_path
    if not key:
        return
    fname = receipt.original_filename or (key.rsplit("/", 1)[-1] if key else None) or "receipt.bin"
    try:
        tiered = get_tiered_storage()
        if tiered.cold is None:
            return
        content = tiered.hot.get_object(key) if getattr(receipt, "storage_tier", "HOT") == "HOT" else None
        cold_key = tiered.promote(key, receipt.id, fname, content)
        receipt.storage_tier = "COLD"
        receipt.paid_cache_key = None
        try:  # paid cache only exists on an object-store hot tier (MinIO/S3)
            paid_cache_content = content if content is not None else tiered.cold.s3.get_object(
                Bucket=tiered.cold.bucket_name, Key=cold_key)["Body"].read()
            receipt.paid_cache_key = tiered.put_paid_cache(receipt.id, fname, paid_cache_content)
            tiered.apply_paid_cache_lifecycle(db_config.PAID_CACHE_DAYS)
        except Exception:
            receipt.paid_cache_key = None
        db.commit()
        for c in db.scalars(select(Claim).where(Claim.receipt_id == receipt.id)):
            audit(db, c, claim.paid_by, "RECEIPT_ARCHIVED", {
                "receipt_id": receipt.id, "cold_key": cold_key,
                "storage_class": db_config.S3_STORAGE_CLASS,
                "paid_cache_key": receipt.paid_cache_key,
                "paid_cache_days": db_config.PAID_CACHE_DAYS})
        db.commit()
    except Exception as e:
        db.rollback()
        # the archive failed but the payment stands: ticket the failure
        for c in db.scalars(select(Claim).where(Claim.receipt_id == receipt.id)):
            audit(db, c, claim.paid_by, "ARCHIVAL_FAILED", {
                "receipt_id": receipt.id, "error": str(e)[:400]})
        db.commit()


# --- file type detection -------------------------------------------------

IMAGE_MIME_TYPES = ("image/jpeg", "image/png", "image/webp")

_PDF_MAGIC = b"%PDF-"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_WEBP_MAGIC = b"RIFF"


def _is_pdf_signature(content: bytes) -> bool:
    return bool(content) and content[:5] == _PDF_MAGIC


def _is_image_signature(content: bytes) -> bool:
    return (content[:3] == _JPEG_MAGIC
            or content[:8] == _PNG_MAGIC
            or (content[:4] == _WEBP_MAGIC and content[8:12] == b"WEBP"))


def _is_pdf(content: bytes, filename: str, content_type: str | None = None) -> bool:
    if _is_pdf_signature(content):
        return True
    if filename and filename.lower().endswith(".pdf"):
        return True
    return content_type == "application/pdf"


def _is_image(content_type: str | None, content: bytes) -> bool:
    if content_type in IMAGE_MIME_TYPES:
        return True
    return _is_image_signature(content)


def _sniff_image_type(content: bytes) -> str | None:
    if content[:3] == _JPEG_MAGIC:
        return "image/jpeg"
    if content[:8] == _PNG_MAGIC:
        return "image/png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def _pdf_magic_ok(content: bytes) -> bool:
    return _is_pdf_signature(content)


# --- extraction ----------------------------------------------------------

def extract(db: Session, user: User, text: str) -> dict:
    if not (text or "").strip():
        raise BusinessRuleError("No receipt text found. Enter the claim manually.")
    try:
        r: ExtractedReceipt = extract_from_text(text)
    except ExtractionError as e:
        raise BusinessRuleError(str(e))
    receipt = _store_artifacts(db, user, r, original_text=text)
    return {"receipt_id": receipt.id,
            "duplicate_matches": _duplicates(db, r), **r.model_dump(mode="json")}


async def extract_upload(db: Session, user: User, file: UploadFile) -> dict:
    max_size = db_config.UPLOAD_MAX_SIZE_MB * 1024 * 1024
    content = await file.read()
    if len(content) > max_size:
        raise BusinessRuleError(f"File too large. Max size {db_config.UPLOAD_MAX_SIZE_MB}MB.")
    if not content:
        raise BusinessRuleError("Empty file. Upload a receipt image or PDF.")

    filename = file.filename or ""
    if _is_pdf(content, filename, file.content_type):
        return await _extract_pdf_upload(db, user, content, filename)

    sniffed = _sniff_image_type(content)
    if file.content_type not in IMAGE_MIME_TYPES and sniffed is None:
        raise BusinessRuleError("Upload a jpg, png, webp receipt image or a PDF receipt.")
    content_type = file.content_type if file.content_type in IMAGE_MIME_TYPES else (sniffed or file.content_type)
    try:
        r = await extract_from_image(content, content_type)
    except ExtractionError as e:
        raise BusinessRuleError(str(e))
    receipt = _store_artifacts(db, user, r, image_content=content, image_content_type=content_type,
                               original_filename=filename)
    return {"receipt_id": receipt.id,
            "duplicate_matches": _duplicates(db, r), **r.model_dump(mode="json")}


def _pdf_processor() -> "PDFProcessor":
    from .pdf_processor import PDFProcessor

    return PDFProcessor(
        max_pages=int(os.getenv("PDF_MAX_PAGES", "10")),
        dpi=int(os.getenv("PDF_RENDER_DPI", "180")),
        jpeg_quality=int(os.getenv("PDF_JPEG_QUALITY", "80")),
        max_size_mb=db_config.UPLOAD_MAX_SIZE_MB,
    )


def _extract_pdf_fields(pdf_bytes: bytes):
    """Extract structured fields from a PDF via embedded text (local only).

    Returns (ExtractedReceipt, PDFProcessingResult) so page images and the
    extraction snapshot can be persisted as artifacts by the caller.
    """
    processor = _pdf_processor()
    result = processor.process_pdf(pdf_bytes)
    text = (result.embedded_text or "").strip()
    if not text.strip():
        # Optional local OCR fallback (requires Tesseract; not required for tests).
        try:
            from .ocr import ocr_pdf_pages  # optional dependency
            text = (ocr_text := ocr_pdf_pages(pdf_bytes, processor.dpi) or "").strip()
        except Exception:
            text = ""
    if not text.strip():
        raise ExtractionError("No readable text found in the PDF. Enter the claim manually.")
    try:
        r: ExtractedReceipt = extract_from_text(text)
    except ExtractionError as e:
        raise BusinessRuleError(str(e))
    r.raw_text = text
    return r, result


async def _extract_pdf_upload(db: Session, user: User, content: bytes, filename: str) -> dict:
    if len(content) > db_config.UPLOAD_MAX_SIZE_MB * 1024 * 1024:
        raise BusinessRuleError(f"File too large. Max size {db_config.UPLOAD_MAX_SIZE_MB}MB.")
    if not _pdf_magic_ok(content):
        raise BusinessRuleError("Invalid or corrupted PDF file.")
    try:
        r, pdf_result = _extract_pdf_fields(content)
    except BusinessRuleError:
        raise
    except ValueError as e:
        raise BusinessRuleError(str(e))
    receipt = _store_artifacts(db, user, r, pdf_content=content, pdf_result=pdf_result,
                               original_filename=filename)
    return {"receipt_id": receipt.id,
            "duplicate_matches": _duplicates(db, r), **r.model_dump(mode="json")}


def _duplicates(db: Session, r: ExtractedReceipt) -> list[dict]:
    return [match_dict(m) for m in find_duplicates(
        db, {"merchant": r.merchant, "date": r.date,
             "amount": r.amount, "description": r.description})]


def _ext_for(content_type: str) -> str:
    return {
        "application/pdf": "pdf",
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get(content_type, "bin")


def _store_artifacts(db: Session, user: User, extracted: ExtractedReceipt,
                     original_text: str | None = None,
                     image_content: bytes | None = None,
                     image_content_type: str | None = None,
                     original_filename: str | None = None,
                     pdf_content: bytes | None = None,
                     pdf_result=None) -> Receipt:
    """Store the canonical original receipt file in S3 and the structured
    extraction result in PostgreSQL.

    New records keep ONE private S3 object under a per-receipt key space:

        receipts/<receipt-id>/original.pdf   (or original.jpg/png/webp by type)

    Extracted structured data, file metadata (filename, content type, size,
    sha256 checksum), processing status and workflow data live in PostgreSQL.
    Page images, OCR text and extracted JSON are rendered/parsed transiently
    in memory for extraction; they are NOT persisted to S3 for new uploads
    (legacy receipts that already have those artifacts keep working).

    The receipt row is flushed first to obtain the id used in the object key.
    If any later step fails, every object uploaded during this call is deleted
    best-effort and the transaction is rolled back - no silent fallback, no
    orphaned objects, and no DB record without its canonical file.
    """
    import hashlib

    storage = get_storage()
    is_s3 = db_config.STORAGE_BACKEND == "s3"
    uploaded_keys: list[str] = []

    if pdf_content is not None:
        content = pdf_content
        content_type = "application/pdf"
        ext = "pdf"
        source_type = "PDF"
    elif image_content is not None:
        content = image_content
        sniffed = _sniff_image_type(image_content)
        content_type = sniffed or image_content_type or "image/jpeg"
        ext = _ext_for(content_type)
        source_type = SourceType.IMAGE.value
    else:
        content = None
        ext = ""
        content_type = None
        source_type = SourceType.TEXT.value

    checksum = hashlib.sha256(content).hexdigest() if content else None
    extracted_payload = {**extracted.model_dump(mode="json"),
                         "extraction_status": "completed" if extracted.amount is not None else "partial"}
    if pdf_result is not None:
        extracted_payload["page_count"] = pdf_result.page_count
    r = Receipt(
        user_id=user.id,
        source_type=source_type,
        original_text=original_text,
        merchant=extracted.merchant,
        receipt_date=extracted.date,
        amount=extracted.amount,
        currency=extracted.currency,
        raw_extracted_text=extracted.raw_text,
        normalized_hash=content_hash(extracted.raw_text),
        content_hash=fingerprint(extracted.merchant, extracted.date, extracted.amount,
                                 extracted.currency, extracted.description),
        receipt_no=normalize_receipt_no(extracted.receipt_no),
        trans_time=normalize_time(extracted.time),
        original_filename=original_filename,
        content_type=content_type,
        file_size=len(content) if content else None,
        checksum_sha256=checksum,
        extracted_data=extracted_payload,
        storage_tier="HOT",
        processing_status="completed",
    )
    db.add(r)
    db.flush()  # assign r.id for the canonical object key

    def _cleanup():
        for k in uploaded_keys:
            try:
                get_storage().delete_object(k)
            except Exception:
                pass
        db.rollback()

    try:
        if content is not None:
            obj = storage.upload_artifact(r.id, f"original.{ext}", content, content_type)
            uploaded_keys.append(obj.key)
            if is_s3:
                r.s3_image_key = obj.key  # canonical original object key
            else:
                r.image_path = obj.key
        # NOTE: extracted JSON lives in PostgreSQL (extracted_data), not S3.
        # Page images and OCR text are rendered/parsed temporarily in memory
        # for extraction and are not persisted for new records.

        db.commit()
    except BusinessRuleError:
        _cleanup()
        raise
    except Exception:
        _cleanup()
        raise BusinessRuleError("Receipt storage failed. Please retry.")

    db.refresh(r)
    return r


# --- claims --------------------------------------------------------------

CREATE_ATTEMPTS = 4


def create_claim(db: Session, user: User, data: ClaimCreate) -> Claim:
    if data.receipt_id:
        receipt = db.get(Receipt, data.receipt_id)
        if not receipt or receipt.user_id != user.id:
            raise BusinessRuleError("Invalid receipt reference.")
    claim = Claim(
        user_id=user.id,
        merchant=data.merchant.strip(),
        transaction_date=data.transaction_date,
        category=data.category,
        amount=data.amount,
        currency=data.currency,
        description=data.description,
        receipt_no=normalize_receipt_no(data.receipt_no),
        trans_time=normalize_time(data.trans_time),
        receipt_id=data.receipt_id,
        status=Status.DRAFT.value,
    )
    # claim_number comes from max(id) — under concurrency two creates can collide
    # on the unique constraint; retry instead of a 500.
    from sqlalchemy.exc import IntegrityError
    lasterr: Exception | None = None
    for _ in range(CREATE_ATTEMPTS):
        claim.claim_number = next_claim_number(db)
        try:
            db.add(claim)
            db.commit()
            db.refresh(claim)
            audit(db, claim, user, "CLAIM_CREATED")
            db.commit()
            if data.submit:
                return submit_claim(db, user, claim)["claim"]
            return claim
        except IntegrityError:
            db.rollback()
            lasterr = None  # unique claim_number collision: retry with a fresh max
        except Exception as e:
            db.rollback()
            raise
    raise BusinessRuleError("Could not allocate a claim number, please retry.")


def update_claim(db: Session, user: User, claim: Claim, data: ClaimUpdate) -> Claim:
    editable = EDITABLE_FIELDS_BY_STATUS.get(Status(claim.status), set())
    attempted = set(data.model_dump(exclude_unset=True))
    disallowed = attempted - editable
    is_owner = claim.user_id == user.id
    if not is_owner:
        # Only the owner can edit. An authorized non-owner (Finance) probing a
        # frozen claim gets the explicit frozen refusal (422), everyone else 403.
        if disallowed and user.role == Role.FINANCE.value:
            audit(db, claim, user, "CLAIM_EDIT_BLOCKED",
                  {"attempted_fields": sorted(attempted), "status": claim.status})
            db.commit()
            raise BusinessRuleError(f"A {claim.status} claim cannot be edited. It is already decided.")
        raise HTTPException(status_code=403, detail="Only the claim owner can edit it.")
    if disallowed:
        # every editable status leaves disallowed == {} (whitelist covers all
        # fields there), so a non-empty remainder means the status is frozen
        audit(db, claim, user, "CLAIM_EDIT_BLOCKED",
              {"attempted_fields": sorted(attempted), "status": claim.status})
        db.commit()
        why = {
            Status.SUBMITTED.value: " It is with your reviewer \u2014 wait for approval, "
                                    "rejection or a send-back.",
            Status.APPROVED.value: " It is frozen after approval.",
            Status.PAID.value: " It is already paid and immutable.",
        }.get(claim.status, "")
        raise BusinessRuleError(f"A {claim.status} claim cannot be edited.{why}")
    fields_diff = {}
    for field in attempted:
        old = getattr(claim, field)
        new = getattr(data, field)
        if field in ("receipt_no",):
            new = normalize_receipt_no(new) if new is not None else None
        if field in ("trans_time",):
            new = normalize_time(new) if new is not None else None
        if (old is None and new is not None) or (old is not None and new != old):
            fields_diff[field] = {"old": old, "new": new}
    if fields_diff:
        for field, ch in fields_diff.items():
            setattr(claim, field, ch["new"])
        db.commit()
        db.refresh(claim)
        audit(db, claim, user, "CLAIM_UPDATED", fields=fields_diff)
        db.commit()
    return claim


def transition(db: Session, claim: Claim, to: Status, actor: User,
               meta: dict | None = None, *, feedback: dict | None = None,
               action: str | None = None) -> Claim:
    """CAS transition: the row is updated only if it still has the status we
    validated against — a concurrent approve/pay/reject yields 409, never a
    silent double transition. One ticket per transition, one commit."""
    current = Status(claim.status)
    if to not in ALLOWED_TRANSITIONS[current]:
        raise BusinessRuleError(f"Cannot move a {current.value} claim to {to.value}.")
    now = utcnow()
    values = {"status": to.value, "updated_at": now}
    if to == Status.SUBMITTED:
        values["submitted_at"] = now
    elif to == Status.APPROVED:
        values["approved_at"] = now
        values["approved_by_id"] = actor.id
    elif to == Status.PAID:
        values["paid_at"] = now
        values["paid_by_id"] = actor.id
    res = db.execute(update(Claim).where(Claim.id == claim.id, Claim.status == current.value)
                     .values(**values))
    if res.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409,
                            detail="Claim was just changed by another action. Refresh and retry.")
    db.refresh(claim)
    claim.status = to.value
    if to == Status.SUBMITTED:
        claim.submitted_at = now
    elif to == Status.APPROVED:
        claim.approved_at = now
        claim.approved_by_id = actor.id
    elif to == Status.PAID:
        claim.paid_at = now
        claim.paid_by_id = actor.id
    elif to == Status.REJECTED and meta:
        claim.rejection_reason = (meta or {}).get("reason")
    audit(db, claim, actor, action or f"CLAIM_{to.value}", meta, transition=(current.value, to.value),
          feedback=feedback)
    db.commit()
    db.refresh(claim)
    return claim


def submit_claim(db: Session, user: User, claim: Claim) -> dict:
    if claim.user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the claim owner can submit it.")
    prior_submissions = sum(1 for a in claim.audits if a.action == "CLAIM_SUBMITTED")
    matches = find_duplicates(db, {"merchant": claim.merchant, "date": claim.transaction_date,
                                   "amount": float(claim.amount), "description": claim.description},
                              exclude_id=claim.id)
    transition(db, claim, Status.SUBMITTED, user, {"resubmission_count": prior_submissions + 1})
    if matches:
        audit(db, claim, user, "DUPLICATE_WARNING",
              {"matches": [m["claim"].claim_number for m in matches]})
        db.commit()
    return {"claim": claim, "duplicate_matches": [match_dict(m) for m in matches]}


def approve_claim(db: Session, manager: User, claim: Claim) -> Claim:
    if manager.role != Role.MANAGER.value:
        raise HTTPException(status_code=403, detail="Manager access required.")
    if claim.status != Status.SUBMITTED.value:
        raise BusinessRuleError("Only submitted claims can be approved.")
    if claim.user_id == manager.id:
        raise BusinessRuleError("Managers cannot approve their own claims.")
    if claim.user.manager_id != manager.id:
        raise BusinessRuleError("You cannot approve claims outside your team.")
    return transition(db, claim, Status.APPROVED, manager)


def reject_claim(db: Session, manager: User, claim: Claim, reason: str) -> Claim:
    if manager.role != Role.MANAGER.value:
        raise HTTPException(status_code=403, detail="Manager access required.")
    if claim.status != Status.SUBMITTED.value:
        raise BusinessRuleError("Only submitted claims can be rejected.")
    if claim.user_id == manager.id:
        raise BusinessRuleError("Managers cannot reject their own claims.")
    if claim.user.manager_id != manager.id:
        raise BusinessRuleError("You cannot reject claims outside your team.")
    if not (reason or "").strip():
        raise BusinessRuleError("A rejection reason is required.")
    return transition(db, claim, Status.REJECTED, manager, {"reason": reason.strip()})


def send_back_claim(db: Session, manager: User, claim: Claim, data: SentBackIn) -> Claim:
    """Manager finds mistakes and sends the claim back to staff for revision.
    This is the only path that re-opens edit rights (whitelist SENT_BACK)."""
    if manager.role != Role.MANAGER.value:
        raise HTTPException(status_code=403, detail="Manager access required.")
    if claim.status != Status.SUBMITTED.value:
        raise BusinessRuleError("Only submitted claims can be sent back for revision.")
    if claim.user_id == manager.id:
        raise BusinessRuleError("Managers cannot send back their own claims.")
    if claim.user.manager_id != manager.id:
        raise BusinessRuleError("You cannot send back claims outside your team.")
    return transition(db, claim, Status.SENT_BACK, manager, feedback={
        "reason": data.reason.strip(),
        "flagged_fields": data.flagged_fields or [],
        "comment": (data.comment or "").strip() or None,
    })


def return_to_manager(db: Session, finance_user: User, claim: Claim, data: ReturnToManagerIn) -> Claim:
    """Finance pulls an APPROVED claim back into its manager's queue for re-review.
    Finance never contacts staff directly; the manager is the gate for revision."""
    if finance_user.role != Role.FINANCE.value:
        raise HTTPException(status_code=403, detail="Finance access required.")
    if claim.status != Status.APPROVED.value:
        raise BusinessRuleError("Only approved claims can be returned to the manager.")
    feedback = {
        "reason": data.reason.strip(),
        "flagged_fields": data.flagged_fields or [],
        "comment": (data.comment or "").strip() or None,
        "assigned_to": None,
    }
    if claim.user and claim.user.manager:
        feedback["assigned_to"] = {"id": claim.user.manager.id, "name": claim.user.manager.name,
                                   "role": claim.user.manager.role}
    return transition(db, claim, Status.SUBMITTED, finance_user, feedback=feedback,
                      action="CLAIM_RETURNED_TO_MANAGER")


def return_to_draft(db: Session, user: User, claim: Claim) -> Claim:
    if claim.user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the claim owner can edit it.")
    if claim.status == Status.SENT_BACK.value:
        return transition(db, claim, Status.DRAFT, user)
    return transition(db, claim, Status.DRAFT, user)


def pay_claim(db: Session, finance_user: User, claim: Claim, data: PayIn | None = None) -> Claim:
    """Tiered identity gate (docs/FINAL-DESIGN.md §B7): A/B hard-block, C requires
    a typed justification, D is advisory chips only. Final move is a CAS UPDATE."""
    if finance_user.role != Role.FINANCE.value:
        raise HTTPException(status_code=403, detail="Finance access required.")
    if claim.status != Status.APPROVED.value:
        raise BusinessRuleError("Only approved claims can be paid.")

    tier, matched, compared = identity_match(db, claim)
    if tier in (TIER_A, TIER_B):
        audit(db, claim, finance_user, "PAYMENT_BLOCKED", {
            "tier": tier, "basis": "checksum" if tier == TIER_A else "receipt_no",
            "compared": compared,
            "matched": {"claim": matched.claim_number, "merchant": matched.merchant,
                        "date": matched.transaction_date.isoformat(),
                        "amount": float(matched.amount), "receipt_no": matched.receipt_no},
        })
        db.commit()
        raise BusinessRuleError(
            f"Payment blocked: a similar receipt has already been paid ({matched.claim_number})."
            + ("" if tier == TIER_A else " Same receipt number has already been paid."))
    if tier == TIER_C and not (data and (data.justification or "").strip()):
        audit(db, claim, finance_user, "PAYMENT_BLOCKED", {
            "tier": tier, "basis": "transaction",
            "hint": "pay-with-justification", "compared": compared,
            "matched": {"claim": matched.claim_number, "receipt_no": matched.receipt_no},
        })
        db.commit()
        raise BusinessRuleError(
            f"Possible duplicate: claim {matched.claim_number} has already been paid with the same "
            "merchant, date and amount. To pay anyway, add a justification (min 20 characters).")
    if tier == TIER_C:
        audit(db, claim, finance_user, "DUPLICATE_DISMISSED",
              {"justification": data.justification.strip(), "compared": compared,
               "matched": {"claim": matched.claim_number, "receipt_no": matched.receipt_no}})
        db.commit()
    paid = transition(db, claim, Status.PAID, finance_user)
    # promotion is deliberately AFTER the committed payment: money left, even
    # if the archive leg fails; the failure gets its own ticket.
    try:
        maybe_promote_on_pay(db, paid, paid.receipt)
    except Exception:
        pass
    return paid


def log_file_access(db: Session, user: User, receipt: Receipt, served_from: str = "hot"):
    """RECEIPT_FILE_ACCESSED tickets: proof the reviewer actually opened the file.
    Authorization must already have passed. Only claim-linked receipts are
    ticketed (claim_id is not nullable in audit_events)."""
    from .models import Claim as _Claim
    linked = list(db.scalars(select(_Claim).where(_Claim.receipt_id == receipt.id)))
    today = datetime.now(timezone.utc).date()
    if not linked:
        return
    meta = {
        "receipt_id": receipt.id,
        "filename": receipt.original_filename or (receipt.s3_image_key or receipt.image_path),
        "content_type": receipt.content_type,
        "tier": getattr(receipt, "storage_tier", "HOT"),
        "served_from": served_from,
        "purpose": "verification",
    }
    for c in linked:
        audit(db, c, user, "RECEIPT_FILE_ACCESSED", meta)
    db.commit()


# --- spend / limits / reports -------------------------------------------

def month_bounds(ref: date | None = None) -> tuple[date, date]:
    # UTC-stable so billing periods don't shift at the month boundary on servers
    # whose local timezone is not UTC.
    first = (ref or datetime.now(timezone.utc).date()).replace(day=1)
    nxt = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, nxt


def monthly_spend(db: Session, user_id: int, ref: date | None = None) -> float:
    first, nxt = month_bounds(ref)
    return float(db.scalar(select(func.coalesce(func.sum(Claim.amount), 0)).where(
        Claim.user_id == user_id,
        Claim.status.in_([Status.APPROVED.value, Status.PAID.value]),
        Claim.transaction_date >= first, Claim.transaction_date < nxt,
    )) or 0)


def limit_status(spend: float, limit: float) -> dict:
    pct = (spend / limit * 100) if limit else 0
    level = "NORMAL" if pct < 80 else "NEAR_LIMIT" if pct < 100 else "OVER_LIMIT"
    return {"spend": spend, "limit": limit, "remaining": round(limit - spend, 2),
            "percent": round(pct, 1), "status": level}


def staff_dashboard(db: Session, user: User) -> dict:
    first, nxt = month_bounds()
    claims = list(db.scalars(select(Claim).where(Claim.user_id == user.id)
                             .order_by(Claim.id.desc()).limit(20)))
    return {
        "limit": limit_status(monthly_spend(db, user.id), float(user.monthly_limit)),
        "claims_total": len(claims),
        "pending_approval": sum(1 for c in claims if c.status == Status.SUBMITTED.value),
        "paid": sum(1 for c in claims if c.status == Status.PAID.value),
        "recent_claims": [claim_dict(c) for c in claims],
    }


def manager_dashboard(db: Session, user: User) -> dict:
    team_ids = [u.id for u in db.scalars(select(User).where(User.manager_id == user.id))]
    claims = list(db.scalars(select(Claim).where(Claim.user_id.in_(team_ids + [user.id]))
                             .order_by(Claim.id.desc()).limit(100)))
    first, _ = month_bounds()
    team_only = [c for c in claims if c.user_id != user.id]

    def latest_audit_action(claim_id: int) -> str | None:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.claim_id == claim_id)
                        .order_by(AuditEvent.id.desc()).limit(1)).first()
        return ev.action if ev else None

    # Split what needs the manager's attention into two buckets: brand-new
    # requests vs claims finance pushed back for re-review. A returned claim
    # is a SUBMITTED claim whose latest audit event is the finance return.
    finance_returns: dict[int, dict] = {}
    for c in team_only:
        if c.status == Status.SUBMITTED.value and latest_audit_action(c.id) == "CLAIM_RETURNED_TO_MANAGER":
            fb = get_ticket_feedback(db, c, ("CLAIM_RETURNED_TO_MANAGER",))
            if fb:
                finance_returns[c.id] = fb

    claims_out = [claim_dict(c) for c in claims]
    for d in claims_out:
        fb = finance_returns.get(d["id"])
        if fb:
            ticket = fb["meta"].get("feedback") or {}
            d["returned_from_finance"] = True
            d["finance_feedback"] = {"reason": ticket.get("reason"),
                                     "flagged_fields": ticket.get("flagged_fields") or []}
            d["returned_by"] = fb["user_name"]
            d["returned_at"] = fb["created_at"]

    return {
        "pending_approval": sum(1 for c in team_only if c.status == Status.SUBMITTED.value),
        "new_requests": [d for d in claims_out if d["status"] == Status.SUBMITTED.value and not d.get("returned_from_finance")],
        "returned_from_finance": [d for d in claims_out if d.get("returned_from_finance")],
        "approved_this_month": sum(1 for c in team_only if c.status == Status.APPROVED.value and c.approved_at and c.approved_at.date() >= first),
        "rejected": sum(1 for c in team_only if c.status == Status.REJECTED.value),
        "team_spend": sum(float(c.amount) for c in team_only if c.status in (Status.APPROVED.value, Status.PAID.value) and first <= c.transaction_date < month_bounds()[1]),
        "team_members": [{"id": u.id, "name": u.name, "limit": float(u.monthly_limit),
                          **limit_status(monthly_spend(db, u.id), float(u.monthly_limit))}
                         for u in db.scalars(select(User).where(User.manager_id == user.id))],
        "claims": claims_out,
    }


def finance_dashboard(db: Session, user: User) -> dict:
    first, nxt = month_bounds()
    claims = list(db.scalars(select(Claim).order_by(Claim.id.desc()).limit(200)))
    month = [c for c in claims if first <= c.transaction_date < nxt]
    approved_spend = sum(float(c.amount) for c in month if c.status in (Status.APPROVED.value, Status.PAID.value))
    paid_spend = sum(float(c.amount) for c in month if c.status == Status.PAID.value)
    awaiting = sum(float(c.amount) for c in month if c.status == Status.APPROVED.value)
    employees = [{
        "id": u.id, "name": u.name, "role": u.role,
        **limit_status(monthly_spend(db, u.id), float(u.monthly_limit)),
        "projected": float(db.scalar(select(func.coalesce(func.sum(Claim.amount), 0)).where(
            Claim.user_id == u.id, Claim.status == Status.SUBMITTED.value,
            Claim.transaction_date >= first, Claim.transaction_date < nxt)) or 0),
    } for u in db.scalars(select(User))]
    from .duplicates import identity_match
    queue = []
    for c in claims:
        if c.status == Status.APPROVED.value:
            entry = claim_dict(c)
            tier, matched, compared = identity_match(db, c)
            entry["duplicate_tier"] = tier
            if tier:
                entry["duplicate_compare"] = compared
                entry["matched_claim_number"] = matched.claim_number if matched else None
            queue.append(entry)
    return {
        "monthly_spend": approved_spend,
        "awaiting_payment": awaiting,
        "paid_this_month": paid_spend,
        "over_limit": sum(1 for e in employees if e["status"] == "OVER_LIMIT"),
        "employees": employees,
        "payment_queue": queue,
        "categories": spend_by_category(db),
    }


def spend_by_category(db: Session) -> list[dict]:
    first, nxt = month_bounds()
    rows = db.execute(select(Claim.category, func.coalesce(func.sum(Claim.amount), 0)).where(
        Claim.status.in_([Status.APPROVED.value, Status.PAID.value]),
        Claim.transaction_date >= first, Claim.transaction_date < nxt,
    ).group_by(Claim.category)).all()
    return [{"category": cat, "total": float(total)} for cat, total in rows]


def reports(db: Session) -> dict:
    first, nxt = month_bounds()
    claims = list(db.scalars(select(Claim).where(Claim.transaction_date >= first, Claim.transaction_date < nxt)))
    counts: dict[str, int] = {}
    for c in claims:
        counts[c.status] = counts.get(c.status, 0) + 1
    return {
        "month": first.strftime("%B %Y"),
        "total_claims": len(claims),
        "by_status": counts,
        "categories": spend_by_category(db),
    }


def claim_dict(c: Claim) -> dict:
    rec = c.receipt
    return {
        "id": c.id, "claim_number": c.claim_number, "user_id": c.user_id,
        "user_name": c.user.name if c.user else None, "merchant": c.merchant,
        "transaction_date": c.transaction_date.isoformat(), "category": c.category,
        "amount": float(c.amount), "currency": c.currency, "description": c.description,
        "receipt_no": c.receipt_no, "trans_time": c.trans_time,
        "status": c.status, "rejection_reason": c.rejection_reason,
        "has_receipt_image": bool(rec and (rec.image_path or rec.s3_image_key)),
        # The receipt file travels with the ticket: id + where the bytes live
        # right now (hot local path or cold S3 key), so every dashboard, report
        # and archive export knows exactly where the original is. When storage
        # moves the file (hot → cold), these fields update automatically.
        "receipt_id": rec.id if rec else None,
        "receipt_file": (rec.s3_image_key or rec.image_path) if rec else None,
        "receipt_storage_tier": rec.storage_tier if rec else None,
        "receipt_filename": rec.original_filename if rec else None,
        "receipt_content_type": rec.content_type if rec else None,
        "receipt_file_size": rec.file_size if rec else None,
        "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None,
        "approved_at": c.approved_at.isoformat() if c.approved_at else None,
        "paid_at": c.paid_at.isoformat() if c.paid_at else None,
    }


def claim_detail(db: Session, user: User, claim: Claim) -> dict:
    """Lightweight core ticket: identity, status, receipt POINTER, feedback and
    an audit summary. Every heavy payload (OCR text, extracted data, audit
    metadata, duplicate analysis) loads on demand from its own endpoint, so
    opening a ticket never drags big blobs along."""
    require_claim_view(user, claim)
    d = claim_dict(claim)
    d["receipt"] = None
    if claim.receipt:
        rec = claim.receipt
        d["receipt"] = {
            "id": rec.id,
            "source_type": rec.source_type,
            "image_path": rec.image_path,
            "s3_image_key": rec.s3_image_key,
            "original_filename": rec.original_filename,
            "content_type": rec.content_type,
            "file_size": rec.file_size,
            "checksum_sha256": rec.checksum_sha256,
            "processing_status": rec.processing_status,
            "has_pages": bool(rec.s3_pages_keys),
            "storage_tier": getattr(rec, "storage_tier", "HOT"),
            # NOTE: extracted_data and original_text intentionally excluded —
            # they arrive via /claims/{id}/receipt-data when the user asks.
        }
    d["latest_feedback"] = get_ticket_feedback(
        db, claim, ("CLAIM_SENT_BACK", "CLAIM_RETURNED_TO_MANAGER"))
    d["duplicate_tier"] = None
    tier, _, _ = identity_match(db, claim)
    if tier:
        d["duplicate_tier"] = tier
    # audit summary WITHOUT payloads: action + actor + time only. Full audit
    # (with metadata / JSON payloads) loads via /claims/{id}/audit.
    d["audit"] = [{
        "action": a.action,
        "user_name": a.user.name if a.user else None,
        "created_at": a.created_at.isoformat(),
    } for a in claim.audits]
    return d


def claim_receipt_data(db: Session, user: User, claim: Claim) -> dict:
    """Lazy section 1: extracted receipt data + original OCR text."""
    require_claim_view(user, claim)
    if not claim.receipt:
        raise HTTPException(status_code=404, detail="No receipt on this claim.")
    return {
        "receipt_id": claim.receipt.id,
        "extracted_data": claim.receipt.extracted_data or {},
        "original_text": claim.receipt.original_text,
    }


def claim_audit_full(db: Session, user: User, claim: Claim) -> list[dict]:
    """Lazy section 2: full audit trail including metadata payloads."""
    require_claim_view(user, claim)
    return [{
        "action": a.action, "meta": a.meta,
        "user_name": a.user.name if a.user else None,
        "created_at": a.created_at.isoformat(),
    } for a in claim.audits]


def claim_duplicates(db: Session, user: User, claim: Claim) -> dict:
    """Lazy section 3: duplicate warnings + payment anchor comparison."""
    require_claim_view(user, claim)
    tier, matched, compared = identity_match(db, claim)
    return {
        "tier": tier,
        "matched_claim_number": matched.claim_number if matched else None,
        "compared": compared or None,
        "matches": duplicate_flags(db, claim, exclude_id=claim.id),
    }
