from pathlib import Path

from cesta_inteligente.database import connection, insert_receipt, reclassify_existing_products
from cesta_inteligente.pipeline import process_receipt
from cesta_inteligente.query_api import execute_query
from cesta_inteligente.queries import comparable_price_comparisons
from cesta_inteligente.taxonomy import classify_product, stable_id


def test_almond_and_hazelnut_are_distinct_comparables_in_the_same_family():
    almond = classify_product("Mercadona", "BEBIDA ALMENDRAS HACENDADO 1L")
    hazelnut = classify_product("Mercadona", "BEBIDA AVELLANAS HACENDADO 1L")

    assert almond.comparable_name == "Bebida de almendras"
    assert hazelnut.comparable_name == "Bebida de avellanas"
    assert almond.comparable_stable_id != hazelnut.comparable_stable_id
    assert almond.family_name == hazelnut.family_name == "Bebidas vegetales"
    assert almond.family_stable_id == hazelnut.family_stable_id


def test_equivalent_cross_chain_aliases_converge_without_merging_skus():
    mercadona = classify_product("Mercadona", "BEBIDA ALMENDRAS HACENDADO 1L")
    carrefour = classify_product("Carrefour", "BEBIDA DE ALMENDRAS CARREFOUR 1 L")

    assert mercadona.product_stable_id != carrefour.product_stable_id
    assert mercadona.comparable_stable_id == carrefour.comparable_stable_id
    assert mercadona.comparable_name == carrefour.comparable_name == "Bebida de almendras"


def test_feta_has_specific_comparable_and_broad_family():
    feta = classify_product("Carrefour", "QUESO FETA BIO 180G")

    assert feta.comparable_name == "Queso feta"
    assert feta.family_name == "Quesos"
    assert feta.category_name == "Lácteos y huevos"


def test_real_purchase_aliases_receive_useful_categories():
    expected = {
        "PATAT.RUFFLES JAMON 45 GR.": "Snacks y dulces",
        "HAFFE LATTE KAIKU LIGHT 230 ML": "Bebidas",
        "AGUA FONT VELLA MINERAL 1,5 L.": "Bebidas",
        "BARQUETA PASTA+RUCULA 325 GR.": "Platos preparados",
        "2 NAPOLITANAS BIKINI": "Panadería",
        "ACEITE OLIVA SL": "Despensa",
        "BALSAMO LABIAL COCO": "Higiene y cuidado personal",
        "CHAMPU FRUCTIS 380M 2E12": "Higiene y cuidado personal",
        "FAIRY PODER 1250ML 5N55": "Limpieza y hogar",
        "SAL LAVAVAJILLAS 5KG": "Limpieza y hogar",
        "ATUN CLARO NATURAL": "Despensa",
        "GARBANZO EXTRA": "Despensa",
        "TORTILLA TRIGO WRAPS 5B68": "Panadería",
        "APETINA SALMUERA": "Lácteos y huevos",
        "ARROZ 3 DELICIAS": "Platos preparados",
        "BEBIDA DE ALMENDRA": "Bebidas",
        "GRIEGO NATURAL LIGHT": "Lácteos y huevos",
        "JAMON SERRANO 5U34": "Carne y charcutería",
        "PECHUGA POLLO ENTERA": "Carne y charcutería",
        "AJOS 250GR (LOCAL)": "Frutas y verduras",
        "PATATA GUARNICION": "Frutas y verduras",
        "TOMATE CHERRY": "Frutas y verduras",
    }

    for alias, category in expected.items():
        assert classify_product("Carrefour", alias).category_name == category


