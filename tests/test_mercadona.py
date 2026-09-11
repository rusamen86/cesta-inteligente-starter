from decimal import Decimal

from cesta_inteligente.models import ReceiptStatus


def test_mercadona_fixture_is_valid(mercadona_case):
    receipt, expected = mercadona_case

    assert receipt.status == ReceiptStatus.VALID
    assert receipt.supermarket == expected["supermarket"]
    assert receipt.purchase_date.isoformat() == expected["purchase_date"]
    assert receipt.purchase_date_source == expected["purchase_date_source"]
    assert receipt.purchase_date_needs_confirmation is False
    assert receipt.article_count == expected["article_count"]
    assert receipt.printed_product_line_count == expected["printed_product_line_count"]
    assert receipt.subtotal_gross == Decimal(expected["subtotal_gross"])
    assert receipt.amount_paid == Decimal(expected["amount_paid"])
    assert all(check.passed for check in receipt.validations)


def test_mercadona_quantity_is_not_pack_count(mercadona_case):
    receipt, _ = mercadona_case
    by_alias = {item.alias_text: item for item in receipt.items}

    almonds = by_alias["BEBIDA ALMENDRAS 0%"]
    assert almonds.purchase_quantity == Decimal("3")
    assert almonds.pack_count is None
    assert almonds.unit_price == Decimal("1.10")
    assert almonds.line_gross == Decimal("3.30")

    water = by_alias["AGUADOY GAS P-6X500M"]
    assert water.purchase_quantity == Decimal("1")
    assert water.pack_count == 6
    assert water.normalized_quantity == Decimal("3")
    assert water.normalized_unit == "L"


def test_mercadona_total_reconciles(mercadona_case):
    receipt, _ = mercadona_case
    assert sum(item.line_final for item in receipt.items) == Decimal("14.11")
