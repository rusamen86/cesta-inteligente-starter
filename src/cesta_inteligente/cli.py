from __future__ import annotations

import argparse
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .database import confirm_receipt_purchase_date, get_receipt, insert_receipt
from .database import reclassify_existing_products
from .pipeline import process_receipt


def _process(args) -> int:
    text_path = Path(args.text)
    receipt = process_receipt(
        text_path.read_text(encoding="utf-8"),
        [Path(path) for path in args.source],
        received_at=datetime.fromisoformat(args.received_at) if args.received_at else None,
        confirmed_purchase_date=date.fromisoformat(args.confirmed_date) if args.confirmed_date else None,
        extraction_confidence=Decimal(args.confidence) if args.confidence else None,
    )
    output = receipt.model_dump_json(indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


def _demo(args) -> int:
    root = Path.cwd()
    text_path = root / "tests" / "fixtures" / "mercadona" / "ticket.txt"
    receipt = process_receipt(text_path.read_text(encoding="utf-8"), [text_path], extraction_confidence=Decimal("1"))
    first = insert_receipt(args.db, receipt)
    second = insert_receipt(args.db, receipt)
    stored = get_receipt(args.db, first.receipt_id)
    print(
        {
            "first_inserted": first.inserted,
            "second_inserted": second.inserted,
            "receipt_id": first.receipt_id,
            "supermarket": stored["supermarket"],
            "amount_paid_cents": stored["amount_paid_cents"],
            "items": len(stored["items"]),
        }
    )
    return 0


def _confirm_date(args) -> int:
    receipt = confirm_receipt_purchase_date(args.db, args.receipt_id, date.fromisoformat(args.date))
    print(
        {
            "receipt_id": args.receipt_id,
            "purchase_date": receipt.purchase_date.isoformat(),
            "purchase_date_source": receipt.purchase_date_source,
            "status": receipt.status,
        }
    )
    return 0


def _reclassify(args) -> int:
    print(reclassify_existing_products(args.db))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="cesta")
    subparsers = parser.add_subparsers(required=True)

    process_parser = subparsers.add_parser("process", help="Process OCR text into audited JSON")
    process_parser.add_argument("--text", required=True)
    process_parser.add_argument("--source", action="append", required=True)
    process_parser.add_argument("--output")
    process_parser.add_argument("--received-at", help="Authenticated channel reception timestamp (ISO 8601)")
    process_parser.add_argument("--confirmed-date", help="User-confirmed purchase date (YYYY-MM-DD)")
    process_parser.add_argument("--confidence", help="Extractor confidence; omit when unknown")
    process_parser.set_defaults(func=_process)

    demo_parser = subparsers.add_parser("demo", help="Insert and query a fixture twice")
    demo_parser.add_argument("--db", default="data/demo-cesta.db")
    demo_parser.set_defaults(func=_demo)

    confirm_parser = subparsers.add_parser("confirm-date", help="Confirm the purchase date of a reviewed receipt")
    confirm_parser.add_argument("--db", default="data/cesta.db")
    confirm_parser.add_argument("--receipt-id", type=int, required=True)
    confirm_parser.add_argument("--date", required=True, help="Confirmed date (YYYY-MM-DD)")
    confirm_parser.set_defaults(func=_confirm_date)

    reclassify_parser = subparsers.add_parser("reclassify", help="Apply the current taxonomy to stored products")
    reclassify_parser.add_argument("--db", default="data/cesta.db")
    reclassify_parser.set_defaults(func=_reclassify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
