from __future__ import annotations

import re
from copy import deepcopy
from decimal import Decimal

from .models import NormalizedUnit, PurchaseUnit, ReceiptExtraction, ReceiptItem
from .taxonomy import classify_product


def _canonical_name(alias: str) -> str:
    value = re.sub(r"\b(?:[A-Z]{1,3}|\d[A-Z])\d{2,4}\b", "", alias.upper())
    value = re.sub(r"\bP-?\d+X\d+(?:[,.]\d+)?(?:ML|M|G|L)\b", "", value)
    value = re.sub(r"\b\d+X\d+(?:[,.]\d+)?(?:ML|G|L)\b", "", value)
    value = re.sub(r"\b\d+\s*(?:UNID|U)\b", "", value)
    return re.sub(r"\s+", " ", value).strip().title()


def _set_normalized_quantity(item: ReceiptItem) -> None:
    if item.pack_count:
        if item.content_per_unit_value is not None and item.content_per_unit_unit:
            total = item.purchase_quantity * item.pack_count * item.content_per_unit_value
            unit = item.content_per_unit_unit.lower()
            if unit == "g":
                item.normalized_quantity = total / Decimal("1000")
                item.normalized_unit = NormalizedUnit.KG
            elif unit == "ml":
                item.normalized_quantity = total / Decimal("1000")
                item.normalized_unit = NormalizedUnit.L
            elif unit == "l":
                item.normalized_quantity = total
                item.normalized_unit = NormalizedUnit.L
        elif "HUEVO" in item.alias_text.upper():
            item.normalized_quantity = item.purchase_quantity * item.pack_count
            item.normalized_unit = NormalizedUnit.EGG
        else:
            item.normalized_quantity = item.purchase_quantity * item.pack_count
            item.normalized_unit = NormalizedUnit.UNIT
        item.purchase_unit = PurchaseUnit.PACK
    elif item.content_per_unit_value is not None and item.content_per_unit_unit:
        total = item.purchase_quantity * item.content_per_unit_value
        unit = item.content_per_unit_unit.lower()
        if unit == "g":
            item.normalized_quantity = total / Decimal("1000")
            item.normalized_unit = NormalizedUnit.KG
        elif unit == "kg":
            item.normalized_quantity = total
            item.normalized_unit = NormalizedUnit.KG
        elif unit == "ml":
            item.normalized_quantity = total / Decimal("1000")
            item.normalized_unit = NormalizedUnit.L
        elif unit == "l":
            item.normalized_quantity = total
            item.normalized_unit = NormalizedUnit.L
    elif item.purchase_unit == PurchaseUnit.KG:
        item.normalized_quantity = item.purchase_quantity
        item.normalized_unit = NormalizedUnit.KG
    else:
        item.normalized_quantity = item.purchase_quantity
        item.normalized_unit = NormalizedUnit.UNIT


def normalize_receipt(extraction: ReceiptExtraction) -> ReceiptExtraction:
    receipt = extraction.model_copy(deep=True)
    discount_by_code: dict[str, Decimal] = {}
    discount_by_alias: dict[str, Decimal] = {}
    for adjustment in receipt.adjustments:
        if adjustment.type == "line_discount" and adjustment.code:
            discount_by_code[adjustment.code] = discount_by_code.get(adjustment.code, Decimal("0")) + adjustment.amount
        elif adjustment.type == "line_discount" and adjustment.linked_alias:
            alias_key = adjustment.linked_alias.upper().strip()
            discount_by_alias[alias_key] = discount_by_alias.get(alias_key, Decimal("0")) + adjustment.amount

    grouped: dict[tuple, ReceiptItem] = {}
    order: list[tuple] = []
    for source_item in receipt.items:
        item = deepcopy(source_item)
        item.canonical_name = _canonical_name(item.alias_text)
        taxonomy = classify_product(receipt.supermarket, item.alias_text, item.product_code)
        item.product_stable_id = taxonomy.product_stable_id
        item.product_name = taxonomy.product_name
        item.comparable_product_stable_id = taxonomy.comparable_stable_id
        item.comparable_product_name = taxonomy.comparable_name
        item.product_family_stable_id = taxonomy.family_stable_id
        item.product_family_name = taxonomy.family_name
        item.category_stable_id = taxonomy.category_stable_id
        item.category_name = taxonomy.category_name
        key = (
            item.alias_text.upper(),
            item.product_code,
            item.unit_price,
            item.pack_count,
            item.content_per_unit_value,
            item.content_per_unit_unit,
        )
        if key not in grouped:
            grouped[key] = item
            order.append(key)
        else:
            grouped[key].purchase_quantity += item.purchase_quantity
            grouped[key].line_gross += item.line_gross

    receipt.items = [grouped[key] for key in order]
    remaining_discount_by_code = dict(discount_by_code)
    remaining_discount_by_alias = dict(discount_by_alias)
    for item in receipt.items:
        if item.product_code in remaining_discount_by_code:
            item.line_discount += remaining_discount_by_code.pop(item.product_code)
            for adjustment in receipt.adjustments:
                if adjustment.code == item.product_code and adjustment.linked_alias is None:
                    adjustment.linked_alias = item.alias_text
        alias_key = item.alias_text.upper().strip()
        if alias_key in remaining_discount_by_alias:
            item.line_discount += remaining_discount_by_alias.pop(alias_key)
        item.line_final = item.line_gross - item.line_discount
        _set_normalized_quantity(item)

    receipt.immediate_discounts = sum((item.line_discount for item in receipt.items), Decimal("0"))
    return receipt
