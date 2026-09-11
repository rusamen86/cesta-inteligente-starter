from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cesta_inteligente.batching import assess_receipt_completeness
from cesta_inteligente.pipeline import process_receipt


FIXTURE = Path(__file__).parent / "fixtures" / "aldi" / "ticket.txt"


def test_aldi_vision_ocr_reconciles_products_and_total():
    text = FIXTURE.read_text(encoding="utf-8")

    receipt = process_receipt(text, [FIXTURE])

    assert receipt.supermarket == "Aldi"
    assert receipt.purchase_date.isoformat() == "2026-09-03"
    assert receipt.purchase_date_source == "ocr"
    assert receipt.amount_paid == Decimal("13.70")
    assert receipt.printed_product_line_count == 6
    assert receipt.article_count is None
    assert receipt.status == "valid"
    assert [item.line_final for item in receipt.items] == [
        Decimal("1.89"),
        Decimal("2.21"),
        Decimal("3.40"),
        Decimal("1.99"),
        Decimal("1.63"),
        Decimal("2.58"),
    ]
    assert receipt.items[1].purchase_quantity == Decimal("1.590")
    assert receipt.items[1].purchase_unit == "kg"
    assert receipt.items[1].normalized_quantity == Decimal("1.590")
    assert receipt.items[2].purchase_quantity == Decimal("4")
    assert receipt.items[4].purchase_quantity == Decimal("0.710")
    assert receipt.items[5].purchase_quantity == Decimal("2")
    assert all(item.category_name != "Sin clasificar" for item in receipt.items)


def test_aldi_complete_ticket_uses_short_batch_window():
    decision = assess_receipt_completeness(
        FIXTURE.read_text(encoding="utf-8"),
        [FIXTURE],
        received_at=datetime(2026, 9, 3, 22, 18, 52, tzinfo=timezone.utc),
    )

    assert decision.appears_complete is True
    assert decision.closure_mode == "short"
    assert decision.wait_seconds == 10
