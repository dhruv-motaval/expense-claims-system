"""Gate tests — Phase ②: tiered duplicate payment prevention (A/B/C/D) + CAS pay."""
from datetime import date, timedelta

import pytest
from app.db import SessionLocal
from fastapi.testclient import TestClient


@pytest.fixture
def c(seeded):
    return seeded


def hdr(c: TestClient, email: str) -> dict:
    r = c.post("/api/auth/login", json={"email": email})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def make_and_approve(c, riya, arjun, merchant, d, amount, receipt_no=None, receipt_id=None,
                     trans_time=None) -> dict:
    body = {"merchant": merchant, "transaction_date": d, "category": "TAXI", "amount": amount,
            "receipt_id": receipt_id, "receipt_no": receipt_no, "trans_time": trans_time}
    j = c.post("/api/claims", headers=riya, json=body)
    assert j.status_code == 200, j.text
    j = j.json()
    r = c.post(f"/api/claims/{j['id']}/submit", headers=riya)
    assert r.status_code == 200, r.text
    r = c.post(f"/api/claims/{j['id']}/approve", headers=arjun)
    assert r.status_code == 200, r.text
    return j


def queue_entry(c, priya, cid):
    q = c.get("/api/dashboard/finance", headers=priya).json()["payment_queue"]
    return next(e for e in q if e["id"] == cid)


def test_tier_A_same_file_blocked(c):
    import fitz
    riya, arjun, priya = hdr(c, "riya@example.com"), hdr(c, "arjun@example.com"), hdr(c, "priya@example.com")
    doc = fitz.open(); page = doc.new_page(); page.insert_text((72, 72), "CafeA\n01 Oct 2026\nCoffee\nRs 50")
    pdf = doc.tobytes(); doc.close()
    up = c.post("/api/receipts/upload", headers=riya, files={"file": ("same.pdf", pdf, "application/pdf")})
    assert up.status_code == 200, up.text
    rid = up.json()["receipt_id"]

    d = (date.today() - timedelta(days=2)).isoformat()
    c1 = make_and_approve(c, riya, arjun, "CafeA", d, 50.5, receipt_id=rid)
    assert c.post(f"/api/claims/{c1['id']}/pay", headers=priya).status_code == 200

    # second claim, different wording, same canonical file -> tier A hard block
    c2 = make_and_approve(c, riya, arjun, "CafeA Different Wording", d, 50.5, receipt_id=rid)
    r = c.post(f"/api/claims/{c2['id']}/pay", headers=priya)
    assert r.status_code == 422, r.text
    assert "already been paid" in r.json()["detail"]
    detail = c.get(f"/api/claims/{c2['id']}/audit", headers=riya).json()
    t = next(x for x in detail if x["action"] == "PAYMENT_BLOCKED")
    assert t["meta"]["tier"] == "A" and t["meta"]["basis"] == "checksum"


def test_tier_B_same_receipt_number_blocked_despite_rewording(c):
    riya, arjun, priya = hdr(c, "riya@example.com"), hdr(c, "arjun@example.com"), hdr(c, "priya@example.com")
    # seeded paid Uber CLM-1009 carries receipt_no UB984213X in seed
    d = (date.today() - timedelta(days=3)).isoformat()
    c2 = make_and_approve(c, riya, arjun, "UBER RIDES PVT", d, 480, receipt_no="UB-984213 X")
    r = c.post(f"/api/claims/{c2['id']}/pay", headers=priya)
    assert r.status_code == 422, r.text
    detail = c.get(f"/api/claims/{c2['id']}/audit", headers=riya).json()
    t = next(x for x in detail if x["action"] == "PAYMENT_BLOCKED")
    assert t["meta"]["tier"] == "B" and t["meta"]["basis"] == "receipt_no"
    anchors = t["meta"]["compared"]["receipt_no"]
    assert anchors["equal"] is True and anchors["this"] and anchors["other"]


