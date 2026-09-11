from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .pipeline import process_receipt


SHORT_WINDOW_SECONDS = 10
LONG_WINDOW_SECONDS = 90

FOOTER_PATTERNS = (
    re.compile(r"(?im)^PAGO\s+(?:TARJETA|EFECTIVO)\b"),
    re.compile(r"(?im)^TOTAL\s+A\s+PAGAR\b"),
    re.compile(r"(?im)^TOTAL\s+VENTAJAS\s+EN\s+ESTA\s+COMPRA\b"),
    re.compile(r"(?im)^TOTAL(?:\s*\([€E]\))?\s+\d+[,.]\d{2}\s*$"),
    re.compile(r"(?im)^TOTAL\s*$\s*^\d+[,.]\d{2}\s*[€E]?\s*$"),
    re.compile(r"(?im)^A\s+PAGAR\s*$"),
)


@dataclass(frozen=True)
class CompletenessDecision:
    appears_complete: bool
    closure_mode: str
    wait_seconds: int
    contains_footer: bool
    reconciles: bool
    reason: str

    def deadline(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.wait_seconds)


def assess_receipt_completeness(
    raw_ocr_text: str,
    source_paths: list[str | Path],
    *,
    received_at: datetime,
    extraction_confidence: Decimal | None = None,
) -> CompletenessDecision:
    """Select a short window only when footer evidence and critical math agree."""
    contains_footer = any(pattern.search(raw_ocr_text) for pattern in FOOTER_PATTERNS)
    if not contains_footer:
        return CompletenessDecision(
            appears_complete=False,
            closure_mode="long",
            wait_seconds=LONG_WINDOW_SECONDS,
            contains_footer=False,
            reconciles=False,
            reason="ticket footer not detected",
        )
    try:
        candidate = process_receipt(
            raw_ocr_text,
            source_paths,
            received_at=received_at,
            extraction_confidence=extraction_confidence,
        )
    except Exception:
        return CompletenessDecision(
            appears_complete=False,
            closure_mode="long",
            wait_seconds=LONG_WINDOW_SECONDS,
            contains_footer=True,
            reconciles=False,
            reason="parser could not reconcile candidate",
        )

    critical_checks = [
        check
        for check in candidate.validations
        if check.name.endswith("_multiplication")
        or check.name
        in {
            "line_gross_less_immediate_discounts_equals_subtotal",
            "line_final_sum_equals_subtotal",
            "subtotal_less_coupon_equals_amount_paid",
            "physical_article_count",
        }
    ]
    reconciles = bool(critical_checks) and all(check.passed for check in critical_checks)
    return CompletenessDecision(
        appears_complete=reconciles,
        closure_mode="short" if reconciles else "long",
        wait_seconds=SHORT_WINDOW_SECONDS if reconciles else LONG_WINDOW_SECONDS,
        contains_footer=True,
        reconciles=reconciles,
        reason="footer and critical math reconcile" if reconciles else "critical math does not reconcile",
    )
