from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from importlib.resources import files
from datetime import date
from pathlib import Path
from typing import Iterator

from .models import AuditableReceipt, IngestResult, PurchaseDateSource
from .taxonomy import classify_product
from .validate import validate_receipt


def cents(value: Decimal | None) -> int | None:
    if value is None:
        return None
    return int((value * 100).quantize(Decimal("1")))


@contextmanager
def connection(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(db_path: str | Path) -> None:
    path = Path(db_path)
    if path.exists():
        with sqlite3.connect(path) as probe:
            has_version_table = probe.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
            ).fetchone()
            if has_version_table:
                version = probe.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
                if version is not None and version < 3:
                    raise RuntimeError(
                        "Pre-production schema v1/v2 detected. Use a fresh v3 database; "
                        "the previous acceptance databases contain fixtures only."
                    )
    schema = files("cesta_inteligente").joinpath("schema.sql").read_text(encoding="utf-8")
    with connection(db_path) as conn:
        conn.executescript(schema)
        conn.execute("INSERT OR IGNORE INTO households(id, name) VALUES (1, 'Hogar principal')")


def reclassify_existing_products(db_path: str | Path) -> dict[str, int]:
    """Apply the current deterministic taxonomy to already stored product aliases."""
    with connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT pa.id AS alias_id, pa.alias_text, pa.product_code, pa.product_id,
                   s.name AS supermarket
            FROM product_aliases pa
            JOIN supermarkets s ON s.id = pa.supermarket_id
            ORDER BY pa.product_id, pa.id
            """
        ).fetchall()
        classifications: dict[int, object] = {}
        for row in rows:
            taxonomy = classify_product(row["supermarket"], row["alias_text"], row["product_code"])
            product_id = int(row["product_id"])
            previous = classifications.get(product_id)
            if previous is not None and (
                previous.family_stable_id != taxonomy.family_stable_id
                or previous.category_stable_id != taxonomy.category_stable_id
            ):
                raise RuntimeError(f"conflicting taxonomy aliases for product {product_id}")
            if previous is None:
                classifications[product_id] = taxonomy

        changed = 0
        for product_id, taxonomy in classifications.items():
            conn.execute(
                "INSERT OR IGNORE INTO categories(household_id, stable_id, name) VALUES (1, ?, ?)",
                (taxonomy.category_stable_id, taxonomy.category_name),
            )
            category_id = int(
                conn.execute("SELECT id FROM categories WHERE stable_id = ?", (taxonomy.category_stable_id,)).fetchone()["id"]
            )
            conn.execute(
                "INSERT OR IGNORE INTO product_families(household_id, stable_id, category_id, name) VALUES (1, ?, ?, ?)",
                (taxonomy.family_stable_id, category_id, taxonomy.family_name),
            )
            family_id = int(
                conn.execute("SELECT id FROM product_families WHERE stable_id = ?", (taxonomy.family_stable_id,)).fetchone()["id"]
            )
            conn.execute(
                """
                INSERT INTO comparable_products(household_id, stable_id, product_family_id, name)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(stable_id) DO UPDATE SET
                    product_family_id = excluded.product_family_id,
                    name = excluded.name
                """,
                (taxonomy.comparable_stable_id, family_id, taxonomy.comparable_name),
            )
            comparable_id = int(
                conn.execute(
                    "SELECT id FROM comparable_products WHERE stable_id = ?",
                    (taxonomy.comparable_stable_id,),
                ).fetchone()["id"]
            )
            cursor = conn.execute(
                "UPDATE products SET comparable_product_id = ? WHERE id = ? AND comparable_product_id <> ?",
                (comparable_id, product_id, comparable_id),
            )
            changed += cursor.rowcount

        unclassified = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM receipt_items ri
                JOIN products p ON p.id = ri.product_id
                JOIN comparable_products cp ON cp.id = p.comparable_product_id
                JOIN product_families pf ON pf.id = cp.product_family_id
                JOIN categories c ON c.id = pf.category_id
                WHERE c.name = 'Sin clasificar'
                """
            ).fetchone()[0]
        )
        return {"products_seen": len(classifications), "products_changed": changed, "unclassified_items": unclassified}