def test_tier_C_gate_requires_justification(c):
    riya, arjun, priya = hdr(c, "riya@example.com"), hdr(c, "arjun@example.com"), hdr(c, "priya@example.com")
    # seed has a PAID claim: merchant 'Uber', today, 320 (CLM-1004)
    j = make_and_approve(c, riya, arjun, "Uber", date.today().isoformat(), 320,
                         receipt_no="ZZ-9999")  # different receipt_no -> not tier B
    qe = queue_entry(c, priya, j["id"])
    assert qe["duplicate_tier"] == "C"
    # pay without justification -> refused, but ticket explains the gate
    r = c.post(f"/api/claims/{j['id']}/pay", headers=priya)
    assert r.status_code == 422 and "justification" in r.json()["detail"]
    # with justification -> paid + DUPLICATE_DISMISSED ticket
    r = c.post(f"/api/claims/{j['id']}/pay", headers=priya,
               json={"justification": "Confirmed with the vendor: genuine second trip same day."})
    assert r.status_code == 200, r.text
    detail = c.get(f"/api/claims/{j['id']}", headers=riya).json()
    assert any(x["action"] == "DUPLICATE_DISMISSED" for x in detail["audit"])


def test_daily_cab_five_legit_claims_all_pay(c):
    """The regression that kills the old fuzzy false positive: same merchant and
    amount every day, different dates -> no block, no justification needed."""
    riya, arjun, priya = hdr(c, "riya@example.com"), hdr(c, "arjun@example.com"), hdr(c, "priya@example.com")
    today = date.today()
    for i in range(5):
        d = (today - timedelta(days=i + 5)).isoformat()
        j = make_and_approve(c, riya, arjun, "DailyCabServices", d, 450, receipt_no=f"DC-20{i:03d}")
        r = c.post(f"/api/claims/{j['id']}/pay", headers=priya)
        assert r.status_code == 200, f"legit daily cab claim {i} must pay: {r.text}"
        assert r.json()["status"] == "PAID"


def test_tier_D_fuzzy_is_only_advisory(c):
    riya, arjun, priya = hdr(c, "riya@example.com"), hdr(c, "arjun@example.com"), hdr(c, "priya@example.com")
    # overlaps merchant+amount with a paid claim but a different day and receipt -> D at most
    d = (date.today() - timedelta(days=9)).isoformat()
    j = make_and_approve(c, riya, arjun, "Uber", d, 320, receipt_no="QW-7777")
    qe = queue_entry(c, priya, j["id"])
    assert qe["duplicate_tier"] in (None, "D"), "fuzzy similarity alone must not raise the tier"
    r = c.post(f"/api/claims/{j['id']}/pay", headers=priya)
    assert r.status_code == 200, r.text


def test_cas_prevents_double_pay_race(c):
    """Direct CAS probe: one session holds a stale APPROVED row while another
    session already paid the claim out from under it -> 409, not a second PAID."""
    from sqlalchemy import select, update
    from app.models import Claim, User, Status
    riya, arjun, priya = hdr(c, "riya@example.com"), hdr(c, "arjun@example.com"), hdr(c, "priya@example.com")
    j = make_and_approve(c, riya, arjun, "RaceCorp", (date.today() - timedelta(days=6)).isoformat(),
                         777, receipt_no="RC-1")

    # stale session S1 (loaded before the rival update)
    s1 = SessionLocal()
    u_priya = s1.scalar(select(User).where(User.email == "priya@example.com"))
    race_claim = s1.get(Claim, j["id"])
    race_claim.status  # touch: S1 believes APPROVED

    # rival session pays it (S2 -> PAID)
    r = c.post(f"/api/claims/{j['id']}/pay", headers=priya)
    assert r.status_code == 200
    first_paid = r.json()["paid_at"]

    # S1 now tries its own pay on the stale APPROVED instance
    import app.services as services
    try:
        services.pay_claim(s1, u_priya, race_claim)
        raise AssertionError("stale pay must not succeed")
    except Exception as e:
        status = getattr(e, "status_code", None) or getattr(getattr(e, "orig", None), "status_code", None)
        assert status in (409, 422), f"expected 409/422, got {status}: {e}"
    finally:
        s1.close()
