from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .database import connection


class QueryValidationError(ValueError):
    pass


MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _month(filters: dict[str, Any]) -> str:
    value = str(filters.get("month", ""))
    if not MONTH_PATTERN.fullmatch(value):
        raise QueryValidationError("month must use YYYY-MM")
    return value


def _text(filters: dict[str, Any], key: str, *, max_length: int = 120) -> str:
    value = str(filters.get(key, "")).strip()
    if not value or len(value) > max_length:
        raise QueryValidationError(f"{key} is required and must be at most {max_length} characters")
    return value


def execute_query(db_path: str | Path, intent: str, filters: dict[str, Any] | None = None) -> dict:
    filters = filters or {}
    with connection(db_path) as conn:
        if intent == "month_total":
            month = _month(filters)
            row = conn.execute(
                """
                SELECT COUNT(*) AS receipt_count,
                       COALESCE(SUM(amount_paid_cents), 0) AS amount_paid_cents
                FROM receipts
                WHERE deleted_at IS NULL AND validation_status = 'valid'
                  AND substr(purchase_date, 1, 7) = ?
                """,
                (month,),
            ).fetchone()
            return {"intent": intent, "month": month, **dict(row)}

        if intent == "supermarket_total":
            supermarket = _text(filters, "supermarket")
            month = filters.get("month")
            params: list[Any] = [supermarket.casefold()]
            month_clause = ""
            if month is not None:
                month_value = _month(filters)
                month_clause = "AND substr(r.purchase_date, 1, 7) = ?"
                params.append(month_value)
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS receipt_count,
                       COALESCE(SUM(r.amount_paid_cents), 0) AS amount_paid_cents
                FROM receipts r
                JOIN supermarkets s ON s.id = r.supermarket_id
                WHERE r.deleted_at IS NULL AND r.validation_status = 'valid'
                  AND lower(s.chain) = ? {month_clause}
                """,
                params,
            ).fetchone()
            return {"intent": intent, "supermarket": supermarket, **dict(row)}

        if intent == "product_quantity":
            product = _text(filters, "product")
            rows = conn.execute(
                """
                SELECT cp.stable_id AS comparable_product_id,
                       cp.name AS comparable_product,
                       ri.normalized_unit,
                       SUM(CAST(ri.normalized_quantity AS REAL)) AS normalized_quantity,
                       SUM(CAST(ri.purchase_quantity AS REAL)) AS purchase_quantity
                FROM receipt_items ri
                JOIN receipts r ON r.id = ri.receipt_id
                JOIN products p ON p.id = ri.product_id
                JOIN comparable_products cp ON cp.id = p.comparable_product_id
                WHERE r.deleted_at IS NULL AND r.validation_status = 'valid'
                  AND (lower(cp.name) = ? OR cp.stable_id = ?)
                GROUP BY cp.id, ri.normalized_unit
                ORDER BY ri.normalized_unit
                """,
                (product.casefold(), product),
            )
            return {"intent": intent, "product": product, "rows": [dict(row) for row in rows]}

        if intent in {"price_history", "comparable_price_by_store"}:
            product = _text(filters, "product")
            order = "r.purchase_date, r.id" if intent == "price_history" else "normalized_price_cents, s.chain"
            rows = conn.execute(
                f"""
                SELECT cp.stable_id AS comparable_product_id,
                       cp.name AS comparable_product,
                       s.chain AS supermarket,
                       r.purchase_date,
                       ri.normalized_unit,
                       ri.normalized_quantity,
                       ri.line_final_cents,
                       ri.line_final_cents / CAST(ri.normalized_quantity AS REAL)
                           AS normalized_price_cents
                FROM receipt_items ri
                JOIN receipts r ON r.id = ri.receipt_id
                JOIN supermarkets s ON s.id = r.supermarket_id
                JOIN products p ON p.id = ri.product_id
                JOIN comparable_products cp ON cp.id = p.comparable_product_id
                WHERE r.deleted_at IS NULL AND r.validation_status = 'valid'
                  AND CAST(ri.normalized_quantity AS REAL) > 0
                  AND ri.normalized_unit IN ('kg', 'L', 'unit', 'egg')
                  AND (lower(cp.name) = ? OR cp.stable_id = ?)
                ORDER BY {order}
                """,
                (product.casefold(), product),
            )
            payload = [dict(row) for row in rows]
            if intent == "comparable_price_by_store":
                units = {row["normalized_unit"] for row in payload}
                if len(units) > 1:
                    return {
                        "intent": intent,
                        "product": product,
                        "comparable": False,
                        "reason": "normalized units are incompatible",
                        "rows": [],
                    }
            return {"intent": intent, "product": product, "comparable": True, "rows": payload}

        if intent == "comparable_store_summary":
            month_clause = ""
            params: list[Any] = []
            month = filters.get("month")
            if month is not None:
                month_value = _month(filters)
                month_clause = "AND substr(r.purchase_date, 1, 7) = ?"
                params.append(month_value)
            rows = conn.execute(
                f"""
                WITH store_product AS (
                    SELECT cp.id AS comparable_product_id,
                           cp.stable_id AS comparable_product_stable_id,
                           cp.name AS comparable_product,
                           ri.normalized_unit,
                           s.chain AS supermarket,
                           AVG(ri.line_final_cents / CAST(ri.normalized_quantity AS REAL))
                               AS normalized_price_cents
                    FROM receipt_items ri
                    JOIN receipts r ON r.id = ri.receipt_id
                    JOIN supermarkets s ON s.id = r.supermarket_id
                    JOIN products p ON p.id = ri.product_id
                    JOIN comparable_products cp ON cp.id = p.comparable_product_id
                    WHERE r.deleted_at IS NULL AND r.validation_status = 'valid'
                      AND CAST(ri.normalized_quantity AS REAL) > 0
                      AND ri.normalized_unit IN ('kg', 'L', 'unit', 'egg')
                      {month_clause}
                    GROUP BY cp.id, ri.normalized_unit, s.chain
                ), ranked AS (
                    SELECT *,
                           COUNT(*) OVER (
                               PARTITION BY comparable_product_id, normalized_unit
                           ) AS supermarket_count,
                           MIN(normalized_price_cents) OVER (
                               PARTITION BY comparable_product_id, normalized_unit
                           ) AS cheapest_price_cents
                    FROM store_product
                )
                SELECT supermarket,
                       COUNT(*) AS comparable_entries,
                       SUM(CASE WHEN ABS(normalized_price_cents - cheapest_price_cents) < 0.000001
                                THEN 1 ELSE 0 END) AS cheapest_entries,
                       ROUND(AVG(
                           CASE WHEN cheapest_price_cents > 0
                                THEN (normalized_price_cents / cheapest_price_cents - 1) * 100
                           END
                       ), 2) AS average_percent_over_cheapest
                FROM ranked
                WHERE supermarket_count >= 2
                GROUP BY supermarket
                ORDER BY cheapest_entries DESC, average_percent_over_cheapest, supermarket
                """,
                params,
            )
            result = [dict(row) for row in rows]
            return {
                "intent": intent,
                "month": month if month is not None else None,
                "comparison_level": "comparable_product",
                "requires_compatible_normalized_unit": True,
                "rows": result,
            }

        if intent == "discounts":
            month = _month(filters)
            rows = conn.execute(
                """
                SELECT s.chain AS supermarket,
                       SUM(r.immediate_discounts_cents) AS immediate_discounts_cents,
                       SUM(r.coupon_applied_cents) AS coupon_applied_cents
                FROM receipts r
                JOIN supermarkets s ON s.id = r.supermarket_id
                WHERE r.deleted_at IS NULL AND r.validation_status = 'valid'
                  AND substr(r.purchase_date, 1, 7) = ?
                GROUP BY s.chain
                ORDER BY s.chain
                """,
                (month,),
            )
            return {"intent": intent, "month": month, "rows": [dict(row) for row in rows]}

        if intent == "receipt_history":
            limit = int(filters.get("limit", 20))
            if not 1 <= limit <= 100:
                raise QueryValidationError("limit must be between 1 and 100")
            rows = conn.execute(
                """
                SELECT r.id, r.purchase_date, s.chain AS supermarket,
                       r.article_count, r.amount_paid_cents, r.validation_status
                FROM receipts r
                JOIN supermarkets s ON s.id = r.supermarket_id
                WHERE r.deleted_at IS NULL
                ORDER BY COALESCE(r.purchase_date, r.created_at) DESC, r.id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return {"intent": intent, "rows": [dict(row) for row in rows]}

    raise QueryValidationError(f"unsupported query intent: {intent}")