def _supermarket_id(conn: sqlite3.Connection, receipt: AuditableReceipt) -> int:
    row = conn.execute(
        "SELECT id FROM supermarkets WHERE chain = ? AND store_name IS ?",
        (receipt.supermarket, receipt.store_name),
    ).fetchone()
    if row:
        return int(row["id"])
    conn.execute(
        "INSERT OR IGNORE INTO supermarkets(name, chain, store_name) VALUES (?, ?, ?)",
        (receipt.supermarket, receipt.supermarket, receipt.store_name),
    )
    row = conn.execute(
        "SELECT id FROM supermarkets WHERE chain = ? AND store_name IS ?",
        (receipt.supermarket, receipt.store_name),
    ).fetchone()
    return int(row["id"])


def _product_id(conn: sqlite3.Connection, item) -> int:
    base_unit = item.normalized_unit or "other"
    conn.execute(
        "INSERT OR IGNORE INTO categories(household_id, stable_id, name) VALUES (1, ?, ?)",
        (item.category_stable_id, item.category_name),
    )
    category_id = int(conn.execute("SELECT id FROM categories WHERE stable_id = ?", (item.category_stable_id,)).fetchone()["id"])
    conn.execute(
        "INSERT OR IGNORE INTO product_families(household_id, stable_id, category_id, name) VALUES (1, ?, ?, ?)",
        (item.product_family_stable_id, category_id, item.product_family_name),
    )
    family_id = int(conn.execute("SELECT id FROM product_families WHERE stable_id = ?", (item.product_family_stable_id,)).fetchone()["id"])
    conn.execute(
        "INSERT OR IGNORE INTO comparable_products(household_id, stable_id, product_family_id, name) VALUES (1, ?, ?, ?)",
        (item.comparable_product_stable_id, family_id, item.comparable_product_name),
    )
    comparable_id = int(conn.execute(
        "SELECT id FROM comparable_products WHERE stable_id = ?",
        (item.comparable_product_stable_id,),
    ).fetchone()["id"])
    conn.execute(
        "INSERT OR IGNORE INTO products(household_id, stable_id, comparable_product_id, name, base_unit) VALUES (1, ?, ?, ?, ?)",
        (item.product_stable_id, comparable_id, item.product_name, base_unit),
    )
    return int(conn.execute("SELECT id FROM products WHERE stable_id = ?", (item.product_stable_id,)).fetchone()["id"])


