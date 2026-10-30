"""Receipt extraction: deterministic text parser (always available) + optional AI vision."""
import base64
import json
import os
import re

import httpx

from .duplicates import categorize, parse_amount, parse_date
from .schemas import ExtractedReceipt

CURRENCY_LINE = re.compile(r"\b(?:₹|rs\.?|inr)\b", re.I)

RECEIPT_NO_LABELLED = re.compile(
    r"(?:receipt|invoice|bill|trip|tax\s*invoice|offer|token|ref)\s*(?:no\.?|num(?:ber)?|#)\s*[:#]?\s*(\S[^\n]{0,30})", re.I)
# bare trip IDs cab apps print on the receipt: UB984213X, TGX-229003, OLA4482 shorthand
RECEIPT_NO_TOKEN = re.compile(r"\b([A-Z]{2,6}-?[0-9]{4,}(?:[A-Z])?)\b")
# inline time (receipts print next to the date); TIME_ONLY (existing) already gates lines
TIME_INLINE = re.compile(r"\b(\d{1,2}[:.]\d{2}(?::\d{2})?\s*(?:am|pm)?)\b", re.I)

RECEIPT_NO_PREFIX = re.compile(
    r"^\s*(?:(?:receipt|invoice|tax\s*invoice|bill|trip|ref|offer|token)\s*[-: \t]*\s*)?"
    r"(?:(?:no|num(?:ber)?)\s*\.?\s*|#!)\s*[-:# \t]*", re.I)


def _clean_receipt_no(s: str | None) -> str | None:
    from .duplicates import normalize_receipt_no
    raw = (s or "").strip()
    if not raw:
        return None
    raw = RECEIPT_NO_PREFIX.sub("", raw, count=1).strip().rstrip("#").strip()
    # keep a readable form; deep normalization happens on write via normalize_receipt_no
    return normalize_receipt_no(raw) or raw or None


class ExtractionError(Exception):
    pass


EXTRACT_PROMPT = """Extract the following fields from this receipt.

Return JSON only:

{
  "merchant": string | null,
  "date": "YYYY-MM-DD" | null,
  "time": "HH:MM" | null,
  "amount": number | null,
  "currency": string | null,
  "receipt_no": string | null,
  "description": string | null,
  "category": "TRAVEL" | "MEALS" | "SUPPLIES" | "TAXI" | "ACCOMMODATION" | "OTHER" | null,
  "raw_text": string | null
}

receipt_no is any receipt / invoice / bill / trip number printed on the receipt.
time is the transaction time if printed (HH:MM preferred).
Do not invent missing information.
If a field cannot be determined, return null."""


def _is_amount_line(line: str) -> bool:
    return bool(CURRENCY_LINE.search(line)) or bool(re.fullmatch(r"[0-9][0-9,]*(?:\.[0-9]+)?", line.strip()))


TIME_ONLY = re.compile(r"^\d{1,2}[:.]\d{2}(?::\d{2})?\s*(?:am|pm)?$", re.I)


def _is_date_line(line: str) -> bool:
    return parse_date(line) is not None


def _is_time_line(line: str) -> bool:
    return bool(TIME_ONLY.fullmatch(line.strip()))


def _is_header_noise(line: str) -> bool:
    """Date-only, time-only, or pure-amount lines are not merchant names."""
    return _is_amount_line(line) or _is_time_line(line) or parse_date(line) is not None


def extract_from_text(text: str) -> ExtractedReceipt:
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        raise ExtractionError("No receipt text found. Enter the claim manually.")
    # merchant: first line that is not a date/time/amount line; general rule,
    # no receipt-specific values. Fall back to the first line when the receipt
    # is only dates/amounts.
    merchant = next((l for l in lines if not _is_header_noise(l)), lines[0])[:200]
    amount = parse_amount(text)
    d = parse_date(text)
    desc_lines = [l for l in lines[1:] if not _is_amount_line(l) and parse_date(l) is None and not _is_time_line(l)]
    description = max(desc_lines, key=len) if desc_lines else None  # ponytail: longest line wins

    # receipt_no: label beats bare trip-ID token (labels are unambiguous).
    receipt_no = None
    m = RECEIPT_NO_LABELLED.search(text)
    if m:
        receipt_no = _clean_receipt_no(m.group(1))
    if not receipt_no:
        for t in RECEIPT_NO_TOKEN.finditer(text):
            receipt_no = t.group(1)
            break

    # time: prefer a pure time line, else an inline time token
    matched_time = None
    for l in lines:
        if _is_time_line(l):
            matched_time = l.strip()
            break
    if not matched_time:
        mt = TIME_INLINE.search(text)
        if mt:
            matched_time = mt.group(1)
    from .duplicates import normalize_time
    norm_time = normalize_time(matched_time) if matched_time else None

    return ExtractedReceipt(
        merchant=merchant,
        date=d,
        amount=amount,
        currency="INR",
        description=description[:500] if description else None,
        category=categorize(merchant, description, text),
        receipt_no=receipt_no,
        time=norm_time,
        raw_text=text,
    )


async def extract_from_image(content: bytes, content_type: str) -> ExtractedReceipt:
    key = os.getenv("AI_API_KEY")
    if not key:
        raise ExtractionError("AI extraction is not configured. Enter the claim manually.")
    try:
        b64 = base64.b64encode(content).decode()
        # ponytail: native Gemini generateContent — the OpenAI-compat layer hangs on current models
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{os.getenv('AI_MODEL', 'gemini-2.0-flash')}:generateContent",
                # key travels in the header so it never lands in proxies/access logs
                headers={"x-goog-api-key": key},
                json={
                    "contents": [{
                        "parts": [
                            {"text": EXTRACT_PROMPT},
                            {"inline_data": {"mime_type": content_type, "data": b64}},
                        ],
                    }],
                },
            )
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            answer = next((p.get("text") for p in parts if p.get("text")), "")
        answer = re.sub(r"^```(?:json)?|```$", "", answer.strip(), flags=re.M).strip()
        return ExtractedReceipt(**json.loads(answer))
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("Receipt extraction failed. Enter the claim manually.")
