from __future__ import annotations

from pathlib import Path

from .database import connection


def monthly_summary(db_path: str | Path) -> list[dict]:
    with connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT substr(purchase_date, 1, 7) AS month,
                   COUNT(*) AS receipt_count,
                   SUM(amount_paid_cents) AS amount_paid_cents,
                   SUM(immediate_discounts_cents + coupon_applied_cents) AS savings_cents
            FROM receipts
            WHERE deleted_at IS NULL AND validation_status = 'valid' AND purchase_date IS NOT NULL
            GROUP BY substr(purchase_date, 1, 7)
            ORDER BY month
            """
        )
        return [dict(row) for row in rows]


def spending_by_supermarket(db_path: str | Path) -> list[dict]:
    with connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.name AS supermarket, s.store_name,
                   COUNT(*) AS receipt_count,
                   SUM(r.amount_paid_cents) AS amount_paid_cents
            FROM receipts r
            JOIN supermarkets s ON s.id = r.supermarket_id
            WHERE r.deleted_at IS NULL AND r.validation_status = 'valid'
            GROUP BY s.id
            ORDER BY amount_paid_cents DESC
            """
        )
        return [dict(row) for row in rows]


def product_history(db_path: str | Path) -> list[dict]:
    with connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT p.stable_id AS product_stable_id, p.name AS product,
                   cp.stable_id AS comparable_product_stable_id,
                   cp.name AS comparable_product,
                   pf.stable_id AS product_family_stable_id, pf.name AS product_family,
                   c.stable_id AS category_stable_id, c.name AS category,
                   r.purchase_date, s.name AS supermarket,
                   ri.purchase_quantity, ri.normalized_quantity, ri.normalized_unit,
                   ri.line_final_cents,
                   CASE
                     WHEN CAST(ri.normalized_quantity AS REAL) > 0
                     THEN ri.line_final_cents / CAST(ri.normalized_quantity AS REAL)
                   END AS normalized_price_cents
            FROM receipt_items ri
            JOIN receipts r ON r.id = ri.receipt_id
            JOIN supermarkets s ON s.id = r.supermarket_id
            JOIN products p ON p.id = ri.product_id
            JOIN comparable_products cp ON cp.id = p.comparable_product_id
            JOIN product_families pf ON pf.id = cp.product_family_id
            JOIN categories c ON c.id = pf.category_id
            WHERE r.deleted_at IS NULL AND r.validation_status = 'valid'
            ORDER BY r.purchase_date, ri.id
            """
        )
        return [dict(row) for row in rows]


def comparable_price_comparisons(db_path: str | Path) -> list[dict]:
    """Return cross-supermarket observations comparable in both concept and unit."""
    with connection(db_path) as conn:
        rows = conn.execute(
            """
            WITH observations AS (
                SELECT cp.stable_id AS comparable_product_stable_id,
                       cp.name AS comparable_product,
                       pf.name AS product_family,
                       c.name AS category,
                       ri.normalized_unit,
                       r.purchase_date,
                       s.id AS supermarket_id,
                       s.name AS supermarket,
                       p.name AS sku,
                       ri.normalized_quantity,
                       ri.line_final_cents,
                       ri.line_final_cents / CAST(ri.normalized_quantity AS REAL)
                           AS normalized_price_cents
                FROM receipt_items ri
                JOIN receipts r ON r.id = ri.receipt_id
                JOIN supermarkets s ON s.id = r.supermarket_id
                JOIN products p ON p.id = ri.product_id
                JOIN comparable_products cp ON cp.id = p.comparable_product_id
                JOIN product_families pf ON pf.id = cp.product_family_id
                JOIN categories c ON c.id = pf.category_id
                WHERE r.deleted_at IS NULL
                  AND r.validation_status = 'valid'
                  AND CAST(ri.normalized_quantity AS REAL) > 0
                  AND ri.normalized_unit IN ('kg', 'L', 'unit', 'egg')
            )
            SELECT o.*
            FROM observations o
            WHERE EXISTS (
                SELECT 1
                FROM observations other
                WHERE other.comparable_product_stable_id = o.comparable_product_stable_id
                  AND other.normalized_unit = o.normalized_unit
                  AND other.supermarket_id <> o.supermarket_id
            )
            ORDER BY o.comparable_product, o.normalized_unit, o.purchase_date, o.supermarket
            """
        )
        return [dict(row) for row in rows]


def receipt_history(db_path: str | Path) -> list[dict]:
    with connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.purchase_date, s.name AS supermarket, s.store_name,
                   r.article_count, r.printed_product_line_count,
                   r.amount_paid_cents,
                   r.immediate_discounts_cents + r.coupon_applied_cents AS savings_cents,
                   r.reward_generated_cents, r.validation_status, r.created_at
            FROM receipts r
            JOIN supermarkets s ON s.id = r.supermarket_id
            WHERE r.deleted_at IS NULL
            ORDER BY COALESCE(r.purchase_date, r.created_at) DESC
            """
        )
        return [dict(row) for row in rows]
