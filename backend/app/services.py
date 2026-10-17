"""Services: auth, claims workflow, state machine, reporting, audit."""
import hashlib
import hmac
import json
import os
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import db as db_config
from .duplicates import (content_hash, fingerprint, find_duplicates, has_paid_duplicate,
                         match_dict, normalize_merchant, parse_date, categorize, parse_amount)
from .extractors import ExtractionError, extract_from_text, extract_from_image
from .models import AuditEvent, Category, Claim, Receipt, Role, SourceType, Status, User, utcnow
from .schemas import ClaimCreate, ClaimUpdate, ExtractedReceipt
from .storage import get_storage

ALLOWED_TRANSITIONS = {
    Status.DRAFT: {Status.SUBMITTED},
    Status.SUBMITTED: {Status.APPROVED, Status.REJECTED},
    Status.REJECTED: {Status.DRAFT},
    Status.APPROVED: {Status.PAID},
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


def audit(db: Session, claim_id: int, user_id: int | None, action: str, meta: dict | None = None):
    db.add(AuditEvent(claim_id=claim_id, user_id=user_id, action=action, meta=meta))


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


def get_receipt_content(db: Session, user: User, receipt: Receipt) -> bytes:
    """Authorized retrieval of stored receipt bytes. Auth check comes first."""
    if not can_view_receipt(user, receipt):
        raise HTTPException(status_code=403, detail="You do not have access to this receipt.")
    storage = get_storage()
    key = receipt.s3_image_key or receipt.image_path
    if not key:
        raise HTTPException(status_code=404, detail="No receipt image.")
    try:
        return storage.get_object(key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No receipt image.")


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
        extracted_payload["page_count"] = len(pdf_result.page_images or [])
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
        original_filename=original_filename,
        content_type=content_type,
        file_size=len(content) if content else None,
        checksum_sha256=checksum,
        extracted_data=extracted_payload,
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

def create_claim(db: Session, user: User, data: ClaimCreate) -> Claim:
    if data.receipt_id:
        receipt = db.get(Receipt, data.receipt_id)
        if not receipt or receipt.user_id != user.id:
            raise BusinessRuleError("Invalid receipt reference.")
    claim = Claim(
        claim_number=next_claim_number(db),
        user_id=user.id,
        merchant=data.merchant.strip(),
        transaction_date=data.transaction_date,
        category=data.category,
        amount=data.amount,
        currency=data.currency,
        description=data.description,
        receipt_id=data.receipt_id,
        status=Status.DRAFT.value,
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    audit(db, claim.id, user.id, "CLAIM_CREATED")
    db.commit()
    if data.submit:
        return submit_claim(db, user, claim)["claim"]
    return claim


def update_claim(db: Session, user: User, claim: Claim, data: ClaimUpdate) -> Claim:
    require_claim_view(user, claim)
    if claim.status == Status.PAID.value:
        raise BusinessRuleError("Paid claims cannot be changed.")
    if claim.user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the claim owner can edit it.")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(claim, field, value)
    db.commit()
    db.refresh(claim)
    audit(db, claim.id, user.id, "CLAIM_UPDATED")
    db.commit()
    return claim


def transition(db: Session, claim: Claim, to: Status, actor: User, meta: dict | None = None) -> Claim:
    current = Status(claim.status)
    if to not in ALLOWED_TRANSITIONS[current]:
        raise BusinessRuleError(f"Cannot move a {current.value} claim to {to.value}.")
    claim.status = to.value
    now = utcnow()
    if to == Status.SUBMITTED:
        claim.submitted_at = now
    elif to == Status.APPROVED:
        claim.approved_at = now
        claim.approved_by_id = actor.id
    elif to == Status.PAID:
        claim.paid_at = now
        claim.paid_by_id = actor.id
    elif to == Status.REJECTED and meta:
        claim.rejection_reason = meta.get("reason")
    audit(db, claim.id, actor.id, f"CLAIM_{to.value}", meta)
    db.commit()
    db.refresh(claim)
    return claim


def submit_claim(db: Session, user: User, claim: Claim) -> dict:
    if claim.user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the claim owner can submit it.")
    matches = find_duplicates(db, {"merchant": claim.merchant, "date": claim.transaction_date,
                                   "amount": float(claim.amount), "description": claim.description},
                              exclude_id=claim.id)
    transition(db, claim, Status.SUBMITTED, user)
    if matches:
        audit(db, claim.id, user.id, "DUPLICATE_WARNING", {"matches": [m["claim"].claim_number for m in matches]})
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


def pay_claim(db: Session, finance_user: User, claim: Claim) -> Claim:
    if finance_user.role != Role.FINANCE.value:
        raise HTTPException(status_code=403, detail="Finance access required.")
    if claim.status != Status.APPROVED.value:
        raise BusinessRuleError("Only approved claims can be paid.")
    if has_paid_duplicate(db, claim):
        audit(db, claim.id, finance_user.id, "PAYMENT_BLOCKED")
        db.commit()
        raise BusinessRuleError("Payment blocked: a similar receipt has already been paid.")
    return transition(db, claim, Status.PAID, finance_user)


def return_to_draft(db: Session, user: User, claim: Claim) -> Claim:
    if claim.user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the claim owner can edit it.")
    return transition(db, claim, Status.DRAFT, user)


# --- spend / limits / reports -------------------------------------------

def month_bounds(ref: date | None = None) -> tuple[date, date]:
    first = (ref or date.today()).replace(day=1)
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
    return {
        "pending_approval": sum(1 for c in team_only if c.status == Status.SUBMITTED.value),
        "approved_this_month": sum(1 for c in team_only if c.status == Status.APPROVED.value and c.approved_at and c.approved_at.date() >= first),
        "rejected": sum(1 for c in team_only if c.status == Status.REJECTED.value),
        "team_spend": sum(float(c.amount) for c in team_only if c.status in (Status.APPROVED.value, Status.PAID.value) and first <= c.transaction_date < month_bounds()[1]),
        "team_members": [{"id": u.id, "name": u.name, "limit": float(u.monthly_limit),
                          **limit_status(monthly_spend(db, u.id), float(u.monthly_limit))}
                         for u in db.scalars(select(User).where(User.manager_id == user.id))],
        "claims": [claim_dict(c) for c in claims],
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
    return {
        "monthly_spend": approved_spend,
        "awaiting_payment": awaiting,
        "paid_this_month": paid_spend,
        "over_limit": sum(1 for e in employees if e["status"] == "OVER_LIMIT"),
        "employees": employees,
        "payment_queue": [claim_dict(c) for c in claims if c.status == Status.APPROVED.value],
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
    return {
        "id": c.id, "claim_number": c.claim_number, "user_id": c.user_id,
        "user_name": c.user.name if c.user else None, "merchant": c.merchant,
        "transaction_date": c.transaction_date.isoformat(), "category": c.category,
        "amount": float(c.amount), "currency": c.currency, "description": c.description,
        "status": c.status, "rejection_reason": c.rejection_reason,
        "has_receipt_image": bool(c.receipt and (c.receipt.image_path or c.receipt.s3_image_key)),
        "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None,
        "approved_at": c.approved_at.isoformat() if c.approved_at else None,
        "paid_at": c.paid_at.isoformat() if c.paid_at else None,
    }


def claim_detail(db: Session, user: User, claim: Claim) -> dict:
    require_claim_view(user, claim)
    d = claim_dict(claim)
    d["receipt"] = None
    if claim.receipt:
        d["receipt"] = {
            "source_type": claim.receipt.source_type,
            "image_path": claim.receipt.image_path,
            "s3_image_key": claim.receipt.s3_image_key,
            "original_text": claim.receipt.original_text,
            "original_filename": claim.receipt.original_filename,
            "content_type": claim.receipt.content_type,
            "file_size": claim.receipt.file_size,
            "checksum_sha256": claim.receipt.checksum_sha256,
            "processing_status": claim.receipt.processing_status,
            "extracted_data": claim.receipt.extracted_data,
            "has_pages": bool(claim.receipt.s3_pages_keys),
        }
    d["audit"] = [{
        "action": a.action, "meta": a.meta,
        "user_name": a.user.name if a.user else None,
        "created_at": a.created_at.isoformat(),
    } for a in claim.audits]
    d["duplicate_matches"] = [match_dict(m) for m in find_duplicates(
        db, {"merchant": claim.merchant, "date": claim.transaction_date,
             "amount": float(claim.amount), "description": claim.description}, exclude_id=claim.id)]
    return d
