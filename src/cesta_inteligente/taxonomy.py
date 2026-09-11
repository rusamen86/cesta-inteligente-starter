from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass


NAMESPACE = uuid.UUID("b9fbb13f-e538-4f36-80d8-bd55aee92c16")


def _fold(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.upper())
        if not unicodedata.combining(char)
    )


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{uuid.uuid5(NAMESPACE, f'{prefix}:{_fold(value).strip()}')}"


@dataclass(frozen=True)
class ProductTaxonomy:
    product_stable_id: str
    product_name: str
    comparable_stable_id: str
    comparable_name: str
    family_stable_id: str
    family_name: str
    category_stable_id: str
    category_name: str


COMPARABLE_RULES = (
    (r"\bBEBIDA (?:DE )?ALMENDRAS?\b", "Bebida de almendras", "Bebidas vegetales", "Bebidas"),
    (r"\bBEBIDA (?:DE )?AVELLANAS?\b", "Bebida de avellanas", "Bebidas vegetales", "Bebidas"),
    (r"\bBEBIDA (?:DE )?SOJA\b", "Bebida de soja", "Bebidas vegetales", "Bebidas"),
    (r"\bBEBIDA (?:DE )?AVENA\b", "Bebida de avena", "Bebidas vegetales", "Bebidas"),
    (r"\b(?:QUESO FETA|APETINA)\b", "Queso feta", "Quesos", "Lácteos y huevos"),
    (r"\bHUEVO", "Huevos", "Huevos", "Lácteos y huevos"),
    (r"\bLECHE\b", "Leche", "Leche", "Lácteos y huevos"),
    (r"\bTOMATE FRITO\b", "Tomate frito", "Tomate en conserva", "Despensa"),
    (r"\bTOMATE TRITURADO\b", "Tomate triturado", "Tomate en conserva", "Despensa"),
    (r"\bVINAGRE\b", "Vinagre", "Aceites y condimentos", "Despensa"),
    (r"\bACEITE (?:DE )?OLIVA\b", "Aceite de oliva", "Aceites y condimentos", "Despensa"),
    (r"\bATUN (?:CLARO )?NATURAL\b", "Atún al natural", "Conservas de pescado", "Despensa"),
    (r"\bGARBANZ", "Garbanzos", "Legumbres", "Despensa"),
    (r"\bQUESO (?:CREMA|PHILADELPHIA)\b|\bCREMA PHILADELPHIA\b", "Queso crema", "Quesos", "Lácteos y huevos"),
    (r"\bQUESO QUARK\b", "Queso quark", "Quesos", "Lácteos y huevos"),
)

FAMILY_RULES = (
    (r"\b(?:LAVAVAJILLAS|FAIRY|SAL LAVAVAJILLAS)\b", "Lavavajillas", "Limpieza y hogar"),
    (r"\bPAPEL COCINA\b", "Papel doméstico", "Limpieza y hogar"),
    (r"\b(?:CHAMPU|DENTIFRICO|BALSAMO LABIAL|GEL (?:DE )?(?:BANO|DUCHA|MANGO))\b", "Higiene personal", "Higiene y cuidado personal"),
    (r"\b(?:CERV\.?|CERVEZA)\b", "Cerveza", "Bebidas"),
    (r"\b(?:AGUA|KOMBUCHA|LATTE|CAFE)\b", "Bebidas", "Bebidas"),
    (r"\bQUESO\b|\bAPETINA\b|\bMOZZARELLA\b", "Quesos", "Lácteos y huevos"),
    (r"\b(?:YOGUR|GRIEGO NATURAL)\b", "Yogures", "Lácteos y huevos"),
    (r"\b(?:PAN|HOGAZA|BAGELS?)\b", "Pan", "Panadería"),
    (r"\b(?:NAPOLITANAS?|TORTILLA TRIGO|WRAPS?)\b", "Bollería y masas", "Panadería"),
    (r"\b(?:BARQUETA PASTA|ARROZ 3 DELICIAS)\b", "Platos preparados", "Platos preparados"),
    (r"\b(?:RUFFLES|CHEETOS|CHOCOLATE|NUEZ|NUECES|PATATAS? SANTA ANA)\b", "Snacks y dulces", "Snacks y dulces"),
    (r"\b(?:JAMON|PECHUGA PAVO|PEPPERONI|MORTADELA)\b", "Charcutería", "Carne y charcutería"),
    (r"\b(?:HAMBURGUESA|PECHUGA POLLO|PICADA)\b", "Carne", "Carne y charcutería"),
    (r"\b(?:AJOS?|JUDIAS? VERDES?|CHAMPINON|KIWI|PINA|AGUACATE|PATATA|PLATANO|TOMATE CHERRY)\b", "Frutas y verduras", "Frutas y verduras"),
    (r"\b(?:MAIZ|PEPINILLO)\b", "Conservas vegetales", "Despensa"),
    (r"\bSAL YODADA\b", "Sal y especias", "Despensa"),
)


def _sku_name(alias: str) -> str:
    value = re.sub(r"\b[A-Z]{1,3}\d{2,4}\b", "", alias.upper())
    return re.sub(r"\s+", " ", value).strip().title()


def _fallback_family(sku_name: str) -> str:
    value = re.sub(r"\bP-?\d+X\d+(?:[,.]\d+)?(?:ML|M|G|L)\b", "", sku_name.upper())
    value = re.sub(r"\b\d+X\d+(?:[,.]\d+)?(?:ML|G|L)\b", "", value)
    value = re.sub(r"\b\d+(?:[,.]\d+)?\s*(?:KG|G|ML|L|UNID|U)\b", "", value)
    return re.sub(r"\s+", " ", value).strip().title()


def _fallback_comparable(sku_name: str) -> str:
    value = _fallback_family(sku_name).upper()
    value = re.sub(r"\b(?:CARREFOUR|CRF|HACENDADO|MERCADONA)\b", "", value)
    return re.sub(r"\s+", " ", value).strip().title()


def classify_product(supermarket: str, alias: str, product_code: str | None = None) -> ProductTaxonomy:
    folded = _fold(alias)
    sku_name = _sku_name(alias)
    comparable_name = _fallback_comparable(sku_name)
    family_name = comparable_name
    category_name = "Sin clasificar"
    matched_comparable = False
    for pattern, candidate_comparable, candidate_family, candidate_category in COMPARABLE_RULES:
        if re.search(pattern, folded):
            comparable_name = candidate_comparable
            family_name = candidate_family
            category_name = candidate_category
            matched_comparable = True
            break
    if not matched_comparable:
        for pattern, candidate_family, candidate_category in FAMILY_RULES:
            if re.search(pattern, folded):
                family_name = candidate_family
                category_name = candidate_category
                break

    product_key = f"{supermarket}|{product_code or folded}"
    family_key = f"{category_name}|{family_name}"
    # Comparable identity is semantic, not positional: moving a product to a
    # corrected family/category later must not change its durable ID.
    comparable_key = comparable_name
    return ProductTaxonomy(
        product_stable_id=stable_id("sku", product_key),
        product_name=sku_name,
        comparable_stable_id=stable_id("comparable", comparable_key),
        comparable_name=comparable_name,
        family_stable_id=stable_id("family", family_key),
        family_name=family_name,
        category_stable_id=stable_id("category", category_name),
        category_name=category_name,
    )
