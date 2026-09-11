from decimal import Decimal

from cesta_inteligente.models import ReceiptStatus
from cesta_inteligente.normalize import normalize_receipt
from cesta_inteligente.ocr_layout import normalize_vision_receipt_text
from cesta_inteligente.parser import parse_receipt_text
from cesta_inteligente.validate import validate_receipt


def test_carrefour_math_is_valid_but_inferred_date_requires_confirmation(carrefour_case):
    receipt, expected = carrefour_case

    assert receipt.status == expected["expected_status_before_confirmation"]
    assert receipt.purchase_date.isoformat() == "2026-08-20"
    assert receipt.purchase_date_source == expected["purchase_date_source"]
    assert receipt.purchase_date_needs_confirmation is True
    assert receipt.pending_questions == [
        "No aparece la fecha en el ticket. He inferido 2026-08-20 por la recepción; ¿la confirmas?"
    ]
    assert receipt.article_count == expected["article_count"]
    assert receipt.printed_product_line_count == expected["printed_product_line_count"]
    assert len(receipt.items) == expected["normalized_item_count"]
    assert sum(item.line_gross for item in receipt.items) == Decimal(expected["gross_items_sum"])
    assert receipt.immediate_discounts == Decimal(expected["immediate_discounts"])
    assert receipt.subtotal_gross == Decimal(expected["subtotal_gross"])
    assert receipt.coupon_applied == Decimal(expected["coupon_applied"])
    assert receipt.amount_paid == Decimal(expected["amount_paid"])
    failed = [check.name for check in receipt.validations if not check.passed]
    assert failed == ["purchase_date_confirmed"]


def test_carrefour_user_confirmed_date_is_fully_valid(carrefour_confirmed_case):
    receipt, _ = carrefour_confirmed_case

    assert receipt.status == ReceiptStatus.VALID
    assert receipt.purchase_date.isoformat() == "2026-08-20"
    assert receipt.purchase_date_source == "user_confirmed"
    assert receipt.purchase_date_needs_confirmation is False
    assert receipt.pending_questions == []
    assert all(check.passed for check in receipt.validations)


def test_carrefour_explanatory_lines_and_discounts(carrefour_case):
    receipt, _ = carrefour_case
    by_alias = {item.alias_text: item for item in receipt.items}

    tortilla = by_alias["TORTILLA AVENA"]
    assert tortilla.purchase_quantity == Decimal("2")
    assert tortilla.unit_price == Decimal("1.81")
    assert tortilla.line_gross == Decimal("3.62")

    gnocchi = by_alias["GNOCCHI DE PATATA AL58"]
    assert gnocchi.purchase_quantity == Decimal("2")
    assert gnocchi.line_discount == Decimal("0.45")
    assert gnocchi.line_final == Decimal("1.33")

    mochi = by_alias["MOCHI CARREFOUR 6U D422"]
    assert mochi.purchase_quantity == Decimal("2")
    assert mochi.pack_count == 6
    assert mochi.normalized_quantity == Decimal("12")
    assert mochi.line_gross == Decimal("5.70")
    assert mochi.line_discount == Decimal("1.42")
    assert mochi.line_final == Decimal("4.28")

    linked = {adjustment.code: adjustment.linked_alias for adjustment in receipt.adjustments if adjustment.code}
    assert linked == {"AL58": "GNOCCHI DE PATATA AL58", "D422": "MOCHI CARREFOUR 6U D422"}


def test_carrefour_pack_normalization(carrefour_case):
    receipt, _ = carrefour_case
    by_alias = {item.alias_text: item for item in receipt.items}

    cheese = by_alias["QUESO CRF 4X62,5G"]
    assert cheese.purchase_quantity == Decimal("1")
    assert cheese.pack_count == 4
    assert cheese.normalized_quantity == Decimal("0.2500")
    assert cheese.normalized_unit == "kg"

    sliced = by_alias["P.P L.FR/SAL 2X200G"]
    assert sliced.pack_count == 2
    assert sliced.normalized_quantity == Decimal("0.4")

    eggs = by_alias["HUEVO M CRF 24 UNID"]
    assert eggs.purchase_quantity == Decimal("1")
    assert eggs.pack_count == 24
    assert eggs.normalized_quantity == Decimal("24")
    assert eggs.normalized_unit == "egg"


