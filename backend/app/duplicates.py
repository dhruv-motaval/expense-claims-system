"""Duplicate receipt detection: normalization, fingerprinting, weighted similarity.

Weights per spec: merchant 35, amount 30, date 20, description 15.
Score >= 85 strong duplicate, 65-84 possible, below 65 no match.
"""
import hashlib
import re
from datetime import date, datetime, time
from difflib import SequenceMatcher

from sqlalchemy import select

from .models import Claim, Receipt

CURRENCY_TOKEN = re.compile(r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.I)
NUMBER = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")
NONWORD = re.compile(r"[^a-z0-9\s]")
WS = re.compile(r"\s+")
MERCHANT_NOISE = re.compile(r"\b(technologies|pvt|ltd|limited|inc|cabs|india|the)\b")

# receipt-number normalization: uppercase, strip spaces/punctuation and common
# label prefixes ("NO", "INV", "#"). One helper used on WRITE by extractor, API,
# seed and tests — matching is then pure equality (no tolerance windows).
RECEIPT_NO_PREFIX = re.compile(
    r"^\s*(?:(?:receipt|invoice|tax\s*invoice|bill|trip|ref|offer|token)\s*[-: \t]*\s*)?"
    r"(?:(?:no|num(?:ber)?)\s*\.?\s*|#!)\s*[-:# \t]*",
    re.I)
RECEIPT_NO_JUNK = re.compile(r"[^a-z0-9]+", re.I)


def normalize_receipt_no(v: str | None) -> str | None:
    v = (v or "").strip().upper()
    if not v:
        return None
    stripped = RECEIPT_NO_PREFIX.sub("", v, count=1).strip()
    v = stripped or v
    v = RECEIPT_NO_JUNK.sub("", v)
    return v or None


def normalize_time(v) -> str | None:
    """Minute-truncated canonical time string (fetch two receipts printing the same
    moment and they compare equal): '14:22:05' -> '14:22', '2:30 pm' -> '14:30'.

    Matching is pure equality afterwards — no proximity windows. Returns None when
    the value can't be interpreted, and the caller must treat that as "anchor absent"
    rather than "mismatch"."""
    if v is None:
        return None
    if isinstance(v, time):
        return v.strftime("%H:%M")
    if isinstance(v, datetime):
        return v.strftime("%H:%M")
    s = str(v).strip().lower().replace(".", ":").replace(" ", "")
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::\d{2})?(am|pm)?", s)
    if not m:
        return None
    hh, mm, suffix = int(m[1]), int(m[2]), m[3]
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    if suffix == "pm" and hh < 12:
        hh += 12
    elif suffix == "am" and hh == 12:
        hh = 0
    return f"{hh:02d}:{mm:02d}"

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


# --- tiered identity matching (payment gate) -------------------------------
# Identity keys decide, fuzzy only advises. Tiers per docs/FINAL-DESIGN.md §B7:
#   A: same file bytes (checksum_sha256 vs a PAID receipt)          -> hard block
#   B: same receipt no (normalized (merchant, receipt_no) vs PAID)  -> hard block
#   C: same transaction (merchant+date+amount+time?) vs PAID, receipt numbers
#      not both-present-equal (those would have been tier B)        -> Finance gate
#   D: fuzzy weighted score                                         -> advisory chip
# All comparisons are strict equality after one normalization step. A missing
# anchor is "absent", never "mismatch": it only weakens the tier, never fakes one.

TIER_A = "A"   # same file
TIER_B = "B"   # same receipt number
TIER_C = "C"   # same transaction (no receipt-no collision)
TIER_D = "D"   # fuzzy advisory only


def _anchor_row(c: Claim) -> dict:
    return {
        "merchant": normalize_merchant(c.merchant),
        "date": c.transaction_date,
        "amount": float(c.amount) if c.amount is not None else None,
        "receipt_no": normalize_receipt_no(c.receipt_no),
        "trans_time": normalize_time(c.trans_time),
    }


def anchor_compare(this: Claim, other: Claim) -> dict:
    """Every anchor with raw + normalized values and equality — rendered by both
    the UI comparison card and the payment ticket, so they never lie apart."""
    def cell(raw_this, raw_other, norm_eq):
        return {"this": raw_this, "other": raw_other, "equal": bool(norm_eq)}

    def iseq(a, b):
        return a is not None and b is not None and a == b

    t_this, t_other = normalize_time(this.trans_time), normalize_time(other.trans_time)
    return {
        "receipt_no": cell(this.receipt_no, other.receipt_no, iseq(normalize_receipt_no(this.receipt_no),
                                                                   normalize_receipt_no(other.receipt_no))),
        "time": cell(this.trans_time, other.trans_time, iseq(t_this, t_other)),
        "date": cell(this.transaction_date.isoformat(), other.transaction_date.isoformat(),
                     this.transaction_date == other.transaction_date),
        "amount": cell(float(this.amount), float(other.amount),
                       abs(float(this.amount) - float(other.amount)) < 0.01),
        "merchant": cell(this.merchant, other.merchant,
                         normalize_merchant(this.merchant) == normalize_merchant(other.merchant)),
    }


def identity_match(db, claim: Claim) -> tuple[str | None, Claim | None, dict]:
    """Compare this claim against PAID claims only (money already left).

    Returns (tier, matched_claim, compared_anchors); tier None when nothing matched
    strongly enough to matter at payment. Errors on the inputs (no date etc.) leave
    the tier mechanism working with whatever anchors exist."""
    if claim.status == "PAID":
        return None, None, {}
    own = _anchor_row(claim)

    # Tier A first: same canonical bytes — the one sort of match no rewording can beat.
    checksum = claim.receipt.checksum_sha256 if claim.receipt else None
    if checksum:
        a_id = db.scalar(select(Claim.id).join(Receipt, Claim.receipt_id == Receipt.id).where(
            Claim.status == "PAID", Claim.id != claim.id,
            Receipt.checksum_sha256 == checksum))
        if a_id is not None:
            c = db.get(Claim, a_id)
            return TIER_A, c, anchor_compare(claim, c)

    for c in db.scalars(select(Claim).where(Claim.status == "PAID", Claim.id != claim.id)):
        row = _anchor_row(c)

        # Tier B: same normalized receipt number — the number identifies one
        # printed receipt regardless of how the merchant string was reworded.
        if own["receipt_no"] and row["receipt_no"] == own["receipt_no"]:
            return TIER_B, c, anchor_compare(claim, c)

        # Tier C: same transaction quartet, receipt numbers not both-present-equal.
        core_eq = (row["merchant"] == own["merchant"] and row["date"] == own["date"]
                   and row["amount"] is not None and abs(row["amount"] - own["amount"]) < 0.01)
        if core_eq:
            return TIER_C, c, anchor_compare(claim, c)
    return None, None, {}


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


def duplicate_flags(db, claim: Claim, exclude_id: int | None = None) -> list[dict]:
    """Advisory duplicate chips for any claim context (queue cards) — tier D only:
    never used to block anything, just to render warnings."""
    data = claim_data(claim)
    return [match_dict(m) for m in find_duplicates(db, data, exclude_id=exclude_id)]
