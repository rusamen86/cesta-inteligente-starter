from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from decimal import Decimal

from .models import PurchaseDateSource, PurchaseUnit, ReceiptAdjustment, ReceiptExtraction, ReceiptItem


MONEY_RE = r"-?\d+[,.]\d{2}"


def money(value: str) -> Decimal:
    return Decimal(value.replace(".", "").replace(",", "."))


def _fold(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.upper())
        if not unicodedata.combining(char)
    )


def _date_from_text(text: str):
    match = re.search(r"\b(\d{2})[/-](\d{2})[/-](\d{2}|\d{4})\b", text)
    if not match:
        return None
    year = match.group(3)
    if len(year) == 2:
        year = f"20{year}"
    return datetime.strptime(f"{match.group(1)}/{match.group(2)}/{year}", "%d/%m/%Y").date()


def _pack_metadata(description: str) -> dict:
    folded = _fold(description).replace(" ", "")
    match = re.search(r"(?:P-)?(\d+)X(\d+(?:[,.]\d+)?)(ML|M|G|L)\b", folded)
    if match:
        unit = match.group(3)
        if unit == "M":
            unit = "ML"
        return {
            "purchase_unit": "pack",
            "pack_count": int(match.group(1)),
            "content_per_unit_value": money(match.group(2)) if "," in match.group(2) else Decimal(match.group(2)),
            "content_per_unit_unit": unit.lower() if unit != "L" else "L",
        }
    match = re.search(r"\b(\d+)\s*(?:UNID|U)\b", _fold(description))
    if match:
        return {"purchase_unit": "pack", "pack_count": int(match.group(1))}
    return {}


NON_CONSUMABLE_CAPACITY_MARKERS = (
    "BAS ",
    "BASURA",
    "BOLSA",
    "PAPEL",
    "ALUMINIO",
    "ROLLO",
    "CONTENEDOR",
)


def _content_metadata(description: str) -> dict:
    pack = _pack_metadata(description)
    if pack:
        return pack
    folded = _fold(description)
    if any(marker in folded for marker in NON_CONSUMABLE_CAPACITY_MARKERS):
        return {}
    match = re.search(r"\b(\d+(?:[,.]\d+)?)\s*(KG|G|ML|L)\b", folded)
    if not match:
        return {}
    value = money(match.group(1)) if "," in match.group(1) else Decimal(match.group(1))
    unit = match.group(2)
    return {
        "content_per_unit_value": value,
        "content_per_unit_unit": unit.lower() if unit != "L" else "L",
    }


def _new_item(
    description: str,
    gross: Decimal,
    quantity: Decimal = Decimal("1"),
    unit_price: Decimal | None = None,
    confidence: Decimal | None = None,
    purchase_unit: PurchaseUnit = PurchaseUnit.UNIT,
) -> ReceiptItem:
    code_match = re.search(r"\b([A-Z]{1,3}\d{2,4}|\d[A-Z]\s?\d{2,4})\b", description.upper())
    code = code_match.group(1).replace(" ", "") if code_match else None
    metadata = _content_metadata(description)
    metadata.setdefault("purchase_unit", purchase_unit)
    return ReceiptItem(
        original_text=description,
        alias_text=description.strip(),
        product_code=code,
        purchase_quantity=quantity,
        unit_price=unit_price,
        line_gross=gross,
        line_final=gross,
        extraction_confidence=confidence,
        **metadata,
    )