def test_carrefour_simple_container_sizes_and_non_consumable_capacity(carrefour_case):
    receipt, _ = carrefour_case
    by_alias = {item.alias_text: item for item in receipt.items}

    assert by_alias["QUESO FETA BIO 180G"].normalized_quantity == Decimal("0.18")
    assert by_alias["QUESO FETA BIO 180G"].normalized_unit == "kg"
    assert by_alias["HOGAZA CEREALES 400G"].normalized_quantity == Decimal("0.4")
    assert by_alias["POSTRE VEGETAL 400G"].normalized_quantity == Decimal("0.4")

    bags = by_alias["405 BAS 30L COCINA"]
    assert bags.content_per_unit_value is None
    assert bags.normalized_quantity == Decimal("1")
    assert bags.normalized_unit == "unit"


def test_carrefour_summary_lines_are_not_new_rewards(carrefour_case):
    receipt, _ = carrefour_case
    loyalty = [a for a in receipt.adjustments if a.type == "loyalty_balance"]
    summaries = [a for a in receipt.adjustments if a.type == "summary_savings"]

    assert receipt.reward_generated == Decimal("0")
    assert [(a.original_text, a.amount, a.affects_amount_paid) for a in loyalty] == [
        ("ACUMULADO CLUB 8,76", Decimal("8.76"), False)
    ]
    assert {(a.original_text, a.amount) for a in summaries} == {
        ("DESCUENTOS 4,87", Decimal("4.87")),
        ("TOTAL VENTAJAS EN ESTA COMPRA 13,63", Decimal("13.63")),
    }


def test_carrefour_totals_and_article_controls(carrefour_case):
    receipt, _ = carrefour_case
    assert sum(item.line_final for item in receipt.items) == Decimal("150.09")
    assert receipt.subtotal_gross - receipt.coupon_applied == Decimal("147.09")
    assert sum(item.purchase_quantity for item in receipt.items) == Decimal("48")
    assert receipt.article_count == 48
    assert receipt.article_count != len(receipt.items)


def test_carrefour_pdf_layout_preserves_codes_quantities_date_and_all_coupons():
    raw = """***Centros Comerciales Carrefour S.A***
Ciudad Centro
**************************************
AGUA SAN JOAQUIN 5 L
3 x (
0,75 )
2,25
CHAMPU FRUCTIS
2E12
3,79
1 3x2 EN EL MÁS BA
2E12
-3,79
QUESO MAMA VACA
DV70
2 x (
2,29 )
4,58
DESCUENTO EN 2ª UNIDAD
DV70
-1,15
SUBTOTAL
5,68
DTO. CUPON GRATIS ORAL B
-2,75
DTO. CUPON 1 EUR APETINA
-1,00
6 ART. TOTAL A PAGAR : 1,93
ACUMULADO CLUB: 4,00
DESCUENTOS: 4,94
TOTAL VENTAJAS EN ESTA COMPRA: 8,94
27-08-2026 21:50
Saldo acumulado a 27/08/2026:82,60 €
"""

    normalized_text = normalize_vision_receipt_text(raw)
    receipt = normalize_receipt(parse_receipt_text(normalized_text))
    validations, status = validate_receipt(receipt)

    assert receipt.purchase_date.isoformat() == "2026-08-27"
    assert receipt.purchase_date_source == "ocr"
    assert receipt.article_count == 6
    assert sum(item.purchase_quantity for item in receipt.items) == Decimal("6")
    assert receipt.immediate_discounts == Decimal("4.94")
    assert receipt.coupon_applied == Decimal("3.75")
    assert receipt.amount_paid == Decimal("1.93")
    assert status == ReceiptStatus.VALID
    assert all(check.passed for check in validations)
    assert {adjustment.code for adjustment in receipt.adjustments if adjustment.type == "line_discount"} == {
        "2E12",
        "DV70",
    }
    assert [
        adjustment.amount for adjustment in receipt.adjustments if adjustment.type == "receipt_coupon"
    ] == [Decimal("2.75"), Decimal("1.00")]
    assert any(
        adjustment.type == "loyalty_balance" and adjustment.amount == Decimal("82.60")
        for adjustment in receipt.adjustments
    )


def test_carrefour_discount_code_is_applied_once_when_multiple_aliases_share_it():
    text = """CENTROS COMERCIALES CARREFOUR S.A.
FECHA 27/08/2026
3 ART.
CHAMPU A 2E12 3,00
CHAMPU B 2E12 3,00
CHAMPU B 2E12 3,00
1 3x2 EN EL MÁS BA 2E12 -3,00
SUBTOTAL 6,00
TOTAL A PAGAR 6,00
"""

    receipt = normalize_receipt(parse_receipt_text(text))
    validations, status = validate_receipt(receipt)

    assert receipt.immediate_discounts == Decimal("3.00")
    assert sum(item.line_discount for item in receipt.items) == Decimal("3.00")
    assert status == ReceiptStatus.VALID
    assert all(check.passed for check in validations)
