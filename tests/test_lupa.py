from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from cesta_inteligente.batching import assess_receipt_completeness
from cesta_inteligente.pipeline import process_receipt


FIXTURE = Path(__file__).parent / "fixtures" / "lupa" / "ticket.txt"


def test_lupa_semark_split_price_lines_reconcile():
    text = FIXTURE.read_text(encoding="utf-8")
    received_at = datetime(2026, 8, 26, 15, 53, 55, tzinfo=timezone.utc)

    receipt = process_receipt(text, [FIXTURE], received_at=received_at)

    assert receipt.supermarket == "Lupa"
    assert receipt.amount_paid == Decimal("9.07")
    assert receipt.article_count == 5
    assert receipt.printed_product_line_count == 5
    assert sum(item.line_final for item in receipt.items) == Decimal("9.07")
    assert receipt.purchase_date.isoformat() == "2026-08-26"
    assert receipt.purchase_date_source == "inferred"
    assert receipt.purchase_date_needs_confirmation is True


def test_lupa_two_line_total_uses_short_batch_window():
    decision = assess_receipt_completeness(
        FIXTURE.read_text(encoding="utf-8"),
        [FIXTURE],
        received_at=datetime(2026, 8, 26, 15, 53, 55, tzinfo=timezone.utc),
    )

    assert decision.appears_complete is True
    assert decision.closure_mode == "short"
    assert decision.wait_seconds == 10