def insert_receipt(db_path: str | Path, receipt: AuditableReceipt, created_by: int | None = None) -> IngestResult:
    initialize_database(db_path)
    with connection(db_path) as conn:
        existing = conn.execute(
            "SELECT id, validation_status FROM receipts WHERE content_hash = ? AND deleted_at IS NULL",
            (receipt.content_hash,),
        ).fetchone()
        if existing:
            return IngestResult(
                receipt_id=int(existing["id"]),
                inserted=False,
                content_hash=receipt.content_hash,
                status=existing["validation_status"],
            )

        supermarket_id = _supermarket_id(conn, receipt)
        cursor = conn.execute(
            """
            INSERT INTO receipts(
                household_id, supermarket_id, content_hash, purchase_date,
                purchase_date_source, purchase_date_needs_confirmation,
                article_count, printed_product_line_count, subtotal_gross_cents,
                immediate_discounts_cents, coupon_applied_cents, amount_paid_cents,
                reward_generated_cents, currency, raw_ocr_text,
                initial_extraction_json, normalized_result_json,
                extraction_confidence, validation_status, created_by
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                supermarket_id,
                receipt.content_hash,
                receipt.purchase_date.isoformat() if receipt.purchase_date else None,
                receipt.purchase_date_source,
                int(receipt.purchase_date_needs_confirmation),
                receipt.article_count,
                receipt.printed_product_line_count,
                cents(receipt.subtotal_gross),
                cents(receipt.immediate_discounts),
                cents(receipt.coupon_applied),
                cents(receipt.amount_paid),
                cents(receipt.reward_generated),
                receipt.currency,
                receipt.raw_ocr_text,
                json.dumps(receipt.initial_extraction, ensure_ascii=False),
                receipt.model_dump_json(),
                None if receipt.extraction_confidence is None else str(receipt.extraction_confidence),
                receipt.status,
                created_by,
            ),
        )
        receipt_id = int(cursor.lastrowid)

        for image in receipt.receipt_images:
            conn.execute(
                "INSERT INTO receipt_images(receipt_id, ordinal, sha256, storage_id, storage_ref, mime_type) VALUES (?, ?, ?, ?, ?, ?)",
                (receipt_id, image.ordinal, image.sha256, image.storage_id, image.storage_ref, image.mime_type),
            )

        item_ids_by_alias: dict[str, int] = {}
        for item in receipt.items:
            product_id = _product_id(conn, item)
            conn.execute(
                """
                INSERT OR IGNORE INTO product_aliases(
                    household_id, supermarket_id, product_id, alias_text,
                    product_code, confidence
                ) VALUES (1, ?, ?, ?, ?, ?)
                """,
                (
                    supermarket_id,
                    product_id,
                    item.alias_text,
                    item.product_code,
                    None if item.extraction_confidence is None else str(item.extraction_confidence),
                ),
            )
            item_cursor = conn.execute(
                """
                INSERT INTO receipt_items(
                    receipt_id, original_text, product_code, product_id,
                    purchase_quantity, purchase_unit, pack_count,
                    content_per_unit_value, content_per_unit_unit,
                    normalized_quantity, normalized_unit, unit_price_cents,
                    line_gross_cents, line_discount_cents, line_final_cents,
                    extraction_confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    item.original_text,
                    item.product_code,
                    product_id,
                    str(item.purchase_quantity),
                    item.purchase_unit,
                    item.pack_count,
                    str(item.content_per_unit_value) if item.content_per_unit_value is not None else None,
                    item.content_per_unit_unit,
                    str(item.normalized_quantity) if item.normalized_quantity is not None else None,
                    item.normalized_unit,
                    cents(item.unit_price),
                    cents(item.line_gross),
                    cents(item.line_discount),
                    cents(item.line_final),
                    None if item.extraction_confidence is None else str(item.extraction_confidence),
                ),
            )
            item_ids_by_alias[item.alias_text] = int(item_cursor.lastrowid)

        for adjustment in receipt.adjustments:
            conn.execute(
                """
                INSERT INTO receipt_adjustments(
                    receipt_id, receipt_item_id, type, description, original_text,
                    code, amount_cents, affects_amount_paid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    item_ids_by_alias.get(adjustment.linked_alias or ""),
                    adjustment.type,
                    adjustment.description,
                    adjustment.original_text,
                    adjustment.code,
                    cents(adjustment.amount),
                    int(adjustment.affects_amount_paid),
                ),
            )

        for check in receipt.validations:
            conn.execute(
                """
                INSERT INTO receipt_validations(
                    receipt_id, name, passed, expected, actual, tolerance, message
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    check.name,
                    int(check.passed),
                    None if check.expected is None else str(check.expected),
                    None if check.actual is None else str(check.actual),
                    None if check.tolerance is None else str(check.tolerance),
                    check.message,
                ),
            )

        conn.execute(
            "INSERT INTO receipt_revisions(receipt_id, revision_no, event_type, snapshot_json, changed_by) VALUES (?, 1, 'ingested', ?, ?)",
            (receipt_id, receipt.model_dump_json(), created_by),
        )

        return IngestResult(
            receipt_id=receipt_id,
            inserted=True,
            content_hash=receipt.content_hash,
            status=receipt.status,
        )


