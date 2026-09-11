from __future__ import annotations

from decimal import Decimal

from .models import PurchaseDateSource, ReceiptExtraction, ReceiptStatus, ValidationCheck


TOLERANCE = Decimal("0.01")


def _close(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= TOLERANCE


def validate_receipt(receipt: ReceiptExtraction) -> tuple[list[ValidationCheck], ReceiptStatus]:
    checks: list[ValidationCheck] = []

    checks.append(
        ValidationCheck(
            name="purchase_date_present",
            passed=receipt.purchase_date is not None,
            expected="purchase_date",
            actual=receipt.purchase_date.isoformat() if receipt.purchase_date else None,
            message="A purchase date is required before a receipt is fully valid.",
        )
    )
    confirmed_date = receipt.purchase_date_source in (PurchaseDateSource.OCR, PurchaseDateSource.USER_CONFIRMED)
    checks.append(
        ValidationCheck(
            name="purchase_date_confirmed",
            passed=confirmed_date,
            expected="ocr_or_user_confirmed",
            actual=receipt.purchase_date_source,
            message="An inferred date requires confirmation before automatic finalization.",
        )
    )

    for index, item in enumerate(receipt.items, start=1):
        if item.unit_price is None:
            continue
        expected = item.purchase_quantity * item.unit_price
        checks.append(
            ValidationCheck(
                name=f"item_{index}_multiplication",
                passed=_close(expected, item.line_gross),
                expected=expected,
                actual=item.line_gross,
                tolerance=TOLERANCE,
                message=item.alias_text,
            )
        )

    gross_sum = sum((item.line_gross for item in receipt.items), Decimal("0"))
    final_sum = sum((item.line_final for item in receipt.items), Decimal("0"))
    checks.append(
        ValidationCheck(
            name="line_gross_less_immediate_discounts_equals_subtotal",
            passed=_close(gross_sum - receipt.immediate_discounts, receipt.subtotal_gross),
            expected=receipt.subtotal_gross,
            actual=gross_sum - receipt.immediate_discounts,
            tolerance=TOLERANCE,
        )
    )
    checks.append(
        ValidationCheck(
            name="line_final_sum_equals_subtotal",
            passed=_close(final_sum, receipt.subtotal_gross),
            expected=receipt.subtotal_gross,
            actual=final_sum,
            tolerance=TOLERANCE,
        )
    )
    calculated_paid = receipt.subtotal_gross - receipt.coupon_applied
    checks.append(
        ValidationCheck(
            name="subtotal_less_coupon_equals_amount_paid",
            passed=_close(calculated_paid, receipt.amount_paid),
            expected=receipt.amount_paid,
            actual=calculated_paid,
            tolerance=TOLERANCE,
        )
    )
    if receipt.article_count is not None:
        calculated_articles = int(sum(item.purchase_quantity for item in receipt.items))
        checks.append(
            ValidationCheck(
                name="physical_article_count",
                passed=calculated_articles == receipt.article_count,
                expected=receipt.article_count,
                actual=calculated_articles,
                message="Counts purchase quantities; pack_count and printed lines are excluded.",
            )
        )

    status = ReceiptStatus.VALID if all(check.passed for check in checks) else ReceiptStatus.NEEDS_REVIEW
    return checks, status
