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


def extract_from_text(text: str) -> ExtractedReceipt:
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        raise ExtractionError("No receipt text found. Enter the claim manually.")
    merchant = lines[0][:200]
    amount = parse_amount(text)
    d = parse_date(text)
    desc_lines = [l for l in lines[1:] if not _is_amount_line(l) and parse_date(l) is None]
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
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{os.getenv('AI_BASE_URL', 'https://api.groq.com/openai/v1')}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": os.getenv("AI_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": EXTRACT_PROMPT},
                            {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{b64}"}},
                        ],
                    }],
                },
            )
            r.raise_for_status()
            answer = r.json()["choices"][0]["message"]["content"]
        answer = re.sub(r"^```(?:json)?|```$", "", answer.strip(), flags=re.M).strip()
        return ExtractedReceipt(**json.loads(answer))
    except ExtractionError:
        raise
    except Exception:
        raise ExtractionError("Receipt extraction failed. Enter the claim manually.")
