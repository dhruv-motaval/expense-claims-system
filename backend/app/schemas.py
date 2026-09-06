from datetime import date as Date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

CATEGORIES = Literal["TRAVEL", "MEALS", "SUPPLIES", "TAXI", "ACCOMMODATION", "OTHER"]
STATUSES = Literal["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "PAID"]


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
    raw_text: Optional[str] = None


class ClaimCreate(BaseModel):
    merchant: str = Field(min_length=1)
    transaction_date: Date
    category: CATEGORIES
    amount: Decimal = Field(gt=0)
    currency: str = "INR"
    description: Optional[str] = None
    receipt_id: Optional[int] = None
    submit: bool = False


class ClaimUpdate(BaseModel):
    merchant: Optional[str] = None
    transaction_date: Optional[Date] = None
    category: Optional[CATEGORIES] = None
    amount: Optional[Decimal] = Field(default=None, gt=0)
    currency: Optional[str] = None
    description: Optional[str] = None


class RejectIn(BaseModel):
    reason: str = ""
