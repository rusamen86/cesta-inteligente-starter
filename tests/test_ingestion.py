from datetime import date
from pathlib import Path

from cesta_inteligente.database import (
    confirm_receipt_purchase_date,
    connection,
    get_receipt,
    insert_receipt,
    receipt_count,
)
from cesta_inteligente.pipeline import process_receipt
from cesta_inteligente.queries import monthly_summary


def test_same_ticket_is_idempotent(tmp_path: Path, mercadona_case):
    receipt, _ = mercadona_case
    db_path = tmp_path / "cesta.db"

    first = insert_receipt(db_path, receipt)
    second = insert_receipt(db_path, receipt)

    assert first.inserted is True
    assert second.inserted is False
    assert first.receipt_id == second.receipt_id
    assert receipt_count(db_path) == 1
    stored = get_receipt(db_path, first.receipt_id)
    assert stored["supermarket"] == "Mercadona"
    assert stored["amount_paid_cents"] == 1411
    assert len(stored["items"]) == 4
    assert len(stored["validations"]) >= 4


def test_multi_image_receipt_has_one_identity_independent_of_page_order(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "carrefour" / "ticket.txt"
    page_1 = tmp_path / "carrefour-1.jpg"
    page_2 = tmp_path / "carrefour-2.jpg"
    page_1.write_bytes(b"fixture-page-one")
    page_2.write_bytes(b"fixture-page-two")
    text = fixture.read_text(encoding="utf-8")

    normal = process_receipt(text, [page_1, page_2])
    reversed_pages = process_receipt(text, [page_2, page_1])
    db_path = tmp_path / "multi-image.db"

    first = insert_receipt(db_path, normal)
    duplicate = insert_receipt(db_path, reversed_pages)
    stored = get_receipt(db_path, first.receipt_id)

    assert len(normal.receipt_images) == 2
    assert normal.content_hash == reversed_pages.content_hash
    assert first.inserted is True
    assert duplicate.inserted is False
    assert receipt_count(db_path) == 1
    assert len(stored["images"]) == 2
    assert stored["article_count"] == 48
    assert stored["printed_product_line_count"] == 45


def test_audit_payload_is_persisted(tmp_path: Path, mercadona_case):
    receipt, _ = mercadona_case
    db_path = tmp_path / "audit.db"
    result = insert_receipt(db_path, receipt)
    stored = get_receipt(db_path, result.receipt_id)

    assert stored["raw_ocr_text"].startswith("MERCADONA")
    assert stored["initial_extraction_json"]
    assert stored["normalized_result_json"]
    assert stored["validation_status"] == "valid"
    assert stored["purchase_date_source"] == "ocr"
    assert stored["purchase_date_needs_confirmation"] == 0
    assert stored["validations"]
    assert all(not Path(image["storage_ref"]).is_absolute() for image in stored["images"])


def test_taxonomy_and_adjustment_trace_are_persisted(tmp_path: Path, carrefour_confirmed_case):
    receipt, _ = carrefour_confirmed_case
    db_path = tmp_path / "taxonomy.db"
    result = insert_receipt(db_path, receipt)
    stored = get_receipt(db_path, result.receipt_id)

    with connection(db_path) as conn:
        egg = conn.execute(
            """
            SELECT p.stable_id AS sku_id,
                   cp.stable_id AS comparable_id, cp.name AS comparable_name,
                   pf.stable_id AS family_id, pf.name AS family_name,
                   c.name AS category_name
            FROM product_aliases pa
            JOIN products p ON p.id = pa.product_id
            JOIN comparable_products cp ON cp.id = p.comparable_product_id
            JOIN product_families pf ON pf.id = cp.product_family_id
            JOIN categories c ON c.id = pf.category_id
            WHERE pa.alias_text = 'HUEVO M CRF 24 UNID'
            """
        ).fetchone()

    assert egg["sku_id"].startswith("sku_")
    assert egg["comparable_id"].startswith("comparable_")
    assert egg["comparable_name"] == "Huevos"
    assert egg["family_id"].startswith("family_")
    assert egg["family_name"] == "Huevos"
    assert egg["category_name"] == "Lácteos y huevos"
    assert any(row["original_text"] == "ACUMULADO CLUB 8,76" for row in stored["adjustments"])


def test_inferred_date_is_excluded_from_analytics_until_confirmed(tmp_path: Path, carrefour_case):
    receipt, _ = carrefour_case
    db_path = tmp_path / "date-confirmation.db"
    inserted = insert_receipt(db_path, receipt)

    assert monthly_summary(db_path) == []
    confirmed = confirm_receipt_purchase_date(db_path, inserted.receipt_id, date(2026, 8, 20))
    stored = get_receipt(db_path, inserted.receipt_id)

    assert confirmed.status == "valid"
    assert confirmed.purchase_date_source == "user_confirmed"
    assert stored["validation_status"] == "valid"
    assert len(stored["corrections"]) == 1
    assert len(stored["revisions"]) == 2
    assert monthly_summary(db_path) == [
        {"month": "2026-08", "receipt_count": 1, "amount_paid_cents": 14709, "savings_cents": 487}
    ]
