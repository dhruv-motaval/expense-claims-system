"""Services: auth, claims workflow, state machine, reporting, audit."""
import hashlib
import hmac
import os
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import STORAGE_DIR
from .duplicates import (content_hash, fingerprint, find_duplicates, has_paid_duplicate,
                         match_dict, normalize_merchant, parse_date, categorize, parse_amount)
from .extractors import ExtractionError, extract_from_text, extract_from_image
from .models import AuditEvent, Category, Claim, Receipt, Role, SourceType, Status, User, utcnow
from .schemas import ClaimCreate, ClaimUpdate, ExtractedReceipt

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


def login(db: Session, email: str) -> User:
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


# --- extraction ----------------------------------------------------------

def extract(db: Session, user: User, text: str) -> dict:
    try:
        r: ExtractedReceipt = extract_from_text(text)
    except ExtractionError as e:
        raise BusinessRuleError(str(e))
    receipt = _save_receipt(db, SourceType.TEXT.value, original_text=text, extracted=r)
    return {"receipt_id": receipt.id,
            "duplicate_matches": _duplicates(db, r), **r.model_dump(mode="json")}


async def extract_upload(db: Session, user: User, file: UploadFile) -> dict:
    if file.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise BusinessRuleError("Upload a jpg, png or webp receipt image.")
    content = await file.read()
    try:
        r = await extract_from_image(content, file.content_type)
    except ExtractionError as e:
        raise BusinessRuleError(str(e))
    path = _save_image(content, file.filename or "receipt.jpg")
    receipt = _save_receipt(db, SourceType.IMAGE.value, image_path=path, extracted=r)
    return {"receipt_id": receipt.id,
            "duplicate_matches": _duplicates(db, r), **r.model_dump(mode="json")}


def _duplicates(db: Session, r: ExtractedReceipt) -> list[dict]:
    return [match_dict(m) for m in find_duplicates(
        db, {"merchant": r.merchant, "date": r.date,
             "amount": r.amount, "description": r.description})]


def _save_receipt(db: Session, source_type: str, extracted: ExtractedReceipt,
                  original_text: str | None = None, image_path: str | None = None) -> Receipt:
    r = Receipt(
        source_type=source_type,
        original_text=original_text,
        image_path=image_path,
        merchant=extracted.merchant,
        receipt_date=extracted.date,
        amount=extracted.amount,
        currency=extracted.currency,
        raw_extracted_text=extracted.raw_text,
        normalized_hash=content_hash(extracted.raw_text),
        content_hash=fingerprint(extracted.merchant, extracted.date, extracted.amount,
                                 extracted.currency, extracted.description),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _save_image(content: bytes, filename: str) -> str:
    receipts_dir = os.path.join(STORAGE_DIR, "receipts")
    os.makedirs(receipts_dir, exist_ok=True)
    path = os.path.join(receipts_dir, f"{int(datetime.now().timestamp() * 1000)}-{os.path.basename(filename)}")
    with open(path, "wb") as f:
        f.write(content)
    return os.path.relpath(path, STORAGE_DIR)


# --- claims --------------------------------------------------------------

def create_claim(db: Session, user: User, data: ClaimCreate) -> Claim:
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
    this_month = [c for c in claims if first <= c.transaction_date < nxt]
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
        "has_receipt_image": bool(c.receipt and c.receipt.image_path),
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
            "original_text": claim.receipt.original_text,
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