def test_reclassify_existing_products_updates_old_fallback_rows(tmp_path: Path):
    source = _write_ticket(
        tmp_path,
        "old-ticket.txt",
        """CARREFOUR\nFECHA 27/08/2026\n1 ART.\nACEITE OLIVA SL 20,95\nSUBTOTAL 20,95\nTOTAL A PAGAR 20,95\n""",
    )
    db_path = tmp_path / "reclassify.db"
    insert_receipt(db_path, process_receipt(source.read_text(encoding="utf-8"), [source]))
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO categories(household_id, stable_id, name) VALUES (1, ?, 'Sin clasificar')",
            (stable_id("category", "Sin clasificar"),),
        )
        category_id = conn.execute("SELECT id FROM categories WHERE name = 'Sin clasificar'").fetchone()[0]
        conn.execute(
            "INSERT INTO product_families(household_id, stable_id, category_id, name) VALUES (1, ?, ?, 'Aceite Oliva Sl')",
            (stable_id("family", "Sin clasificar|Aceite Oliva Sl"), category_id),
        )
        family_id = conn.execute(
            "SELECT id FROM product_families WHERE category_id = ? AND name = 'Aceite Oliva Sl'",
            (category_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE comparable_products SET product_family_id = ?",
            (family_id,),
        )

    result = reclassify_existing_products(db_path)

    assert result["unclassified_items"] == 0
    with connection(db_path) as conn:
        category = conn.execute(
            """
            SELECT c.name FROM receipt_items ri
            JOIN products p ON p.id = ri.product_id
            JOIN comparable_products cp ON cp.id = p.comparable_product_id
            JOIN product_families pf ON pf.id = cp.product_family_id
            JOIN categories c ON c.id = pf.category_id
            """
        ).fetchone()[0]
    assert category == "Despensa"


def test_reclassify_accepts_multiple_aliases_in_the_same_family(tmp_path: Path):
    source = _write_ticket(
        tmp_path,
        "aliases-ticket.txt",
        """CARREFOUR\nFECHA 27/08/2026\n2 ART.\nCHAMPU ALOE FRUCTIS 2E12\n0,00\nCHAMPU FRUCTIS 380M 2E12\n7,58\nSUBTOTAL 7,58\nTOTAL A PAGAR 7,58\n""",
    )
    db_path = tmp_path / "aliases.db"
    insert_receipt(db_path, process_receipt(source.read_text(encoding="utf-8"), [source]))

    result = reclassify_existing_products(db_path)

    assert result["unclassified_items"] == 0


def _write_ticket(tmp_path: Path, name: str, text: str) -> Path:
    source = tmp_path / name
    source.write_text(text, encoding="utf-8")
    return source


def test_price_comparison_requires_same_comparable_and_normalized_unit(tmp_path: Path):
    cases = (
        (
            "mercadona-almond.txt",
            """MERCADONA S.A.\nTIENDA: City\nFECHA 19/08/2026\n1 BEBIDA ALMENDRAS HACENDADO 1L 1,10\nTOTAL 1,10\n""",
        ),
        (
            "carrefour-almond.txt",
            """CARREFOUR\nTIENDA: City\nFECHA 20/08/2026\n1 ART.\nBEBIDA DE ALMENDRAS CARREFOUR 1 L 1,20\nSUBTOTAL 1,20\nTOTAL A PAGAR 1,20\n""",
        ),
        (
            "mercadona-hazelnut.txt",
            """MERCADONA S.A.\nTIENDA: City\nFECHA 21/08/2026\n1 BEBIDA AVELLANAS HACENDADO 1L 1,30\nTOTAL 1,30\n""",
        ),
        (
            "carrefour-almond-weight.txt",
            """CARREFOUR\nTIENDA: City\nFECHA 22/08/2026\n1 ART.\nBEBIDA DE ALMENDRAS CARREFOUR 500G 1,00\nSUBTOTAL 1,00\nTOTAL A PAGAR 1,00\n""",
        ),
    )
    db_path = tmp_path / "comparisons.db"
    for name, text in cases:
        source = _write_ticket(tmp_path, name, text)
        receipt = process_receipt(text, [source])
        assert receipt.status == "valid"
        insert_receipt(db_path, receipt)

    rows = comparable_price_comparisons(db_path)

    assert len(rows) == 2
    assert {row["supermarket"] for row in rows} == {"Mercadona", "Carrefour"}
    assert {row["comparable_product"] for row in rows} == {"Bebida de almendras"}
    assert {row["normalized_unit"] for row in rows} == {"L"}

    with connection(db_path) as conn:
        hierarchy = conn.execute(
            """
            SELECT p.name AS sku, cp.name AS comparable_product,
                   pf.name AS family, c.name AS category
            FROM products p
            JOIN comparable_products cp ON cp.id = p.comparable_product_id
            JOIN product_families pf ON pf.id = cp.product_family_id
            JOIN categories c ON c.id = pf.category_id
            ORDER BY p.name
            """
        ).fetchall()
    assert hierarchy
    assert all(row["comparable_product"] for row in hierarchy)

    summary = execute_query(db_path, "comparable_store_summary", {"month": "2026-08"})
    assert summary["comparison_level"] == "comparable_product"
    assert summary["requires_compatible_normalized_unit"] is True
    assert [row["supermarket"] for row in summary["rows"]] == ["Mercadona", "Carrefour"]
    assert summary["rows"][0]["cheapest_entries"] == 1
    assert summary["rows"][0]["average_percent_over_cheapest"] == 0.0
    assert summary["rows"][1]["average_percent_over_cheapest"] == 9.09
