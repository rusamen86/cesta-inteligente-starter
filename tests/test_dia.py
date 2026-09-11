from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cesta_inteligente.batching import assess_receipt_completeness
from cesta_inteligente.ocr_layout import normalize_vision_receipt_text
from cesta_inteligente.pipeline import process_receipt


FIXTURE = Path(__file__).parent / "fixtures" / "dia" / "ticket.txt"


def test_dia_vision_ocr_is_not_misrouted_to_carrefour_and_reconciles():
    raw = FIXTURE.read_text(encoding="utf-8")
    normalized = normalize_vision_receipt_text(raw)
    receipt = process_receipt(normalized, [FIXTURE])

    assert normalized.startswith("GRUPO DIA")
    assert "CENTROS COMERCIALES CARREFOUR" not in normalized
    assert receipt.supermarket == "DIA"
    assert receipt.purchase_date.isoformat() == "2026-09-04"
    assert receipt.purchase_date_source == "ocr"
    assert receipt.printed_product_line_count == 16
    assert receipt.article_count is None
    assert receipt.subtotal_gross == Decimal("25.81")
    assert receipt.amount_paid == Decimal("25.81")
    assert receipt.immediate_discounts == Decimal("0.22")
    assert receipt.status == "valid"
    assert all(check.passed for check in receipt.validations)

    by_alias = {item.alias_text: item for item in receipt.items}
    assert by_alias["TOMATE TRITURADO"].purchase_quantity == Decimal("2")
    assert by_alias["TOMATE TRITURADO"].line_gross == Decimal("1.20")
    assert by_alias["TOMATE TRITURADO"].line_discount == Decimal("0.22")
    assert by_alias["CERV. ESTRELLA G. 1906"].purchase_quantity == Decimal("6")
    assert by_alias["CERV. ESTRELLA G. 1906"].unit_price == Decimal("1.19")
    assert by_alias["CERV. ESTRELLA G. 1906"].line_gross == Decimal("7.14")
    assert all(item.category_name != "Sin clasificar" for item in receipt.items)


def test_complete_dia_ticket_uses_short_batch_window():
    raw = FIXTURE.read_text(encoding="utf-8")
    decision = assess_receipt_completeness(
        normalize_vision_receipt_text(raw),
        [FIXTURE],
        received_at=datetime(2026, 9, 4, 21, 21, 10, tzinfo=timezone.utc),
    )

    assert decision.appears_complete is True
    assert decision.closure_mode == "short"
    assert decision.wait_seconds == 10
