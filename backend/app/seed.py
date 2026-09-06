"""Seed realistic demo data: users, claims, receipts, duplicate + near-limit scenarios."""
import os
from datetime import date, datetime, timedelta

from sqlalchemy import select

from .db import Base, SessionLocal, engine
from .duplicates import categorize, content_hash, fingerprint
from .models import AuditEvent, Claim, Receipt, Role, Status, User, utcnow

users = [
    ("Riya Patel", "riya@example.com", Role.STAFF.value, 20000),
    ("Neha Shah", "neha@example.com", Role.STAFF.value, 20000),
    ("Vikram Joshi", "vikram@example.com", Role.STAFF.value, 15000),
    ("Arjun Mehta", "arjun@example.com", Role.MANAGER.value, 25000),
    ("Priya Desai", "priya@example.com", Role.FINANCE.value, 50000),
]
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "password123")  # ponytail: demo-only, no real auth in scope


def make_receipt(db, text: str) -> Receipt:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    from .duplicates import parse_amount, parse_date
    merchant = lines[0]
    amount = parse_amount(text)
    d = parse_date(text)
    desc = lines[1] if len(lines) > 1 else None
    return Receipt(
        source_type="TEXT", original_text=text, merchant=merchant, receipt_date=d,
        amount=amount, currency="INR", raw_extracted_text=text,
        normalized_hash=content_hash(text),
        content_hash=fingerprint(merchant, d, amount, "INR", desc),
    )


def make_claim(db, user, number, merchant, txn_date, category, amount, description, status,
               receipt=None, actor=None):
    c = Claim(claim_number=f"CLM-{1000 + number}", user_id=user.id, merchant=merchant,
              transaction_date=txn_date, category=category, amount=amount, currency="INR",
              description=description, status=status.value, receipt=receipt,
              submitted_at=utcnow() if status != Status.DRAFT else None)
    if status in (Status.APPROVED, Status.PAID):
        c.approved_at = utcnow() - timedelta(days=1)
        c.approved_by_id = actor.id if actor else None
    if status == Status.PAID:
        c.paid_at = utcnow() - timedelta(days=1)
    db.add(c)
    db.flush()
    db.add(AuditEvent(claim_id=c.id, user_id=user.id, action="CLAIM_CREATED"))
    if status != Status.DRAFT:
        db.add(AuditEvent(claim_id=c.id, user_id=user.id, action="CLAIM_SUBMITTED"))
    if status in (Status.APPROVED, Status.PAID):
        db.add(AuditEvent(claim_id=c.id, user_id=actor.id if actor else None, action="CLAIM_APPROVED"))
    if status == Status.PAID:
        db.add(AuditEvent(claim_id=c.id, user_id=actor.id if actor else None, action="CLAIM_PAID"))
    return c


def run():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    if db.scalar(select(User).limit(1)):
        print("Seed skipped: data already exists.")
        return

    arjun = User(name="Arjun Mehta", email="arjun@example.com", role=Role.MANAGER.value, monthly_limit=25000)
    priya = User(name="Priya Desai", email="priya@example.com", role=Role.FINANCE.value, monthly_limit=50000)
    db.add(arjun)
    db.flush()  # ponytail: need arjun.id before pointing staff at him
    db.add_all([User(name=n, email=e, role=r, manager_id=arjun.id, monthly_limit=l)
                for n, e, r, l in
                [("Riya Patel", "riya@example.com", Role.STAFF.value, 20000),
                 ("Neha Shah", "neha@example.com", Role.STAFF.value, 20000),
                 ("Vikram Joshi", "vikram@example.com", Role.STAFF.value, 15000)]])
    db.add(priya)
    db.commit()

    by_email = {u.email: u for u in db.scalars(select(User))}
    neha, riya, vikram = by_email["neha@example.com"], by_email["riya@example.com"], by_email["vikram@example.com"]
    today = date.today().replace(day=min(date.today().day, 27))

    # Neha near-limit scenario: 18,700 approved/paid this month, limit 20,000
    make_claim(db, neha, 1, "Marriott Hotel", today, "ACCOMMODATION", 9500, "Client visit stay", Status.PAID, actor=arjun)
    make_claim(db, neha, 2, "Barbeque Nation", today, "MEALS", 5200, "Team dinner", Status.PAID, actor=arjun)
    make_claim(db, neha, 3, "Air India", today, "TRAVEL", 4000, "Flight to Delhi", Status.APPROVED, actor=arjun)

    # Other staff claims
    make_claim(db, riya, 4, "Uber", today, "TAXI", 320, "Trip to Airport", Status.PAID, actor=arjun)
    make_claim(db, riya, 5, "CCD", today, "MEALS", 468, "2 Coffees + sandwich", Status.SUBMITTED)
    make_claim(db, vikram, 6, "Reliance Fresh", today, "SUPPLIES", 1284, "Office stationery", Status.SUBMITTED)
    make_claim(db, vikram, 7, "Ola Cabs", today, "TAXI", 480, "Ahmedabad to Airport", Status.REJECTED)

    # Manager's own claim (self-approval demo)
    make_claim(db, arjun, 8, "Taj Hotel", today, "ACCOMMODATION", 12000, "Conference stay", Status.SUBMITTED)

    # Duplicate scenario: original Uber 320 paid, duplicate wording arrives as draft
    orig_receipt = make_receipt(db, "UBER\n12 Aug 2026\nTrip to Airport\n₹320")
    db.add(orig_receipt)
    db.flush()
    make_claim(db, riya, 9, "Uber", date(today.year, 8, 12), "TAXI", 320, "Trip to Airport",
               Status.PAID, receipt=orig_receipt, actor=arjun)

    dup_receipt = make_receipt(db, "Uber Technologies\n12/08/26\nAirport ride\nINR 320.00")
    db.add(dup_receipt)
    db.flush()
    make_claim(db, vikram, 10, "Uber Technologies", today, "TAXI", 320, "Airport ride", Status.DRAFT,
               receipt=dup_receipt)

    db.commit()
    print("Seeded 5 users, 10 claims. Login: neha@example.com / password123")


if __name__ == "__main__":
    run()
