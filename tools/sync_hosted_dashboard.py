#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import certifi
except ImportError:  # pragma: no cover - disponible en el entorno de producción
    certifi = None


def build_snapshot(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        receipts = conn.execute(
            """
            SELECT r.id, r.purchase_date AS date, s.name AS supermarket,
                   COALESCE(s.store_name, '') AS store, r.article_count,
                   r.amount_paid_cents,
                   r.immediate_discounts_cents + r.coupon_applied_cents AS savings_cents,
                   r.validation_status
            FROM receipts r
            JOIN supermarkets s ON s.id = r.supermarket_id
            WHERE r.deleted_at IS NULL AND r.purchase_date IS NOT NULL
            ORDER BY r.purchase_date, r.id
            """
        ).fetchall()
        items = conn.execute(
            """
            SELECT ri.id, ri.receipt_id, p.stable_id AS product_stable_id,
                   p.name AS product, cp.stable_id AS comparable_product_stable_id,
                   cp.name AS comparable_product, pf.name AS family, c.name AS category,
                   r.purchase_date AS date, s.name AS supermarket,
                   CAST(ri.purchase_quantity AS REAL) AS quantity,
                   CAST(ri.normalized_quantity AS REAL) AS normalized_quantity,
                   COALESCE(ri.normalized_unit, 'other') AS normalized_unit,
                   ri.line_final_cents,
                   CASE WHEN CAST(ri.normalized_quantity AS REAL) > 0
                        THEN ri.line_final_cents / CAST(ri.normalized_quantity AS REAL)
                   END AS normalized_price_cents
            FROM receipt_items ri
            JOIN receipts r ON r.id = ri.receipt_id
            JOIN supermarkets s ON s.id = r.supermarket_id
            JOIN products p ON p.id = ri.product_id
            JOIN comparable_products cp ON cp.id = p.comparable_product_id
            JOIN product_families pf ON pf.id = cp.product_family_id
            JOIN categories c ON c.id = pf.category_id
            WHERE r.deleted_at IS NULL AND r.purchase_date IS NOT NULL
            ORDER BY r.purchase_date, ri.id
            """
        ).fetchall()
    finally:
        conn.close()

    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(ZoneInfo("Europe/Madrid")).isoformat(timespec="seconds"),
        "receipts": [
            {
                "id": row["id"],
                "date": row["date"],
                "supermarket": row["supermarket"],
                "store": row["store"],
                "articleCount": row["article_count"],
                "amountPaidCents": row["amount_paid_cents"],
                "savingsCents": row["savings_cents"],
                "validationStatus": row["validation_status"],
            }
            for row in receipts
        ],
        "items": [
            {
                "id": row["id"],
                "receiptId": row["receipt_id"],
                "productStableId": row["product_stable_id"],
                "product": row["product"],
                "comparableProductStableId": row["comparable_product_stable_id"],
                "comparableProduct": row["comparable_product"],
                "family": row["family"],
                "category": row["category"],
                "date": row["date"],
                "supermarket": row["supermarket"],
                "quantity": row["quantity"],
                "normalizedQuantity": row["normalized_quantity"],
                "normalizedUnit": row["normalized_unit"],
                "lineFinalCents": row["line_final_cents"],
                "normalizedPriceCents": row["normalized_price_cents"],
            }
            for row in items
        ],
    }


def keychain_token(service: str, account: str) -> str:
    result = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def stable_hash(snapshot: dict) -> str:
    content = {"schemaVersion": snapshot["schemaVersion"], "receipts": snapshot["receipts"], "items": snapshot["items"]}
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def atomic_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        os.write(fd, value.encode("utf-8"))
        os.close(fd)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--keychain-service", default="com.example.cesta-hosted-sync")
    parser.add_argument("--keychain-account", default=getpass.getuser())
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    snapshot = build_snapshot(args.db)
    digest = stable_hash(snapshot)
    if not args.force and args.state.exists() and args.state.read_text(encoding="utf-8").strip() == digest:
        print("sin cambios")
        return 0

    token = keychain_token(args.keychain_service, args.keychain_account)
    body = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        args.url.rstrip("/") + "/api/sync",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "CestaSync/1.0"},
    )
    try:
        context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=25, context=context) as response:
            result = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"sync pendiente: {exc}", file=sys.stderr)
        return 1

    if not result.get("ok"):
        print("sync rechazada", file=sys.stderr)
        return 1
    atomic_write(args.state, digest + "\n")
    print(f"sincronizado: {result.get('receipts', 0)} tickets, {result.get('items', 0)} artículos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
