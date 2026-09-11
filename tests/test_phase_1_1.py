from datetime import datetime, timezone
from pathlib import Path

from cesta_inteligente.database import connection, get_receipt, initialize_database, insert_receipt
from cesta_inteligente.models import ReceiptStatus
from cesta_inteligente.pipeline import process_receipt
from cesta_inteligente.taxonomy import classify_product


def test_real_ingestion_confidence_defaults_to_unknown():
    ticket = Path(__file__).parent / "fixtures" / "mercadona" / "ticket.txt"
    receipt = process_receipt(ticket.read_text(encoding="utf-8"), [ticket])

    assert receipt.extraction_confidence is None
    assert all(item.extraction_confidence is None for item in receipt.items)


def test_unknown_confidence_round_trips_through_sqlite(tmp_path: Path):
    ticket = Path(__file__).parent / "fixtures" / "mercadona" / "ticket.txt"
    receipt = process_receipt(ticket.read_text(encoding="utf-8"), [ticket])
    db_path = tmp_path / "unknown-confidence.db"
    inserted = insert_receipt(db_path, receipt)
    stored = get_receipt(db_path, inserted.receipt_id)

    assert stored["extraction_confidence"] is None
    assert all(item["extraction_confidence"] is None for item in stored["items"])


def test_exported_storage_references_are_not_absolute(carrefour_case):
    receipt, _ = carrefour_case
    payload = receipt.model_dump(mode="json")

    for image in payload["receipt_images"]:
        assert image["storage_id"].startswith("sha256:")
        assert not Path(image["storage_ref"]).is_absolute()
        assert "/Users/" not in image["storage_ref"]
    assert "/Users/" not in receipt.model_dump_json()


def test_product_comparable_and_family_ids_are_stable_across_supermarkets():
    carrefour = classify_product("Carrefour", "HUEVO M CRF 24 UNID")
    carrefour_again = classify_product("Carrefour", "HUEVO M CRF 24 UNID")
    mercadona = classify_product("Mercadona", "HUEVOS M 12U")

    assert carrefour.product_stable_id == carrefour_again.product_stable_id
    assert carrefour.product_stable_id != mercadona.product_stable_id
    assert carrefour.comparable_name == mercadona.comparable_name == "Huevos"
    assert carrefour.comparable_stable_id == mercadona.comparable_stable_id
    assert carrefour.family_name == mercadona.family_name == "Huevos"
    assert carrefour.family_stable_id == mercadona.family_stable_id
    assert carrefour.category_stable_id == mercadona.category_stable_id


def test_missing_date_without_reception_timestamp_is_not_valid():
    ticket = Path(__file__).parent / "fixtures" / "carrefour" / "ticket.txt"
    receipt = process_receipt(ticket.read_text(encoding="utf-8"), [ticket])

    assert receipt.purchase_date is None
    assert receipt.purchase_date_source == "unknown"
    assert receipt.status == ReceiptStatus.NEEDS_REVIEW
    assert receipt.pending_questions == ["No aparece la fecha del ticket. ¿Qué fecha de compra corresponde?"]


def test_inferred_date_uses_authenticated_reception_day_but_still_needs_confirmation():
    ticket = Path(__file__).parent / "fixtures" / "carrefour" / "ticket.txt"
    receipt = process_receipt(
        ticket.read_text(encoding="utf-8"),
        [ticket],
        received_at=datetime(2026, 8, 20, 22, 15, tzinfo=timezone.utc),
    )

    assert receipt.purchase_date.isoformat() == "2026-08-21"
    assert receipt.purchase_date_source == "inferred"
    assert receipt.status == ReceiptStatus.NEEDS_REVIEW


def test_schema_v4_preserves_sku_taxonomy_and_adds_integration_state(tmp_path: Path):
    db_path = tmp_path / "schema-v4.db"
    initialize_database(db_path)
    with connection(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        product_columns = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
        comparable_columns = {row[1] for row in conn.execute("PRAGMA table_info(comparable_products)")}

    assert version == 5
    assert {"products", "comparable_products", "product_families", "categories"}.issubset(tables)
    assert {"user_channel_identities", "media_objects", "receipt_batches", "receipt_batch_images", "receipt_reviews"}.issubset(tables)
    assert "comparable_product_id" in product_columns
    assert "product_family_id" not in product_columns
    assert "product_family_id" in comparable_columns
    assert "canonical_products" not in tables
