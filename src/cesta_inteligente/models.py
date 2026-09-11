from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReceiptStatus(StrEnum):
    VALID = "valid"
    NEEDS_REVIEW = "needs_review"


class PurchaseDateSource(StrEnum):
    OCR = "ocr"
    INFERRED = "inferred"
    USER_CONFIRMED = "user_confirmed"
    UNKNOWN = "unknown"


class PurchaseUnit(StrEnum):
    UNIT = "ud"
    PACK = "pack"
    KG = "kg"
    G = "g"
    L = "L"
    ML = "ml"


class NormalizedUnit(StrEnum):
    UNIT = "unit"
    KG = "kg"
    L = "L"
    EGG = "egg"


class ReceiptImageEvidence(BaseModel):
    ordinal: int = Field(ge=0)
    sha256: str
    storage_id: str
    storage_ref: str
    mime_type: str | None = None


class ReceiptItem(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    original_text: str
    alias_text: str
    product_code: str | None = None
    canonical_name: str | None = None
    product_stable_id: str | None = None
    product_name: str | None = None
    comparable_product_stable_id: str | None = None
    comparable_product_name: str | None = None
    product_family_stable_id: str | None = None
    product_family_name: str | None = None
    category_stable_id: str | None = None
    category_name: str | None = None
    purchase_quantity: Decimal = Decimal("1")
    purchase_unit: PurchaseUnit = PurchaseUnit.UNIT
    pack_count: int | None = None
    content_per_unit_value: Decimal | None = None
    content_per_unit_unit: str | None = None
    normalized_quantity: Decimal | None = None
    normalized_unit: NormalizedUnit | None = None
    unit_price: Decimal | None = None
    line_gross: Decimal
    line_discount: Decimal = Decimal("0")
    line_final: Decimal
    extraction_confidence: Decimal | None = None


class ReceiptAdjustment(BaseModel):
    type: str
    description: str
    original_text: str
    code: str | None = None
    amount: Decimal
    affects_amount_paid: bool
    linked_alias: str | None = None


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    expected: Decimal | int | str | None = None
    actual: Decimal | int | str | None = None
    tolerance: Decimal | None = None
    message: str = ""


class ReceiptExtraction(BaseModel):
    supermarket: str
    store_name: str | None = None
    purchase_date: date | None = None
    purchase_date_source: PurchaseDateSource = PurchaseDateSource.UNKNOWN
    purchase_date_needs_confirmation: bool = False
    article_count: int | None = None
    printed_product_line_count: int | None = None
    subtotal_gross: Decimal
    immediate_discounts: Decimal = Decimal("0")
    coupon_applied: Decimal = Decimal("0")
    amount_paid: Decimal
    reward_generated: Decimal = Decimal("0")
    currency: str = "EUR"
    raw_ocr_text: str
    extraction_confidence: Decimal | None = None
    items: list[ReceiptItem]
    adjustments: list[ReceiptAdjustment] = Field(default_factory=list)


class AuditableReceipt(ReceiptExtraction):
    content_hash: str
    receipt_images: list[ReceiptImageEvidence]
    status: ReceiptStatus
    validations: list[ValidationCheck]
    initial_extraction: dict[str, Any]
    pending_questions: list[str] = Field(default_factory=list)


class IngestResult(BaseModel):
    receipt_id: int
    inserted: bool
    content_hash: str
    status: ReceiptStatus
