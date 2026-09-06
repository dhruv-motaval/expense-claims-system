"""Business-rule tests covering spec section 46."""
from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.services import limit_status, monthly_spend
from tests.conftest import login


def _login(client, email):
    return login(client, email)


# Test 1 - staff creates claim
def test_staff_creates_claim(seeded):
    headers = _login(seeded, "riya@example.com")
    r = seeded.post("/api/claims", headers=headers, json={
        "merchant": "Uber", "transaction_date": str(date.today()),
        "category": "TAXI", "amount": 320,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "DRAFT"


# Test 2 - receipt extraction
def test_receipt_extraction(seeded):
    headers = _login(seeded, "riya@example.com")
    r = seeded.post("/api/receipts/extract", headers=headers,
                    data={"text": "UBER\n12 Aug 2026\nTrip to Airport\n₹320"})
    assert r.status_code == 200
    d = r.json()
    assert d["merchant"] == "UBER" and d["amount"] == 320
    assert d["category"] == "TAXI" and d["date"] == f"{date.today().year}-08-12"


def test_extraction_messy_receipts(seeded):
    headers = _login(seeded, "riya@example.com")
    for text, merchant, amount, cat in [
        ("CCD\n18/08/26\n2 Coffees + sandwich\nRs 468.00\nThank you!", "CCD", 468.0, "MEALS"),
        ("Reliance Fresh\n22 Aug 2026\nOffice stationery\n₹1,284", "Reliance Fresh", 1284.0, "SUPPLIES"),
        ("Air India\n25 Aug 2026\nAhmedabad -> Delhi\n₹6,850", "Air India", 6850.0, "TRAVEL"),
    ]:
        d = seeded.post("/api/receipts/extract", headers=headers, data={"text": text}).json()
        assert (d["merchant"], d["amount"], d["category"]) == (merchant, amount, cat), d


# Test 3 - user can edit extraction
def test_edit_extraction(seeded):
    headers = _login(seeded, "riya@example.com")
    r = seeded.post("/api/receipts/extract", headers=headers,
                    data={"text": "UBER\n12 Aug 2026\nTrip to Airport\n₹320"})
    rid = r.json()["receipt_id"]
    claim = seeded.post("/api/claims", headers=headers, json={
        "merchant": "Uber", "transaction_date": str(date.today()), "category": "TAXI",
        "amount": 320, "receipt_id": rid}).json()
    r = seeded.patch(f"/api/claims/{claim['id']}", headers=headers, json={"amount": 340})
    assert r.json()["amount"] == 340
    r = seeded.post(f"/api/claims/{claim['id']}/submit", headers=headers)
    assert r.status_code == 200
    assert seeded.get(f"/api/claims/{claim['id']}", headers=headers).json()["amount"] == 340


# Test 4 - manager self-approval blocked
def test_self_approval_blocked(seeded):
    headers = _login(seeded, "arjun@example.com")
    own = [c for c in seeded.get("/api/claims", headers=headers).json()
           if c["status"] == "SUBMITTED" and c["user_name"] == "Arjun Mehta"][0]
    r = seeded.post(f"/api/claims/{own['id']}/approve", headers=headers)
    assert r.status_code == 422
    assert "own" in r.json()["detail"].lower()


# Test 5 - wrong manager blocked
def test_wrong_manager_blocked(seeded, client):
    db = SessionLocal()
    db.add(User(name="Manager B", email="managerb@example.com", role="MANAGER", monthly_limit=10000))
    db.commit()
    db.close()

    headers_b = _login(client, "managerb@example.com")
    riya_h = _login(client, "riya@example.com")
    mine = [c for c in client.get("/api/claims", headers=riya_h).json() if c["user_name"] == "Riya Patel"]
    cid = mine[0]["id"] if mine else client.post("/api/claims", headers=riya_h, json={
        "merchant": "Ola", "transaction_date": str(date.today()), "category": "TAXI",
        "amount": 250}).json()["id"]
    r = client.post(f"/api/claims/{cid}/approve", headers=headers_b)
    assert r.status_code == 422
    assert "team" in r.json()["detail"].lower()


# Test 6 - paid claim terminal
def test_paid_is_terminal(seeded):
    headers = _login(seeded, "priya@example.com")
    paid = [c for c in seeded.get("/api/claims", headers=headers).json() if c["status"] == "PAID"][0]
    assert seeded.post(f"/api/claims/{paid['id']}/pay", headers=headers).status_code == 422
    assert seeded.post(f"/api/claims/{paid['id']}/approve", headers=headers).status_code in (403, 422)
    assert seeded.patch(f"/api/claims/{paid['id']}", headers=headers, json={"amount": 1}).status_code == 422


# Test 7 - duplicate receipt detected despite rewording
def test_duplicate_detection(seeded):
    headers = _login(seeded, "riya@example.com")
    cid = seeded.post("/api/claims", headers=headers, json={
        "merchant": "Uber Technologies", "transaction_date": str(date.today()),
        "category": "TAXI", "amount": 320, "description": "Airport ride", "submit": True}).json()["id"]
    detail = seeded.get(f"/api/claims/{cid}", headers=headers).json()
    assert any(m["level"] == "strong" for m in detail["duplicate_matches"])


# Test 8 - double payment blocked
def test_double_payment_blocked(seeded):
    riya_h = _login(seeded, "riya@example.com")
    arjun_h = _login(seeded, "arjun@example.com")
    priya_h = _login(seeded, "priya@example.com")
    cid = seeded.post("/api/claims", headers=riya_h, json={
        "merchant": "Uber", "transaction_date": str(date.today()),
        "category": "TAXI", "amount": 320, "description": "Trip to Airport", "submit": True}).json()["id"]
    seeded.post(f"/api/claims/{cid}/approve", headers=arjun_h)
    r = seeded.post(f"/api/claims/{cid}/pay", headers=priya_h)
    assert r.status_code == 422
    assert "already been paid" in r.json()["detail"]


# Tests 9/10 - monthly limits
def test_near_and_over_limit(seeded):
    db = SessionLocal()
    neha = db.scalar(select(User).where(User.email == "neha@example.com"))
    near = limit_status(monthly_spend(db, neha.id), float(neha.monthly_limit))
    db.close()
    assert near["status"] == "NEAR_LIMIT" and near["spend"] == 18700

    neha_h = _login(seeded, "neha@example.com")
    arjun_h = _login(seeded, "arjun@example.com")
    cid = seeded.post("/api/claims", headers=neha_h, json={
        "merchant": "Uber", "transaction_date": str(date.today()), "category": "TAXI",
        "amount": 1450, "submit": True}).json()["id"]
    seeded.post(f"/api/claims/{cid}/approve", headers=arjun_h)
    db = SessionLocal()
    over = limit_status(monthly_spend(db, neha.id), float(neha.monthly_limit))
    db.close()
    assert over["status"] == "OVER_LIMIT" and over["spend"] == 20150


# Bonus: offline strategy - image upload without AI key fails gracefully
def test_upload_without_ai_key_fails_gracefully(seeded):
    headers = _login(seeded, "riya@example.com")
    r = seeded.post("/api/receipts/upload", headers=headers,
                    files={"file": ("receipt.png", b"fakeimagebytes", "image/png")})
    assert r.status_code == 422
    assert "manually" in r.json()["detail"].lower()
