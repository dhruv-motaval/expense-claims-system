"""Receipt extraction: deterministic text parser (always available) + optional AI vision."""
import base64
import json
import os
import re

import httpx

from .duplicates import categorize, parse_amount, parse_date
from .schemas import ExtractedReceipt

CURRENCY_LINE = re.compile(r"(?:₹|rs\.?|inr)", re.I)


class ExtractionError(Exception):
    pass


EXTRACT_PROMPT = """Extract the following fields from this receipt.

Return JSON only:

{
  "merchant": string | null,
  "date": "YYYY-MM-DD" | null,
  "amount": number | null,
  "currency": string | null,
  "description": string | null,
  "category": "TRAVEL" | "MEALS" | "SUPPLIES" | "TAXI" | "ACCOMMODATION" | "OTHER" | null,
  "raw_text": string | null
}

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
    return ExtractedReceipt(
        merchant=merchant,
        date=d,
        amount=amount,
        currency="INR",
        description=description[:500] if description else None,
        category=categorize(merchant, description, text),
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
                f"{os.getenv('AI_MODEL', 'gemini-3.6-flash')}:generateContent",
                params={"key": key},
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
