from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Response
from sqlalchemy.orm import Session

from . import services
from . import db as db_config
from .db import STORAGE_DIR, get_db
from .deps import current_user, require_role
from .models import Claim, Role, User, Receipt
from .schemas import ClaimCreate, ClaimUpdate, LoginIn

router = APIRouter()


@router.post("/auth/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    user, token = services.login(db, data.email)
    return {"token": token, "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role}}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role,
            "monthly_limit": float(user.monthly_limit)}


@router.post("/receipts/extract")
def extract_receipt(text: str = Form(...), db: Session = Depends(get_db), user: User = Depends(current_user)):
    return services.extract(db, user, text)


@router.post("/receipts/upload")
async def upload_receipt(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(current_user)):
    return await services.extract_upload(db, user, file)


@router.get("/receipts/image/{receipt_id}")
def receipt_image(receipt_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    import os
    import mimetypes
    from fastapi.responses import FileResponse

    receipt = db.get(Receipt, receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="No receipt image.")
    if not services.can_view_receipt(user, receipt):
        raise HTTPException(status_code=403, detail="You do not have access to this receipt.")

    if db_config.STORAGE_BACKEND == "s3":
        if not receipt.s3_image_key:
            raise HTTPException(status_code=404, detail="No receipt image.")
        try:
            content = services.get_receipt_content(db, user, receipt)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=404, detail="No receipt image.")
        ct = mimetypes.guess_type(receipt.s3_image_key or "")[0] or "application/octet-stream"
        return Response(content=content, media_type=ct)
    else:
        key = receipt.image_path or receipt.s3_image_key
        if not key:
            raise HTTPException(status_code=404, detail="No receipt image.")
        path = os.path.join(STORAGE_DIR, key)
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="No receipt image.")
        return FileResponse(path)


claim_router = APIRouter(prefix="/claims")


def _get_claim(db: Session, claim_id: int) -> Claim:
    claim = db.get(Claim, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")
    return claim


@claim_router.post("")
def create_claim(data: ClaimCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    claim = services.create_claim(db, user, data)
    return services.claim_dict(claim)


@claim_router.get("")
def list_claims(db: Session = Depends(get_db), user: User = Depends(current_user)):
    from sqlalchemy import select
    claims = db.scalars(select(Claim).order_by(Claim.id.desc()).limit(200)).all()
    return [services.claim_dict(c) for c in claims if services.can_view(user, c)]

@claim_router.get("/{claim_id}")
def get_claim(claim_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return services.claim_detail(db, user, _get_claim(db, claim_id))


@claim_router.patch("/{claim_id}")
def patch_claim(claim_id: int, data: ClaimUpdate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    claim = services.update_claim(db, user, _get_claim(db, claim_id), data)
    return services.claim_dict(claim)


@claim_router.post("/{claim_id}/submit")
def submit(claim_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    result = services.submit_claim(db, user, _get_claim(db, claim_id))
    return {"claim": services.claim_dict(result["claim"]), "duplicate_matches": result["duplicate_matches"]}


@claim_router.post("/{claim_id}/approve")
def approve(claim_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return services.claim_dict(services.approve_claim(db, user, _get_claim(db, claim_id)))


@claim_router.post("/{claim_id}/reject")
def reject(claim_id: int, data: dict = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    reason = (data or {}).get("reason", "")
    return services.claim_dict(services.reject_claim(db, user, _get_claim(db, claim_id), reason))


@claim_router.post("/{claim_id}/return-to-draft")
def back_to_draft(claim_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return services.claim_dict(services.return_to_draft(db, user, _get_claim(db, claim_id)))


@claim_router.post("/{claim_id}/pay")
def pay(claim_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return services.claim_dict(services.pay_claim(db, user, _get_claim(db, claim_id)))


@router.get("/dashboard/staff")
def staff_dash(db: Session = Depends(get_db), user: User = Depends(require_role(Role.STAFF, Role.MANAGER))):
    return services.staff_dashboard(db, user)


@router.get("/dashboard/manager")
def manager_dash(db: Session = Depends(get_db), user: User = Depends(require_role(Role.MANAGER))):
    return services.manager_dashboard(db, user)


@router.get("/dashboard/finance")
def finance_dash(db: Session = Depends(get_db), user: User = Depends(require_role(Role.FINANCE))):
    return services.finance_dashboard(db, user)


@router.get("/reports/monthly")
def monthly_report(db: Session = Depends(get_db), user: User = Depends(require_role(Role.FINANCE))):
    return services.reports(db)


@router.get("/reports/categories")
def category_report(db: Session = Depends(get_db), user: User = Depends(require_role(Role.FINANCE))):
    return {"categories": services.spend_by_category(db)}


@router.get("/reports/employees")
def employee_report(db: Session = Depends(get_db), user: User = Depends(require_role(Role.FINANCE))):
    return {"employees": services.finance_dashboard(db, user)["employees"]}
