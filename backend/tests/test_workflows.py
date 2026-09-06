"""Full-stack workflow test: clean DB, every spec flow, positive + negative paths."""
from fastapi.testclient import TestClient
import pytest

from app.db import Base, SessionLocal, engine, get_db
from app.main import app
from app import seed


@pytest.fixture(scope="module")
def c():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    def override():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    seed.run()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def tok(c, email):
    r = c.post("/api/auth/login", json={"email": email})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_login_negative(c):
    r = c.post("/api/auth/login", json={"email": "nobody@example.com"})
    assert r.status_code == 401
    r = c.get("/api/claims")
    assert r.status_code == 401  # no token
    r = c.get("/api/claims", headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401  # bad token


def test_idor_staff_cannot_view_foreign_claim(c):
    riya = tok(c, "riya@example.com")
    vikram = tok(c, "vikram@example.com")
    all_riya = c.get("/api/claims", headers=riya).json()
    r = c.get(f"/api/claims/{all_riya[0]['id']}", headers=vikram)
    assert r.status_code == 403


def test_idor_staff_cannot_patch_foreign_claim(c):
    riya = tok(c, "riya@example.com")
    neha = tok(c, "neha@example.com")
    all_riya = c.get("/api/claims", headers=riya).json()
    r = c.patch(f"/api/claims/{all_riya[0]['id']}", headers=neha, json={"amount": 1})
    assert r.status_code == 403


def test_manager_outside_team_blocked_on_detail(c):
    from app.db import SessionLocal
    from app.models import User
    db = SessionLocal()
    if not db.query(User).filter_by(email="mb@example.com").first():
        db.add(User(name="Manager B", email="mb@example.com", role="MANAGER", monthly_limit=10000))
        db.commit()
    db.close()
    mb = tok(c, "mb@example.com")
    riya = tok(c, "riya@example.com")
    all_riya = c.get("/api/claims", headers=riya).json()
    r = c.get(f"/api/claims/{all_riya[0]['id']}", headers=mb)
    assert r.status_code == 403


def test_full_lifecycle_draft_edit_submit_approve_pay(c):
    riya, arjun, priya = tok(c, "riya@example.com"), tok(c, "arjun@example.com"), tok(c, "priya@example.com")
    d = c.post("/api/receipts/extract", headers=riya, data={"text": "Ola Cabs\n22 Sep 2026\nClient visit ride\nRs 214.50"}).json()
    assert d["merchant"] == "Ola Cabs" and d["amount"] == 214.5 and d["category"] == "TAXI"
    claim = c.post("/api/claims", headers=riya, json={
        "merchant": d["merchant"], "transaction_date": d["date"], "category": d["category"],
        "amount": d["amount"], "receipt_id": d["receipt_id"]}).json()
    assert claim["status"] == "DRAFT"
    edited = c.patch(f"/api/claims/{claim['id']}", headers=riya, json={"amount": 250, "merchant": "Ola"}).json()
    assert (edited["amount"], edited["merchant"]) == (250, "Ola")
    s = c.post(f"/api/claims/{claim['id']}/submit", headers=riya).json()
    assert s["claim"]["status"] == "SUBMITTED"
    a = c.post(f"/api/claims/{claim['id']}/approve", headers=arjun).json()
    assert a["status"] == "APPROVED"
    # approve again -> invalid transition
    assert c.post(f"/api/claims/{claim['id']}/approve", headers=arjun).status_code == 422
    p = c.post(f"/api/claims/{claim['id']}/pay", headers=priya).json()
    assert p["status"] == "PAID"
    # paid is terminal: every path rejects
    assert c.post(f"/api/claims/{claim['id']}/approve", headers=arjun).status_code in (403, 422)
    assert c.post(f"/api/claims/{claim['id']}/reject", headers=arjun, json={"reason": "x"}).status_code in (403, 422)
    assert c.patch(f"/api/claims/{claim['id']}", headers=riya, json={"amount": 1}).status_code == 422
    assert c.post(f"/api/claims/{claim['id']}/pay", headers=priya).status_code == 422
    detail = c.get(f"/api/claims/{claim['id']}", headers=riya).json()
    actions = [x["action"] for x in detail["audit"]]
    assert actions == ["CLAIM_CREATED", "CLAIM_UPDATED", "CLAIM_SUBMITTED", "CLAIM_APPROVED", "CLAIM_PAID"]


def test_rejection_loop_back_to_draft(c):
    neha, arjun = tok(c, "neha@example.com"), tok(c, "arjun@example.com")
    claim = c.post("/api/claims", headers=neha, json={
        "merchant": "Indian Oil", "transaction_date": "2026-09-22", "category": "TRAVEL",
        "amount": 999, "submit": True}).json()
    r = c.post(f"/api/claims/{claim['id']}/reject", headers=arjun, json={"reason": "personal expense"})
    assert r.json()["status"] == "REJECTED"
    assert r.json()["rejection_reason"] == "personal expense"
    # approve a rejected claim -> invalid
    assert c.post(f"/api/claims/{claim['id']}/approve", headers=arjun).status_code == 422
    # owner returns to draft, edits, resubmits
    c.post(f"/api/claims/{claim['id']}/return-to-draft", headers=neha)
    c.patch(f"/api/claims/{claim['id']}", headers=neha, json={"description": "Fuel for client visit"})
    s = c.post(f"/api/claims/{claim['id']}/submit", headers=neha)
    assert s.json()["claim"]["status"] == "SUBMITTED"


def test_duplicate_warning_on_submit_and_payment_block(c):
    neha, arjun, priya = tok(c, "neha@example.com"), tok(c, "arjun@example.com"), tok(c, "priya@example.com")
    # reworded duplicate of the seeded PAID Uber 320 (Aug 12)
    d = c.post("/api/receipts/extract", headers=neha,
               data={"text": "Uber Technologies Pvt Ltd\n12/08/26\nAirport ride\nINR 320.00"}).json()
    assert any(m["level"] == "strong" for m in d["duplicate_matches"]), d["duplicate_matches"]
    claim = c.post("/api/claims", headers=neha, json={
        "merchant": "Uber Technologies", "transaction_date": "2026-08-12",
        "category": "TAXI", "amount": 320, "description": "Airport ride",
        "receipt_id": d["receipt_id"], "submit": True}).json()
    c.post(f"/api/claims/{claim['id']}/approve", headers=arjun)
    r = c.post(f"/api/claims/{claim['id']}/pay", headers=priya)
    assert r.status_code == 422 and "already been paid" in r.json()["detail"]
    audit = c.get(f"/api/claims/{claim['id']}", headers=neha).json()["audit"]
    assert "PAYMENT_BLOCKED" in [x["action"] for x in audit]
    assert "DUPLICATE_WARNING" in [x["action"] for x in audit]


def test_validation_errors(c):
    riya = tok(c, "riya@example.com")
    bad = [
        {"merchant": "", "transaction_date": "2026-09-01", "category": "TAXI", "amount": 100},  # empty merchant
        {"merchant": "X", "transaction_date": "2026-09-01", "category": "TAXI", "amount": -5},  # negative amount
        {"merchant": "X", "transaction_date": "2026-09-01", "category": "TAXI", "amount": 0},   # zero amount
        {"merchant": "X", "transaction_date": "not-a-date", "category": "TAXI", "amount": 5},   # bad date
        {"merchant": "X", "transaction_date": "2026-09-01", "category": "GAMBLING", "amount": 5},  # bad category
    ]
    for body in bad:
        assert c.post("/api/claims", headers=riya, json=body).status_code == 422, body
    # reject requires reason
    arjun = tok(c, "arjun@example.com")
    neha = tok(c, "neha@example.com")
    claim = c.post("/api/claims", headers=neha, json={
        "merchant": "X", "transaction_date": "2026-09-01", "category": "OTHER", "amount": 5, "submit": True}).json()
    r = c.post(f"/api/claims/{claim['id']}/reject", headers=arjun, json={"reason": ""})
    assert r.status_code == 422


def test_role_matrix(c):
    riya, arjun, priya = tok(c, "riya@example.com"), tok(c, "arjun@example.com"), tok(c, "priya@example.com")
    # finance cannot approve
    all_r = c.get("/api/claims", headers=riya).json()
    assert c.post(f"/api/claims/{all_r[0]['id']}/approve", headers=priya).status_code == 403
    # staff cannot reject
    assert c.post(f"/api/claims/{all_r[0]['id']}/reject", headers=riya, json={"reason": "x"}).status_code == 403
    # staff/manager blocked from finance dashboards + reports
    assert c.get("/api/dashboard/finance", headers=riya).status_code == 403
    assert c.get("/api/dashboard/finance", headers=arjun).status_code == 403
    assert c.get("/api/reports/monthly", headers=arjun).status_code == 403
    assert c.get("/api/reports/categories", headers=riya).status_code == 403
    assert c.get("/api/reports/employees", headers=arjun).status_code == 403
    # finance sees all; staff sees only self
    all_names = {x["user_name"] for x in c.get("/api/claims", headers=priya).json()}
    assert all_names == {"Riya Patel", "Neha Shah", "Vikram Joshi", "Arjun Mehta"}
    only_riya = {x["user_name"] for x in c.get("/api/claims", headers=riya).json()}
    assert only_riya == {"Riya Patel"}


def test_limits_and_reports(c):
    priya = tok(c, "priya@example.com")
    f = c.get("/api/dashboard/finance", headers=priya).json()
    neha = next(e for e in f["employees"] if e["name"] == "Neha Shah")
    assert neha["status"] == "NEAR_LIMIT" and neha["spend"] == 18700.0
    rep = c.get("/api/reports/monthly", headers=priya).json()
    assert rep["total_claims"] > 0 and isinstance(rep["by_status"], dict)
    assert any(cat["total"] > 0 for cat in rep["categories"])


def test_manager_dashboard_numbers(c):
    arjun = tok(c, "arjun@example.com")
    m = c.get("/api/dashboard/manager", headers=arjun).json()
    assert {t["name"] for t in m["team_members"]} == {"Riya Patel", "Neha Shah", "Vikram Joshi"}
    assert m["pending_approval"] >= 1
