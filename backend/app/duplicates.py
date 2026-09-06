"""Duplicate receipt detection: normalization, fingerprinting, weighted similarity.

Weights per spec: merchant 35, amount 30, date 20, description 15.
Score >= 85 strong duplicate, 65-84 possible, below 65 no match.
"""
import hashlib
import re
from datetime import date, datetime
from difflib import SequenceMatcher

from sqlalchemy import select

from .models import Claim

CURRENCY_TOKEN = re.compile(r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.I)
NUMBER = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")
NONWORD = re.compile(r"[^a-z0-9\s]")
WS = re.compile(r"\s+")
MERCHANT_NOISE = re.compile(r"\b(technologies|pvt|ltd|limited|inc|cabs|india|the)\b")

# ponytail: dd/mm assumed for numeric dates (Indian receipts); flip if mm/dd shows up
DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[ /.-]([A-Za-z]{3,9})\.?,?[ /.-](\d{2,4})\b"), "dmy_name"),
    (re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b"), "mdy_name"),
    (re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b"), "dmy_num"),
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "iso"),
]

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTHS.update({m.lower(): i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)})

CATEGORY_KEYWORDS = [
    ("TAXI", ["uber", "ola", "cab", "taxi", "rickshaw", "auto rickshaw"]),
    ("MEALS", ["restaurant", "cafe", "coffee", "lunch", "dinner", "breakfast", "sandwich", "ccd", "starbucks"]),
    ("ACCOMMODATION", ["hotel", "stay", "accommodation", "lodge", "inn"]),
    ("TRAVEL", ["flight", "airline", "air india", "indigo", "airport", "travel", "train", "railway", "irctc", "makemytrip"]),
    ("SUPPLIES", ["stationery", "office supplies", "supplies", "reliance fresh", "supermarket", "grocery", "petrol", "fuel"]),
]


def normalize_text(v: str | None) -> str:
    v = (v or "").lower()
    v = CURRENCY_TOKEN.sub(r" \1 ", v)
    v = NONWORD.sub(" ", v)
    return WS.sub(" ", v).strip()


def normalize_merchant(v: str | None) -> str:
    return MERCHANT_NOISE.sub("", normalize_text(v)).strip()


def parse_amount(text: str) -> float | None:
    # currency-marked figures win; otherwise bottom-up scan of raw lines (totals sit at the end)
    amounts = [float(m.replace(",", "")) for m in CURRENCY_TOKEN.findall(text or "")]
    if amounts:
        return amounts[-1]
    for line in reversed((text or "").splitlines()):
        if parse_date(line):
            continue
        m = NUMBER.search(line)
        if m:
            return float(m.group(0).replace(",", ""))
    return None  # ponytail: multi-item receipts without a grand total get the last line-item


def _fix_year(y: int) -> int:
    return y + 2000 if y < 100 else y


def parse_date(text: str) -> date | None:
    for pat, kind in DATE_PATTERNS:
        m = pat.search(text or "")
        if not m:
            continue
        try:
            if kind == "dmy_name":
                return date(_fix_year(int(m[3])), _MONTHS[m[2][:3].lower()], int(m[1]))
            if kind == "mdy_name":
                return date(int(m[3]), _MONTHS[m[1][:3].lower()], int(m[2]))
            if kind == "dmy_num":
                return date(_fix_year(int(m[3])), int(m[2]), int(m[1]))
            if kind == "iso":
                return date(int(m[1]), int(m[2]), int(m[3]))
        except (KeyError, ValueError):
            continue
    return None


def categorize(merchant: str | None, description: str | None, raw_text: str | None) -> str:
    blob = " ".join(filter(None, [merchant, description])).lower() or (raw_text or "").lower()
    for cat, words in CATEGORY_KEYWORDS:
        if any(w in blob for w in words):
            return cat
    return "OTHER"


def fingerprint(merchant, d: date | None, amount, currency, description) -> str:
    parts = [
        normalize_merchant(merchant),
        d.isoformat() if d else "",
        f"{float(amount):.2f}" if amount is not None else "",
        (currency or "").lower(),
        normalize_text(description),
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def content_hash(text: str | None) -> str:
    return hashlib.sha1(normalize_text(text).encode()).hexdigest() if text else ""


def claim_data(claim: Claim) -> dict:
    return {
        "merchant": claim.merchant,
        "date": claim.transaction_date,
        "amount": float(claim.amount),
        "description": claim.description,
    }


def similarity(a: dict, b: dict) -> int:
    score = 0.0
    ma, mb = normalize_merchant(a.get("merchant") or ""), normalize_merchant(b.get("merchant") or "")
    if ma and mb:
        score += 35 * SequenceMatcher(None, ma, mb).ratio()
    aa, ab = a.get("amount"), b.get("amount")
    if aa is not None and ab is not None and abs(float(aa) - float(ab)) < 0.01:
        score += 30
    da, db = a.get("date"), b.get("date")
    if da and db and da == db:
        score += 20
    ta, tb = normalize_text(a.get("description") or ""), normalize_text(b.get("description") or "")
    if ta and tb:
        score += 15 * SequenceMatcher(None, ta, tb).ratio()
    return round(score)


def find_duplicates(db, data: dict, exclude_id: int | None = None) -> list[dict]:
    """ponytail: O(n) scan over active claims; bucket by fingerprint first if volume ever matters."""
    stmt = select(Claim).where(Claim.status.in_(["SUBMITTED", "APPROVED", "PAID"]))
    if exclude_id:
        stmt = stmt.where(Claim.id != exclude_id)
    out = []
    for c in db.scalars(stmt):
        s = similarity(data, claim_data(c))
        if s >= 65:
            out.append({"claim": c, "score": s, "level": "strong" if s >= 85 else "possible"})
    out.sort(key=lambda m: -m["score"])
    return out


def has_paid_duplicate(db, claim: Claim) -> bool:
    data = claim_data(claim)
    stmt = select(Claim).where(Claim.status == "PAID", Claim.id != claim.id)
    return any(similarity(data, claim_data(c)) >= 65 for c in db.scalars(stmt))


def match_dict(match: dict) -> dict:
    c: Claim = match["claim"]
    return {
        "claim_id": c.id,
        "claim_number": c.claim_number,
        "user_name": c.user.name,
        "merchant": c.merchant,
        "amount": float(c.amount),
        "date": c.transaction_date.isoformat(),
        "score": match["score"],
        "level": match["level"],
    }
