from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from cesta_inteligente.pipeline import process_receipt


FIXTURES = Path(__file__).parent / "fixtures"


def load_case(name: str, *, inferred_date: bool = False, confirmed_date: date | None = None):
    folder = FIXTURES / name
    text_path = folder / "ticket.txt"
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    receipt = process_receipt(
        text_path.read_text(encoding="utf-8"),
        [text_path],
        received_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc) if inferred_date else None,
        confirmed_purchase_date=confirmed_date,
        extraction_confidence=Decimal("1"),
    )
    return receipt, expected


@pytest.fixture
def mercadona_case():
    return load_case("mercadona")


@pytest.fixture
def carrefour_case():
    return load_case("carrefour", inferred_date=True)


@pytest.fixture
def carrefour_confirmed_case():
    return load_case("carrefour", confirmed_date=date(2026, 8, 20))
