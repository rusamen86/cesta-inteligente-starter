from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .hashing import build_image_evidence, stable_receipt_hash
from .models import AuditableReceipt, PurchaseDateSource
from .normalize import normalize_receipt
from .parser import parse_receipt_text
from .validate import validate_receipt


def process_receipt(
    raw_ocr_text: str,
    source_paths: list[str | Path],
    *,
    received_at: datetime | None = None,
    confirmed_purchase_date: date | None = None,
    extraction_confidence: Decimal | None = None,
    household_timezone: str = "Europe/Madrid",
) -> AuditableReceipt:
    images = build_image_evidence(source_paths)
    parsed = parse_receipt_text(raw_ocr_text, confidence=extraction_confidence)
    initial = parsed.model_dump(mode="json")
    if parsed.purchase_date is None and confirmed_purchase_date is not None:
        parsed.purchase_date = confirmed_purchase_date
        parsed.purchase_date_source = PurchaseDateSource.USER_CONFIRMED
        parsed.purchase_date_needs_confirmation = False
    elif parsed.purchase_date is None and received_at is not None:
        local_received_at = received_at
        if received_at.tzinfo is not None:
            local_received_at = received_at.astimezone(ZoneInfo(household_timezone))
        parsed.purchase_date = local_received_at.date()
        parsed.purchase_date_source = PurchaseDateSource.INFERRED
        parsed.purchase_date_needs_confirmation = True
    elif parsed.purchase_date is None:
        parsed.purchase_date_source = PurchaseDateSource.UNKNOWN
        parsed.purchase_date_needs_confirmation = True
    normalized = normalize_receipt(parsed)
    validations, status = validate_receipt(normalized)
    pending_questions: list[str] = []
    if normalized.purchase_date_source == PurchaseDateSource.INFERRED:
        pending_questions.append(
            f"No aparece la fecha en el ticket. He inferido {normalized.purchase_date.isoformat()} por la recepción; ¿la confirmas?"
        )
    elif normalized.purchase_date is None:
        pending_questions.append("No aparece la fecha del ticket. ¿Qué fecha de compra corresponde?")
    return AuditableReceipt(
        **normalized.model_dump(),
        content_hash=stable_receipt_hash(images),
        receipt_images=images,
        status=status,
        validations=validations,
        initial_extraction=initial,
        pending_questions=pending_questions,
    )