def _parse_mercadona(text: str, confidence: Decimal | None = None) -> ReceiptExtraction:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items: list[ReceiptItem] = []
    pending: tuple[Decimal, str] | None = None
    total: Decimal | None = None
    store_name = None

    for line in lines:
        folded = _fold(line)
        if folded.startswith("TIENDA:"):
            store_name = line.split(":", 1)[1].strip()
            continue
        total_match = re.match(rf"TOTAL(?:\s+A\s+PAGAR)?\s+({MONEY_RE})", folded)
        if total_match:
            total = money(total_match.group(1))
            continue
        detail_match = re.match(rf"P\.?\s*UNIT\s+({MONEY_RE})\s+IMPORTE\s+({MONEY_RE})", folded)
        if detail_match and pending:
            quantity, description = pending
            items.append(_new_item(description, money(detail_match.group(2)), quantity, money(detail_match.group(1)), confidence))
            pending = None
            continue
        product_with_total = re.match(rf"(\d+)\s+(.+?)\s+({MONEY_RE})$", line)
        if product_with_total and not folded.startswith(("FECHA", "PAGO")):
            quantity = Decimal(product_with_total.group(1))
            description = product_with_total.group(2).strip()
            gross = money(product_with_total.group(3))
            unit_price = gross / quantity if quantity else None
            items.append(_new_item(description, gross, quantity, unit_price, confidence))
            continue
        product_pending = re.match(r"(\d+)\s+(.+)$", line)
        if product_pending and not re.match(r"\d{2}/\d{2}/\d{4}", line):
            pending = (Decimal(product_pending.group(1)), product_pending.group(2).strip())

    if total is None:
        raise ValueError("Mercadona ticket has no readable total")
    purchase_date = _date_from_text(text)
    return ReceiptExtraction(
        supermarket="Mercadona",
        store_name=store_name,
        purchase_date=purchase_date,
        purchase_date_source=PurchaseDateSource.OCR if purchase_date else PurchaseDateSource.UNKNOWN,
        article_count=int(sum(item.purchase_quantity for item in items)),
        printed_product_line_count=len(items),
        subtotal_gross=total,
        amount_paid=total,
        raw_ocr_text=text,
        extraction_confidence=confidence,
        items=items,
    )


def _parse_carrefour(text: str, confidence: Decimal | None = None) -> ReceiptExtraction:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items: list[ReceiptItem] = []
    adjustments: list[ReceiptAdjustment] = []
    subtotal: Decimal | None = None
    total: Decimal | None = None
    coupon = Decimal("0")
    article_count: int | None = None
    store_name = None

    for line in lines:
        folded = _fold(line)
        if folded.startswith("TIENDA:"):
            store_name = line.split(":", 1)[1].strip()
            continue
        article_match = re.search(r"(\d+)\s+ART\.?", folded)
        if article_match:
            article_count = int(article_match.group(1))
            continue
        subtotal_match = re.match(rf"SUBTOTAL\s+({MONEY_RE})", folded)
        if subtotal_match:
            subtotal = money(subtotal_match.group(1))
            continue
        total_match = re.match(rf"TOTAL\s+A\s+PAGAR\s+({MONEY_RE})", folded)
        if total_match:
            total = money(total_match.group(1))
            continue
        coupon_match = re.match(r"(?:TO|DTO|DIO)\.?\s+(?:CUPON|VALE)\s+(.+?)\s+-(\d+[,.]\d{2})$", folded)
        if coupon_match:
            amount = abs(money(coupon_match.group(2)))
            coupon += amount
            adjustments.append(
                ReceiptAdjustment(
                    type="receipt_coupon",
                    description=coupon_match.group(1).strip().title(),
                    original_text=line,
                    amount=amount,
                    affects_amount_paid=True,
                )
            )
            continue
        info_match = re.match(
            rf"(SALDO ACUMULADO|ACUMULADO CLUB|DESCUENTOS|TOTAL VENTAJAS EN ESTA COMPRA)\s+({MONEY_RE})",
            folded,
        )
        if info_match:
            label = info_match.group(1)
            adjustment_type = "loyalty_balance" if label in {"SALDO ACUMULADO", "ACUMULADO CLUB"} else "summary_savings"
            adjustments.append(
                ReceiptAdjustment(
                    type=adjustment_type,
                    description=("Saldo/valor de fidelización informado" if adjustment_type == "loyalty_balance" else "Línea resumen de ahorro"),
                    original_text=line,
                    amount=money(info_match.group(2)),
                    affects_amount_paid=False,
                )
            )
            continue
        discount_match = re.match(
            r"((?:\d+\s+)?3X2\s+EN\s+EL\s+MAS\s+BA|DESCUENTO\s+EN\s+2[Aaª]\s+UNIDAD)\s+((?:[A-Z]{1,3}|\d[A-Z])\d{2,4})\s+-(\d+[,.]\d{2})",
            folded,
        )
        if discount_match:
            amount = abs(money(discount_match.group(3)))
            adjustments.append(
                ReceiptAdjustment(
                    type="line_discount",
                    description=(
                        "Descuento inmediato 3x2"
                        if "3X2" in discount_match.group(1)
                        else "Descuento inmediato en segunda unidad"
                    ),
                    original_text=line,
                    code=discount_match.group(2),
                    amount=amount,
                    affects_amount_paid=True,
                )
            )
            continue
        explanation = re.match(rf"(\d+)\s*X\s*\(({MONEY_RE})\)", folded)
        if explanation and items:
            items[-1].purchase_quantity = Decimal(explanation.group(1))
            items[-1].unit_price = money(explanation.group(2))
            continue
        if folded.startswith(("CARREFOUR", "FECHA", "VENTAJAS", "IVA", "TIPO", "BASE", "CUOTA", "PAGO")):
            continue
        product_match = re.match(rf"(.+?)\s+({MONEY_RE})$", line)
        if product_match:
            description = product_match.group(1).strip()
            if description.startswith("-"):
                continue
            gross = money(product_match.group(2))
            if gross < 0:
                adjustments.append(
                    ReceiptAdjustment(
                        type="unlinked_line_discount",
                        description="Descuento inmediato sin producto enlazado",
                        original_text=line,
                        amount=abs(gross),
                        affects_amount_paid=True,
                    )
                )
                continue
            items.append(_new_item(description, gross, unit_price=gross, confidence=confidence))

    if subtotal is None or total is None:
        raise ValueError("Carrefour ticket has no readable subtotal or total")
    purchase_date = _date_from_text(text)
    return ReceiptExtraction(
        supermarket="Carrefour",
        store_name=store_name,
        purchase_date=purchase_date,
        purchase_date_source=PurchaseDateSource.OCR if purchase_date else PurchaseDateSource.UNKNOWN,
        article_count=article_count,
        printed_product_line_count=len(items),
        subtotal_gross=subtotal,
        coupon_applied=coupon,
        amount_paid=total,
        raw_ocr_text=text,
        extraction_confidence=confidence,
        items=items,
        adjustments=adjustments,
    )


