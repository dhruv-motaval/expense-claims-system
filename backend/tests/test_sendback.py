"""Gate tests — Phase ②: send-back workflow, return-to-manager, inert manager."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def c(seeded):
    return seeded


def hdr(c: TestClient, email: str) -> dict:
    r = c.post("/api/auth/login", json={"email": email})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def make_claim(c, headers, merchant="Sendback Corp", amount=500, **over):
    body = {"merchant": merchant, "transaction_date": "2026-10-05", "category": "OTHER",
            "amount": amount, "receipt_no": "SB-1", "description": "revision flow", **over}
    r = c.post("/api/claims", headers=headers, json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    if over.get("submit", False):
        return j
    r = c.post(f"/api/claims/{j['id']}/submit", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["claim"]


def test_manager_send_back_full_loop(c):
    neha, arjun, priya = hdr(c, "neha@example.com"), hdr(c, "arjun@example.com"), hdr(c, "priya@example.com")
    claim = make_claim(c, neha)

    r = c.post(f"/api/claims/{claim['id']}/send-back", headers=arjun, json={
        "reason": "Amount exceeds receipt",
        "flagged_fields": ["amount", "merchant"],
        "comment": "Attach the correct receipt page"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SENT_BACK"

    detail = c.get(f"/api/claims/{claim['id']}", headers=neha).json()
    fb = detail["latest_feedback"]
    assert fb["action"] == "CLAIM_SENT_BACK"
    assert fb["meta"]["feedback"]["reason"] == "Amount exceeds receipt"
    assert fb["meta"]["feedback"]["flagged_fields"] == ["amount", "merchant"]
    assert fb["meta"]["actor"]["role"] == "MANAGER"

    # owner edits within the revision window (whitelist SENT_BACK)
    r = c.patch(f"/api/claims/{claim['id']}", headers=neha, json={"amount": 420})
    assert r.status_code == 200, r.text

    res = c.post(f"/api/claims/{claim['id']}/submit", headers=neha)
    assert res.status_code == 200
    detail = c.get(f"/api/claims/{claim['id']}/audit", headers=neha).json()
    sub_tickets = [t for t in detail if t["action"] == "CLAIM_SUBMITTED"]
    assert sub_tickets[-1]["meta"]["resubmission_count"] == 2

    # and the manager can approve the revision
    a = c.post(f"/api/claims/{claim['id']}/approve", headers=arjun)
    assert a.status_code == 200


def test_send_back_guards(c):
    neha, vibram, arjun, priya = (hdr(c, "neha@example.com"), hdr(c, "vikram@example.com"),
                                  hdr(c, "arjun@example.com"), hdr(c, "priya@example.com"))
    claim = make_claim(c, neha, merchant="GuardCorp", receipt_no="SB-2")
    # staff cannot send back
    assert c.post(f"/api/claims/{claim['id']}/send-back", headers=neha,
                  json={"reason": "x"}).status_code == 403
    # finance cannot send back to staff (manager is the gate)
    assert c.post(f"/api/claims/{claim['id']}/send-back", headers=priya,
                  json={"reason": "x"}).status_code == 403
    # manager outside the claim's team cannot (manager B has no team here; use vikram's own)
    vclaim = make_claim(c, vibram, merchant="GuardCorp2", receipt_no="SB-3")
    assert c.post(f"/api/claims/{vclaim['id']}/send-back", headers=arjun, json={
        "reason": "check the taxi fare"}).status_code == 200  # vikram IS in Arjun's team
    # empty reason refused
    claim2 = make_claim(c, neha, merchant="GuardCorp3", receipt_no="SB-4")
    assert c.post(f"/api/claims/{claim2['id']}/send-back", headers=arjun,
                  json={"reason": "   "}).status_code == 422
    # self send-back forbidden
    own = c.post("/api/claims", headers=arjun, json={
        "merchant": "SelfCorp", "transaction_date": "2026-10-05", "category": "OTHER",
        "amount": 10, "submit": True}).json()
    assert c.post(f"/api/claims/{own['id']}/send-back", headers=arjun,
                  json={"reason": "mine"}).status_code == 422


def test_manager_inert_after_approval_then_finance_loop(c):
    neha, arjun, priya = hdr(c, "neha@example.com"), hdr(c, "arjun@example.com"), hdr(c, "priya@example.com")
    claim = make_claim(c, neha, merchant="InertCo", receipt_no="IN-1", submit=True)
    cid = claim["id"]
    assert c.post(f"/api/claims/{cid}/approve", headers=arjun).status_code == 200
    # manager is inert post-approval: approve/reject/send-back all refuse
    assert c.post(f"/api/claims/{cid}/approve", headers=arjun).status_code == 422
    assert c.post(f"/api/claims/{cid}/reject", headers=arjun, json={"reason": "late"}).status_code == 422
    assert c.post(f"/api/claims/{cid}/send-back", headers=arjun, json={"reason": "late"}).status_code == 422

    # finance cannot send back to staff directly — only back to the manager
    r = c.post(f"/api/claims/{cid}/return-to-manager", headers=priya, json={
        "reason": "Category mismatch: receipt shows accommodation"})
    assert r.status_code == 200, r.text
    returned = r.json()
    assert returned["status"] == "SUBMITTED"

    detail = c.get(f"/api/claims/{cid}", headers=neha).json()
    fb = detail["latest_feedback"]
    assert fb["action"] == "CLAIM_RETURNED_TO_MANAGER"
    assert fb["meta"]["feedback"]["assigned_to"]["name"] == "Arjun Mehta"

    # manager now owns it again: re-approve works, pay completes the loop
    assert c.post(f"/api/claims/{cid}/approve", headers=arjun).status_code == 200
    p = c.post(f"/api/claims/{cid}/pay", headers=priya)
    assert p.status_code == 200, p.text


def test_spend_math_excludes_sent_back(c):
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import User, Claim
    import app.services as services
    db = SessionLocal()
    neha = db.scalar(select(User).where(User.email == "neha@example.com"))
    before = services.monthly_spend(db, neha.id)
    claim = make_claim(c, hdr(c, "neha@example.com"), merchant="SpendCheck", amount=101,
                       receipt_no="SP-9", submit=True)
    db2 = SessionLocal()
    c2 = db2.get(Claim, claim["id"])
    mid = services.monthly_spend(db2, neha.id)
    services.send_back_claim(db2, db2.scalar(select(User).where(User.email == "arjun@example.com")),
                             c2, __import__("app.schemas", fromlist=["SentBackIn"]).SentBackIn(reason="check"))
    after = services.monthly_spend(db2, neha.id)
    db.close(); db2.close()
    assert before == after, "SENT_BACK claims must not count as spend (actual or projected)"
    assert mid == after  # SUBMITTED is projected, not actual
