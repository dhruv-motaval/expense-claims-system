"""Gate tests — Phase ①: edit whitelist, JSON tickets, typed reject, file-access log."""
import io

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal


@pytest.fixture
def c(seeded):
    return seeded


def hdr(c: TestClient, email: str) -> dict:
    r = c.post("/api/auth/login", json={"email": email})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def make_claim(c, headers, **over):
    body = {"merchant": "Integrity Co", "transaction_date": "2026-10-01",
            "category": "OTHER", "amount": 100, "receipt_no": "inv- 22/33x", **over}
    r = c.post("/api/claims", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def audit_actions(c, headers, claim_id):
    # audit metadata is now served by the lazy full-audit endpoint
    return c.get(f"/api/claims/{claim_id}/audit", headers=headers).json()


def test_normalize_on_write(c):
    riya = hdr(c, "riya@example.com")
    claim = make_claim(c, riya, receipt_no=" Receipt-No: UB-984213 x ")
    assert claim["receipt_no"] == "UB984213X"
    claim2 = make_claim(c, riya, trans_time="2:30 pm")
    assert claim2["trans_time"] == "14:30"


def test_owner_can_edit_draft_and_ticket_has_diff(c):
    riya = hdr(c, "riya@example.com")
    claim = make_claim(c, riya, submit=False)
    r = c.patch(f"/api/claims/{claim['id']}", headers=riya,
                json={"amount": 150, "receipt_no": "inv-22/33y"})
    assert r.status_code == 200
    tickets = [t for t in audit_actions(c, riya, claim["id"]) if t["action"] == "CLAIM_UPDATED"]
    assert tickets, "CLAIM_UPDATED ticket missing"
    diff = tickets[-1]["meta"]["fields"]
    assert diff["amount"] == {"old": 100, "new": 150}
    assert diff["receipt_no"]["old"] == "INV2233X" and diff["receipt_no"]["new"] == "INV2233Y"


def test_submitted_claim_is_frozen_for_owner(c):
    riya = hdr(c, "riya@example.com")
    claim = make_claim(c, riya, submit=True)
    r = c.patch(f"/api/claims/{claim['id']}", headers=riya, json={"amount": 999})
    assert r.status_code == 422
    tickets = [t for t in audit_actions(c, riya, claim["id"]) if t["action"] == "CLAIM_EDIT_BLOCKED"]
    assert tickets, "edit-block on SUBMITTED must be ticketed"
    assert tickets[-1]["meta"]["attempted_fields"] == ["amount"]
    assert tickets[-1]["meta"]["status"] == "SUBMITTED"


def test_approved_claim_is_frozen(c):
    riya, arjun = hdr(c, "riya@example.com"), hdr(c, "arjun@example.com")
    claim = make_claim(c, riya, submit=True)
    r = c.post(f"/api/claims/{claim['id']}/approve", headers=arjun)
    assert r.status_code == 200
    r = c.patch(f"/api/claims/{claim['id']}", headers=riya, json={"amount": 999})
    assert r.status_code == 422
    tickets = [t for t in audit_actions(c, riya, claim["id"]) if t["action"] == "CLAIM_EDIT_BLOCKED"]
    assert tickets and tickets[-1]["meta"]["status"] == "APPROVED"


def test_reject_requires_reason_typed(c):
    neha, arjun = hdr(c, "neha@example.com"), hdr(c, "arjun@example.com")
    claim = make_claim(c, neha, merchant="RejectMe", submit=True)
    assert c.post(f"/api/claims/{claim['id']}/reject", headers=arjun,
                  json={"reason": "  "}).status_code == 422
    r = c.post(f"/api/claims/{claim['id']}/reject", headers=arjun, json={"reason": "wrong category"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"


def test_receipt_file_access_ticketed(c):
    import fitz
    riya = hdr(c, "riya@example.com")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Anchor Cafe\n12 Oct 2026\nfilter coffee\nRs 60")
    data = doc.tobytes()
    doc.close()
    up = c.post("/api/receipts/upload", headers=riya,
                files={"file": ("ticket.pdf", data, "application/pdf")})
    assert up.status_code == 200, up.text
    receipt_id = up.json()["receipt_id"]
    claim = c.post("/api/claims", headers=riya,
                   json={"merchant": "Anchor Cafe", "transaction_date": "2026-10-12",
                         "category": "MEALS", "amount": 60, "receipt_id": receipt_id,
                         "submit": False}).json()
    # the staff owner AND the manager both open the file -> two tickets
    c.get(f"/api/receipts/image/{receipt_id}", headers=riya)
    c.get(f"/api/receipts/image/{receipt_id}", headers=hdr(c, "arjun@example.com"))
    tickets = [t for t in audit_actions(c, riya, claim["id"])
               if t["action"] == "RECEIPT_FILE_ACCESSED"]
    assert len(tickets) >= 2, "file access must be ticketed per viewer"
    assert tickets[-1]["meta"]["purpose"] == "verification"
    assert tickets[-1]["meta"]["tier"] == "HOT"


def test_extraction_pulls_receipt_no_and_time(c):
    riya = hdr(c, "riya@example.com")
    d = c.post("/api/receipts/extract", headers=riya, data={
        "text": "UBER\n12 Aug 2026 14:22\nTrip to Airport\nRs 320\nReceipt No: UB984213X"}).json()
    assert d["receipt_no"] == "UB984213X"
    assert d["time"] == "14:22"