def get_receipt(db_path: str | Path, receipt_id: int) -> dict:
    with connection(db_path) as conn:
        receipt = conn.execute(
            """
            SELECT r.*, s.name AS supermarket, s.store_name
            FROM receipts r JOIN supermarkets s ON s.id = r.supermarket_id
            WHERE r.id = ? AND r.deleted_at IS NULL
            """,
            (receipt_id,),
        ).fetchone()
        if receipt is None:
            raise KeyError(f"Receipt {receipt_id} not found")
        result = dict(receipt)
        result["images"] = [dict(row) for row in conn.execute("SELECT * FROM receipt_images WHERE receipt_id = ? ORDER BY ordinal", (receipt_id,))]
        result["items"] = [dict(row) for row in conn.execute("SELECT * FROM receipt_items WHERE receipt_id = ? ORDER BY id", (receipt_id,))]
        result["validations"] = [dict(row) for row in conn.execute("SELECT * FROM receipt_validations WHERE receipt_id = ? ORDER BY id", (receipt_id,))]
        result["adjustments"] = [dict(row) for row in conn.execute("SELECT * FROM receipt_adjustments WHERE receipt_id = ? ORDER BY id", (receipt_id,))]
        result["corrections"] = [dict(row) for row in conn.execute("SELECT * FROM receipt_corrections WHERE receipt_id = ? ORDER BY id", (receipt_id,))]
        result["revisions"] = [dict(row) for row in conn.execute("SELECT * FROM receipt_revisions WHERE receipt_id = ? ORDER BY revision_no", (receipt_id,))]
        return result


def confirm_receipt_purchase_date(
    db_path: str | Path,
    receipt_id: int,
    confirmed_date: date,
    *,
    corrected_by: int | None = None,
) -> AuditableReceipt:
    initialize_database(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT normalized_result_json FROM receipts WHERE id = ? AND deleted_at IS NULL",
            (receipt_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Receipt {receipt_id} not found")
        receipt = AuditableReceipt.model_validate_json(row["normalized_result_json"])
        old_value = {
            "purchase_date": receipt.purchase_date.isoformat() if receipt.purchase_date else None,
            "purchase_date_source": receipt.purchase_date_source,
            "status": receipt.status,
        }
        receipt.purchase_date = confirmed_date
        receipt.purchase_date_source = PurchaseDateSource.USER_CONFIRMED
        receipt.purchase_date_needs_confirmation = False
        receipt.pending_questions = []
        receipt.validations, receipt.status = validate_receipt(receipt)
        new_value = {
            "purchase_date": confirmed_date.isoformat(),
            "purchase_date_source": receipt.purchase_date_source,
            "status": receipt.status,
        }

        conn.execute(
            """
            UPDATE receipts
            SET purchase_date = ?, purchase_date_source = 'user_confirmed',
                purchase_date_needs_confirmation = 0,
                normalized_result_json = ?, validation_status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (confirmed_date.isoformat(), receipt.model_dump_json(), receipt.status, receipt_id),
        )
        conn.execute(
            """
            INSERT INTO receipt_corrections(
                receipt_id, corrected_by, field_path, old_value_json,
                new_value_json, reason
            ) VALUES (?, ?, '/purchase_date', ?, ?, 'user_confirmation')
            """,
            (
                receipt_id,
                corrected_by,
                json.dumps(old_value, ensure_ascii=False, default=str),
                json.dumps(new_value, ensure_ascii=False, default=str),
            ),
        )
        conn.execute("DELETE FROM receipt_validations WHERE receipt_id = ?", (receipt_id,))
        for check in receipt.validations:
            conn.execute(
                """
                INSERT INTO receipt_validations(
                    receipt_id, name, passed, expected, actual, tolerance, message
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    check.name,
                    int(check.passed),
                    None if check.expected is None else str(check.expected),
                    None if check.actual is None else str(check.actual),
                    None if check.tolerance is None else str(check.tolerance),
                    check.message,
                ),
            )
        revision_no = int(
            conn.execute(
                "SELECT COALESCE(MAX(revision_no), 0) + 1 FROM receipt_revisions WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()[0]
        )
        conn.execute(
            "INSERT INTO receipt_revisions(receipt_id, revision_no, event_type, snapshot_json, changed_by) VALUES (?, ?, 'purchase_date_confirmed', ?, ?)",
            (receipt_id, revision_no, receipt.model_dump_json(), corrected_by),
        )
        return receipt


def receipt_count(db_path: str | Path) -> int:
    with connection(db_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM receipts WHERE deleted_at IS NULL").fetchone()[0])
