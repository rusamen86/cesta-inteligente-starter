from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from decimal import Decimal


MONEY_FULL = re.compile(r"^-?\s*\d+[,.]\d{2}$")
MONEY_ANY = re.compile(r"-?\s*\d+[,.]\d{2}")
CODE_FULL = re.compile(r"^(?:[A-Z]{1,3}|\d[A-Z])\s?\d{2,4}$")


def _fold(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.upper())
        if not unicodedata.combining(char)
    )


def _clean_money(value: str) -> str:
    return value.replace(" ", "").replace(".", ",")


def _money(value: str) -> Decimal:
    return Decimal(_clean_money(value).replace(",", "."))


def _money_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    fixed: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.fullmatch(r"-?\s*\d+", line) and index + 1 < len(lines) and re.fullmatch(r",\d{2}", lines[index + 1]):
            line = f"{line}{lines[index + 1]}"
            index += 1
        compact = re.sub(r"(?<=\d)\s+(?=\d{2}$)", ",", line)
        if MONEY_FULL.fullmatch(compact):
            fixed.append(_clean_money(compact))
        index += 1
    return fixed


def _normalize_mercadona(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    output: list[str] = ["MERCADONA"]
    for line in lines:
        if re.search(r"\b\d{2}/\d{2}/\d{4}\b", line):
            output.append(line)
            break
    for index, line in enumerate(lines):
        match = re.match(r"^(\d+)\s+(.+)$", line)
        if not match or re.match(r"^\d{2}/\d{2}/\d{4}", line):
            continue
        quantity = int(match.group(1))
        description = match.group(2).strip()
        values: list[str] = []
        for following in lines[index + 1 : index + 4]:
            if MONEY_FULL.fullmatch(following):
                values.append(_clean_money(following))
            else:
                break
        if quantity > 1 and len(values) >= 2:
            output.append(f"{quantity} {description}")
            output.append(f"P.Unit {values[0]} Importe {values[1]}")
        elif values:
            output.append(f"{quantity} {description} {values[-1]}")
    for index, line in enumerate(lines):
        if _fold(line).startswith("TOTAL") and index + 1 < len(lines) and MONEY_FULL.fullmatch(lines[index + 1]):
            output.append(f"TOTAL {_clean_money(lines[index + 1])}")
            break
    return "\n".join(output)


def _carrefour_descriptions(text: str) -> tuple[list[str], bool]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    folded = [_fold(line) for line in lines]
    has_header = any("CENTROS" in line and "CARREFOUR" in line for line in folded[:20])
    start = 0
    if has_header:
        separators = [index for index, line in enumerate(lines) if len(line) >= 8 and set(line) <= {"*"}]
        if separators:
            start = separators[0] + 1
    descriptions: list[str] = []
    for line in lines[start:]:
        normalized = _fold(line)
        if normalized.startswith("SUBTOTAL"):
            break
        if MONEY_FULL.fullmatch(line) or MONEY_ANY.fullmatch(line):
            continue
        if CODE_FULL.fullmatch(normalized.replace(" ", "")):
            if descriptions:
                descriptions[-1] = f"{descriptions[-1]} {normalized.replace(' ', '')}"
            continue
        if re.match(r"^\d+\s*X\b", normalized) or normalized in {"(", ")"} or normalized.endswith(")"):
            continue
        if not any(char.isalpha() for char in line):
            continue
        if normalized.startswith(("CENTROS ", "CIUDAD", "CIF", "TELF", "TELEFONO", "ANOS ", "VENTAJAS")):
            continue
        if normalized in {"IONES?", "SUBPLEANOSFELIZ"}:
            continue
        descriptions.append(line)
    return descriptions, has_header


def _carrefour_inline_entries(text: str) -> list[tuple[str, str, int | None, str | None]]:
    """Read Carrefour PDF/Vision output where descriptions and amounts arrive sequentially.

    This path is accepted only when its signed line totals reconcile to the printed subtotal;
    otherwise the older right-column pairing remains the fallback for photographed tickets.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = 0
    separators = [index for index, line in enumerate(lines) if len(line) >= 8 and set(line) <= {"*"}]
    if separators:
        start = separators[0] + 1
    entries: list[tuple[str, str, int | None, str | None]] = []
    description_parts: list[str] = []
    quantity: int | None = None
    unit_price: str | None = None

    for line in lines[start:]:
        folded = _fold(line)
        if folded.startswith("SUBTOTAL"):
            break
        inline = re.match(r"^(.+?)\s+(-?\s*\d+[,.]\d{2})$", line)
        if inline and any(char.isalpha() for char in inline.group(1)):
            description_parts.append(inline.group(1).strip())
            entries.append((" ".join(description_parts), _clean_money(inline.group(2)), quantity, unit_price))
            description_parts = []
            quantity = None
            unit_price = None
            continue
        quantity_match = re.match(r"^(\d+)\s*[X×]\s*(?:\(|1\s*$)", folded)
        if quantity_match:
            quantity = int(quantity_match.group(1))
            continue
        unit_match = re.fullmatch(r"\s*(\d+[,.]\d{2})\s*\)?\s*", line)
        if unit_match and quantity is not None and unit_price is None:
            unit_price = _clean_money(unit_match.group(1))
            continue
        total_match = re.fullmatch(r"\s*(-?\s*\d+[,.]\d{2})\s*", line)
        if total_match and description_parts:
            entries.append((" ".join(description_parts), _clean_money(total_match.group(1)), quantity, unit_price))
            description_parts = []
            quantity = None
            unit_price = None
            continue
        compact_code = folded.replace(" ", "")
        if CODE_FULL.fullmatch(compact_code):
            if description_parts:
                description_parts.append(compact_code)
            continue
        if not any(char.isalpha() for char in line):
            continue
        if folded.startswith(("CENTROS ", "CIUDAD", "CIF", "TELF", "TELEFONO")):
            continue
        description_parts.append(line)
    return entries


def _canonical_date(text: str) -> str | None:
    match = re.search(r"\b(\d{2})[/-](\d{2})[/-](\d{2}|\d{4})\b", text)
    if not match:
        return None
    year = match.group(3)
    if len(year) == 2:
        year = f"20{year}"
    return f"{match.group(1)}/{match.group(2)}/{year}"


def _carrefour_coupon_lines(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    folded = [_fold(line) for line in lines]
    try:
        start = next(index for index, line in enumerate(folded) if line.startswith("SUBTOTAL")) + 1
    except StopIteration:
        return []
    try:
        end = next(index for index in range(start, len(folded)) if "TOTAL A PAGAR" in folded[index])
    except StopIteration:
        end = min(len(lines), start + 30)
    coupons: list[str] = []
    index = start
    while index < end:
        label = folded[index]
        if not (re.match(r"^(?:DTO|DIO|TO)\.?\s+(?:CUPON|VALE)\b", label)):
            index += 1
            continue
        parts = [lines[index]]
        amount: str | None = None
        for following in range(index + 1, min(end, index + 5)):
            amount_match = re.fullmatch(r"\s*(-\s*\d+[,.]\d{2})\s*", lines[following])
            if amount_match:
                amount = _clean_money(amount_match.group(1))
                index = following
                break
            if any(char.isalpha() for char in lines[following]):
                parts.append(lines[following])
        if amount is not None:
            canonical_label = " ".join(parts)
            canonical_label = re.sub(r"^(?:DIO|TO)\.", "DTO.", canonical_label, flags=re.IGNORECASE)
            coupons.append(f"{canonical_label} {amount}")
        index += 1
    return coupons


def _normalize_carrefour(text: str, right_column_text: str) -> str:
    descriptions, has_header = _carrefour_descriptions(text)
    subtotal_position = _fold(text).find("SUBTOTAL")
    item_text = text[:subtotal_position] if subtotal_position >= 0 else text
    full_prices = _money_lines(item_text)
    right_prices = _money_lines(right_column_text)
    first_description = next((index for index, line in enumerate(text.splitlines()) if any(char.isalpha() for char in line)), 0)
    leading_slice = "\n".join(text.splitlines()[first_description : first_description + 8])
    continuation_starts_inside_quantity = not has_header and re.search(r"\d+\s*X\s*\(", _fold(leading_slice))
    if continuation_starts_inside_quantity:
        full_prices = full_prices[1:]
        right_prices = right_prices[1:]
    subtotal_match = re.search(r"(?is)SUBTOTAL\D{0,30}(\d+[,.]\d{2})", text)
    if subtotal_match:
        subtotal_value = _clean_money(subtotal_match.group(1))
        if subtotal_value in right_prices:
            right_prices = right_prices[: right_prices.index(subtotal_value)]

    inline_entries = _carrefour_inline_entries(text)
    inline_total = sum((_money(price) for _, price, _, _ in inline_entries), Decimal("0"))
    printed_subtotal = _money(subtotal_match.group(1)) if subtotal_match else None

    # Keep only the item region. Header pages may have missing prices at the end because the next
    # photo overlaps; continuation pages may begin with a line-total whose description is above the crop.
    if has_header and full_prices and len(full_prices) < len(descriptions):
        try:
            missing_prefix_count = right_prices.index(full_prices[0])
        except ValueError:
            missing_prefix_count = 0
        prices = right_prices[:missing_prefix_count] + full_prices
    elif len(full_prices) >= len(descriptions):
        prices = full_prices
    else:
        prices = right_prices
    if len(prices) > len(descriptions):
        excess = len(prices) - len(descriptions)
        prices = prices[:-excess] if has_header else prices[excess:]
    if len(prices) < len(descriptions):
        # Prefer the full OCR sequence when the narrow crop lost a digit; then preserve only the
        # mathematically pairable suffix/prefix. Incomplete pages remain incomplete and use 90 s.
        fallback = full_prices
        if len(fallback) >= len(descriptions):
            prices = fallback[-len(descriptions) :] if not has_header else fallback[: len(descriptions)]

    pair_count = min(len(descriptions), len(prices))
    descriptions = descriptions[:pair_count]
    prices = prices[:pair_count]

    unit_prices = [_clean_money(value) for value in re.findall(r"(\d+[,.]\d{2})\s*\)", text)]
    quantity_by_index: dict[int, str] = {}
    for unit_price in unit_prices:
        expected = _money(unit_price) * 2
        for index, gross in enumerate(prices):
            if index in quantity_by_index:
                continue
            if abs(_money(gross) - expected) <= Decimal("0.01"):
                quantity_by_index[index] = unit_price
                break

    output = ["CENTROS COMERCIALES CARREFOUR S.A."]
    date_value = _canonical_date(text)
    if date_value:
        output.append(f"FECHA {date_value}")
    article_match = re.search(r"(\d+)\s+ART\.?", _fold(text))
    if article_match:
        output.append(f"{article_match.group(1)} ART.")
    if printed_subtotal is not None and inline_entries and abs(inline_total - printed_subtotal) <= Decimal("0.01"):
        for description, price, quantity, unit_price in inline_entries:
            output.append(f"{description} {price}")
            if quantity is not None and unit_price is not None:
                output.append(f"{quantity} X ({unit_price})")
    else:
        for index, (description, price) in enumerate(zip(descriptions, prices, strict=True)):
            output.append(f"{description} {price}")
            if index in quantity_by_index:
                output.append(f"2 X ({quantity_by_index[index]})")

    folded_lines = [_fold(line.strip()) for line in text.splitlines() if line.strip()]
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    for label, canonical in (
        ("SUBTOTAL", "SUBTOTAL"),
        ("TOTAL A PAGAR", "TOTAL A PAGAR"),
        ("ACUMULADO CLUB", "ACUMULADO CLUB"),
        ("DESCUENTOS", "DESCUENTOS"),
        ("TOTAL VENTAJAS EN ESTA COMPRA", "TOTAL VENTAJAS EN ESTA COMPRA"),
    ):
        for index, folded in enumerate(folded_lines):
            if label not in folded:
                continue
            values = MONEY_ANY.findall(raw_lines[index])
            if not values:
                for following in raw_lines[index + 1 : index + 4]:
                    values = MONEY_ANY.findall(following)
                    if values:
                        break
            if values:
                output.append(f"{canonical} {_clean_money(values[-1])}")
            break
    output.extend(_carrefour_coupon_lines(text))

    balance_match = re.search(r"(?is)SALDO\s+ACUMULADO\s+A\s+\d{2}[/-]\d{2}[/-]\d{2,4}\s*:\s*(\d+[,.]\d{2})", text)
    if balance_match:
        output.append(f"SALDO ACUMULADO {_clean_money(balance_match.group(1))}")
    return "\n".join(output)


def normalize_vision_receipt_text(text: str, right_column_text: str = "") -> str:
    folded = _fold(text)
    # DIA tickets also contain a generic ``TOTAL A PAGAR`` footer. Detect the
    # chain before Carrefour's permissive layout fallback so a clear DIA
    # header cannot be rewritten into a synthetic Carrefour ticket.
    if "GRUPO DIA" in folded or "DIA RETAIL" in folded:
        return text.strip()
    if "MERCADONA" in folded:
        return _normalize_mercadona(text)
    if "CARREFOUR" in folded or "SUBTOTAL" in folded or "TOTAL A PAGAR" in folded:
        return _normalize_carrefour(text, right_column_text)
    return text.strip()


def merge_ocr_pages(page_texts: list[str]) -> str:
    """Merge overlapping receipt photos without duplicating the shared middle section."""
    merged: list[str] = []
    metadata: list[str] = []
    for page_text in page_texts:
        page = [line.strip() for line in page_text.splitlines() if line.strip()]
        metadata.extend(line for line in page if re.search(r"\b\d+\s+ART\.?\b", _fold(line)))
        if not merged:
            merged = page
            continue
        left = [_fold(line) for line in merged]
        right = [_fold(line) for line in page]
        match = SequenceMatcher(a=left, b=right, autojunk=False).find_longest_match()
        if match.size >= 3:
            merged = merged[: match.a] + page[match.b :]
        else:
            merged.extend(page)
    existing = {_fold(line) for line in merged}
    for line in metadata:
        if _fold(line) not in existing:
            merged.insert(1 if merged else 0, line)
            existing.add(_fold(line))
    return "\n".join(merged)
