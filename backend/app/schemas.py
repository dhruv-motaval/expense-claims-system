from datetime import date as Date, timedelta
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

CATEGORIES = Literal["TRAVEL", "MEALS", "SUPPLIES", "TAXI", "ACCOMMODATION", "OTHER"]
STATUSES = Literal["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "PAID", "SENT_BACK"]
CURRENCY_LITERAL = Literal["INR"]

CLAIM_FIELD_NAMES = {"merchant", "transaction_date", "trans_time", "receipt_no",
                     "category", "amount", "currency", "description"}


def validate_txn_date(v: Date) -> Date:
    if not v:
        return v
    today = Date.today()
    if not (today - timedelta(days=365) <= v <= today + timedelta(days=30)):
        raise ValueError("Transaction date must be within the last year and no more than 30 days ahead.")
    return v


class LoginIn(BaseModel):
    email: str
    password: str = ""


class ExtractedIn(BaseModel):
    text: str


class ExtractedReceipt(BaseModel):
    merchant: Optional[str] = None
    date: Optional[Date] = None
    amount: Optional[float] = None
    currency: str = "INR"
    description: Optional[str] = None
    category: Optional[CATEGORIES] = None
    receipt_no: Optional[str] = None   # raw as extracted; normalized on write
    time: Optional[str] = None         # raw "HH:MM" / "HH:MM:SS"; truncated on write
    raw_text: Optional[str] = None


class ClaimCreate(BaseModel):
    merchant: str = Field(min_length=1, max_length=200)
    transaction_date: Date
    category: CATEGORIES
    amount: Decimal = Field(gt=0, le=10000000)
    currency: CURRENCY_LITERAL = "INR"
    description: Optional[str] = None
    receipt_id: Optional[int] = None
    receipt_no: Optional[str] = Field(default=None, max_length=120)
    trans_time: Optional[str] = Field(default=None, max_length=8)
    submit: bool = False

    _check_date = field_validator("transaction_date")(validate_txn_date)


class ClaimUpdate(BaseModel):
    merchant: Optional[str] = Field(default=None, min_length=1, max_length=200)
    transaction_date: Optional[Date] = None
    category: Optional[CATEGORIES] = None
    amount: Optional[Decimal] = Field(default=None, gt=0, le=10000000)
    currency: Optional[CURRENCY_LITERAL] = None
    description: Optional[str] = None
    receipt_no: Optional[str] = Field(default=None, max_length=120)
    trans_time: Optional[str] = Field(default=None, max_length=8)

    _check_date = field_validator("transaction_date")(validate_txn_date)


class RejectIn(BaseModel):
    """Sent to /reject: a refusal to pay must say why (spec rule)."""
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Rejection reason must not be blank.")
        return v.strip()


CLAIM_FIELD_NAMES = Literal[
    "merchant", "transaction_date", "trans_time", "receipt_no",
    "category", "amount", "currency", "description"]


class _FeedbackIn(BaseModel):
    """Shared shape for reviewer feedback tickets."""
    reason: str = Field(min_length=1)
    flagged_fields: Optional[list[CLAIM_FIELD_NAMES]] = None
    comment: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("reason")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("A reason must not be blank.")
        return v.strip()


class SentBackIn(_FeedbackIn):
    """Manager sends a SUBMITTED claim back to staff for revision."""


class ReturnToManagerIn(_FeedbackIn):
    """Finance returns an APPROVED claim to its manager for re-review."""


class PayIn(BaseModel):
    """Optional body when paying: required only to clear a tier-C duplicate gate."""
    justification: Optional[str] = Field(default=None, min_length=20, max_length=2000)