def _parse_lupa(text: str, confidence: Decimal | None = None) -> ReceiptExtraction:
    """Parse Lupa/Semark tickets whose OCR separates descriptions and prices."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items: list[ReceiptItem] = []
    total: Decimal | None = None
    total_index: int | None = None

    for index, line in enumerate(lines):
        folded = _fold(line)
        if not folded.startswith("TOTAL"):
            continue
        inline_total = re.match(rf"TOTAL(?:\s+A\s+PAGAR)?\s+({MONEY_RE})", folded)
        if inline_total:
            total = money(inline_total.group(1))
            total_index = index
            break
        if index + 1 < len(lines):
            next_total = re.fullmatch(rf"\s*({MONEY_RE})\s*[€E]?\s*", _fold(lines[index + 1]))
            if next_total:
                total = money(next_total.group(1))
                total_index = index
                break

    if total is None or total_index is None:
        raise ValueError("Lupa ticket has no readable total")

    for index in range(total_index - 1):
        description = lines[index]
        folded = _fold(description)
        price_match = re.fullmatch(rf"\s*({MONEY_RE})\s*[€E]?\s*", _fold(lines[index + 1]))
        if not price_match or not any(char.isalpha() for char in description):
            continue
        if folded.startswith(("C.I.F", "CIF", "AV.", "CALLE", "SEMARK", "TUS VECINOS")):
            continue
        gross = money(price_match.group(1))
        items.append(_new_item(description, gross, unit_price=gross, confidence=confidence))

    if not items:
        raise ValueError("Lupa ticket has no readable product lines")
    purchase_date = _date_from_text(text)
    return ReceiptExtraction(
        supermarket="Lupa",
        purchase_date=purchase_date,
        purchase_date_source=PurchaseDateSource.OCR if purchase_date else PurchaseDateSource.UNKNOWN,
        article_count=len(items),
        printed_product_line_count=len(items),
        subtotal_gross=total,
        amount_paid=total,
        raw_ocr_text=text,
        extraction_confidence=confidence,
        items=items,
    )


def _parse_aldi(text: str, confidence: Decimal | None = None) -> ReceiptExtraction:
    """Parse Aldi tickets whose Vision OCR returns descriptions and amounts out of phase."""
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines: list[str] = []
    index = 0
    while index < len(raw_lines):
        if (
            re.fullmatch(r"\d+", raw_lines[index])
            and index + 1 < len(raw_lines)
            and re.match(r"^,\d{2}\b", raw_lines[index + 1])
        ):
            lines.append(raw_lines[index] + raw_lines[index + 1])
            index += 2
        else:
            lines.append(raw_lines[index])
            index += 1

    folded_lines = [_fold(line) for line in lines]
    try:
        total_marker = next(index for index, line in enumerate(folded_lines) if line == "A PAGAR")
    except StopIteration as exc:
        raise ValueError("Aldi ticket has no readable total marker") from exc

    start = 0
    for index, line in enumerate(folded_lines[:total_marker]):
        if "CERRADO" in line:
            start = index + 1

    region = lines[start:total_marker]
    folded_region = folded_lines[start:total_marker]
    money_only = re.compile(rf"^\s*({MONEY_RE})\s*(?:€|E)?(?:\s+\d+)?\s*$")
    formula = re.compile(rf"^(\d+(?:[,.]\d+)?)\s*K(?:G|[Oo])?\s*X\s*({MONEY_RE})\s*€?/KG$")
    unit_formula = re.compile(rf"^(\d+)\s*X\s*({MONEY_RE})\s*€?$")

    descriptions: list[tuple[str, str | None]] = []
    prices: list[Decimal] = []
    pending_detail: str | None = None
    for line, folded in zip(region, folded_region, strict=True):
        price_match = money_only.fullmatch(folded)
        if price_match:
            prices.append(money(price_match.group(1)))
            continue
        is_formula = formula.fullmatch(folded) or unit_formula.fullmatch(folded)
        if is_formula:
            pending_detail = folded
            continue
        if any(char.isalpha() for char in folded):
            descriptions.append((line, pending_detail))
            pending_detail = None

    after_total = []
    for folded in folded_lines[total_marker + 1 :]:
        match = money_only.fullmatch(folded)
        if match:
            after_total.append(money(match.group(1)))
        elif after_total:
            break
    if len(after_total) < 2:
        raise ValueError("Aldi ticket has no readable final item amount and total")
    prices.append(after_total[0])
    total = after_total[1]

    if not descriptions or len(descriptions) != len(prices):
        raise ValueError("Aldi ticket product descriptions and amounts do not align")
    if abs(sum(prices, Decimal("0")) - total) > Decimal("0.01"):
        raise ValueError("Aldi ticket product amounts do not reconcile with total")

    items: list[ReceiptItem] = []
    for (description, detail), gross in zip(descriptions, prices, strict=True):
        quantity = Decimal("1")
        unit_price = gross
        purchase_unit = PurchaseUnit.UNIT
        if detail:
            weight_match = formula.fullmatch(detail)
            count_match = unit_formula.fullmatch(detail)
            if weight_match:
                quantity = Decimal(weight_match.group(1).replace(",", "."))
                unit_price = money(weight_match.group(2))
                purchase_unit = PurchaseUnit.KG
            elif count_match:
                quantity = Decimal(count_match.group(1))
                unit_price = money(count_match.group(2))
        items.append(
            _new_item(
                description,
                gross,
                quantity,
                unit_price,
                confidence,
                purchase_unit,
            )
        )

    purchase_date = _date_from_text(text)
    return ReceiptExtraction(
        supermarket="Aldi",
        purchase_date=purchase_date,
        purchase_date_source=PurchaseDateSource.OCR if purchase_date else PurchaseDateSource.UNKNOWN,
        printed_product_line_count=len(items),
        subtotal_gross=total,
        amount_paid=total,
        raw_ocr_text=text,
        extraction_confidence=confidence,
        items=items,
    )


def _parse_dia(text: str, confidence: Decimal | None = None) -> ReceiptExtraction:
    """Parse photographed DIA tickets with description/amount columns split by Vision OCR."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    folded_lines = [_fold(line) for line in lines]

    try:
        start = next(index for index, line in enumerate(folded_lines) if line.startswith("PVP/UNIT")) + 1
    except StopIteration as exc:
        raise ValueError("DIA ticket has no readable product header") from exc

    try:
        total_marker = next(
            index for index in range(start, len(lines)) if folded_lines[index].startswith("TOTAL COMPRA")
        )
    except StopIteration as exc:
        raise ValueError("DIA ticket has no readable purchase total") from exc

    offer_marker = next(
        (index for index in range(start, total_marker) if folded_lines[index] == "OFERTAS"),
        total_marker,
    )
    amount_only = re.compile(rf"^\s*({MONEY_RE})(?:\s*[ABC](?:[L1I])?)?\s*$")
    unit_formula = re.compile(rf"^\s*({MONEY_RE})\s*€?/(UD|KG)\s*$")
    quantity_formula = re.compile(r"^\s*(\d+(?:[,.]\d+)?)\s*(UD|UN|KG)\s*$")

    items: list[ReceiptItem] = []
    pending_description: str | None = None
    quantity = Decimal("1")
    unit_price: Decimal | None = None
    purchase_unit = PurchaseUnit.UNIT

    for line, folded in zip(lines[start:offer_marker], folded_lines[start:offer_marker], strict=True):
        if folded in {"A", "B", "C", "AI", "AL"}:
            continue
        formula_match = unit_formula.fullmatch(folded)
        if formula_match and pending_description is not None:
            unit_price = money(formula_match.group(1))
            continue
        quantity_match = quantity_formula.fullmatch(folded)
        if quantity_match and pending_description is not None:
            quantity = Decimal(quantity_match.group(1).replace(",", "."))
            purchase_unit = PurchaseUnit.KG if quantity_match.group(2) == "KG" else PurchaseUnit.UNIT
            continue
        amount_match = amount_only.fullmatch(folded)
        if amount_match and pending_description is not None:
            gross = money(amount_match.group(1))
            items.append(
                _new_item(
                    pending_description,
                    gross,
                    quantity,
                    unit_price if unit_price is not None else gross,
                    confidence,
                    purchase_unit,
                )
            )
            pending_description = None
            quantity = Decimal("1")
            unit_price = None
            purchase_unit = PurchaseUnit.UNIT
            continue
        if any(char.isalpha() for char in folded):
            pending_description = line
            quantity = Decimal("1")
            unit_price = None
            purchase_unit = PurchaseUnit.UNIT

    if not items:
        raise ValueError("DIA ticket has no readable product lines")

    adjustments: list[ReceiptAdjustment] = []
    pending_offer: str | None = None
    for line, folded in zip(
        lines[offer_marker + 1 : total_marker],
        folded_lines[offer_marker + 1 : total_marker],
        strict=True,
    ):
        amount_match = amount_only.fullmatch(folded)
        if amount_match and pending_offer is not None:
            amount = money(amount_match.group(1))
            if amount < 0:
                adjustments.append(
                    ReceiptAdjustment(
                        type="line_discount",
                        description="Descuento inmediato DIA",
                        original_text=f"{pending_offer} {line}",
                        amount=abs(amount),
                        affects_amount_paid=True,
                        linked_alias=pending_offer,
                    )
                )
            pending_offer = None
            continue
        if any(char.isalpha() for char in folded):
            pending_offer = line

    total: Decimal | None = None
    inline_total = re.search(rf"({MONEY_RE})", lines[total_marker])
    if inline_total:
        total = money(inline_total.group(1))
    elif total_marker + 1 < len(lines):
        total_match = amount_only.fullmatch(folded_lines[total_marker + 1])
        if total_match:
            total = money(total_match.group(1))
    if total is None:
        raise ValueError("DIA ticket purchase total is unreadable")

    purchase_date = _date_from_text(text)
    return ReceiptExtraction(
        supermarket="DIA",
        purchase_date=purchase_date,
        purchase_date_source=PurchaseDateSource.OCR if purchase_date else PurchaseDateSource.UNKNOWN,
        printed_product_line_count=len(items),
        subtotal_gross=total,
        amount_paid=total,
        raw_ocr_text=text,
        extraction_confidence=confidence,
        items=items,
        adjustments=adjustments,
    )


def parse_receipt_text(text: str, confidence: Decimal | None = None) -> ReceiptExtraction:
    folded = _fold(text)
    if "GRUPO DIA" in folded or "DIA RETAIL" in folded:
        return _parse_dia(text, confidence)
    if re.search(r"(?m)^AL\s*DI\s*$", folded) or re.search(r"(?m)^ALDI\s*$", folded):
        return _parse_aldi(text, confidence)
    if "MERCADONA" in folded:
        return _parse_mercadona(text, confidence)
    if "CARREFOUR" in folded:
        return _parse_carrefour(text, confidence)
    if "SEMARK AC GROUP" in folded or "SUPERMERCADOS LUPA" in folded:
        return _parse_lupa(text, confidence)
    raise ValueError("Unsupported or unrecognized supermarket")
