import enum
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    STAFF = "STAFF"
    MANAGER = "MANAGER"
    FINANCE = "FINANCE"


class Category(str, enum.Enum):
    TRAVEL = "TRAVEL"
    MEALS = "MEALS"
    SUPPLIES = "SUPPLIES"
    TAXI = "TAXI"
    ACCOMMODATION = "ACCOMMODATION"
    OTHER = "OTHER"


class Status(str, enum.Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAID = "PAID"


class SourceType(str, enum.Enum):
    TEXT = "TEXT"
    IMAGE = "IMAGE"
    SCREENSHOT = "SCREENSHOT"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20))  # Role value
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    monthly_limit: Mapped[float] = mapped_column(Numeric(12, 2), default=20000)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    manager = relationship("User", remote_side="User.id", foreign_keys=[manager_id])
    claims = relationship("Claim", back_populates="user", foreign_keys="Claim.user_id")
    receipts = relationship("Receipt", back_populates="user")


class Receipt(Base):
    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(20), default=SourceType.TEXT.value)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    s3_image_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    s3_source_text_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    s3_extracted_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    merchant: Mapped[str | None] = mapped_column(String(200), nullable=True)
    receipt_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    raw_extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_hash: Mapped[str] = mapped_column(String(64), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user = relationship("User", back_populates="receipts")

class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_number: Mapped[str] = mapped_column(String(20), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    merchant: Mapped[str] = mapped_column(String(200))
    transaction_date: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(20))
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=Status.DRAFT.value, index=True)
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("receipts.id"), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    paid_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="claims", foreign_keys=[user_id])
    receipt = relationship("Receipt")
    audits = relationship("AuditEvent", back_populates="claim", order_by="AuditEvent.id")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    meta: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    claim = relationship("Claim", back_populates="audits")
    user = relationship("User", foreign_keys=[user_id])
