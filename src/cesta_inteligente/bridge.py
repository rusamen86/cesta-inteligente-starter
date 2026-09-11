from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from .batching import assess_receipt_completeness
from .database import (
    confirm_receipt_purchase_date,
    connection,
    get_receipt,
    initialize_database,
    insert_receipt,
)
from .media_store import MediaStore
from .ocr import OCREngine
from .ocr_layout import merge_ocr_pages
from .permissions import PermissionService, TrustedRequestContext
from .pipeline import process_receipt
from .query_api import execute_query


YES_ANSWERS = {"sí", "si", "s", "yes", "confirmo", "correcto"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _confidence_average(values: list[str | None]) -> Decimal | None:
    known = [Decimal(value) for value in values if value is not None]
    if not known:
        return None
    return sum(known, Decimal("0")) / Decimal(len(known))


class CestaBridge:
    """Narrow application facade used by the trusted OpenClaw plugin."""

    def __init__(
        self,
        *,
        db_path: str | Path,
        media_store: MediaStore,
        ocr_engine: OCREngine,
        permissions: PermissionService,
        dashboard_url: str | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.db_path = Path(db_path)
        self.media_store = media_store
        self.ocr_engine = ocr_engine
        self.permissions = permissions
        self.dashboard_url = dashboard_url
        self.clock = clock
        initialize_database(self.db_path)

    def _open_batch(self, group_jid: str, sender_identity: str):
        with connection(self.db_path) as conn:
            return conn.execute(
                """
                SELECT * FROM receipt_batches
                WHERE group_jid = ? AND sender_identity = ? AND status = 'open'
                """,
                (group_jid, sender_identity),
            ).fetchone()

    def _create_batch(self, context: TrustedRequestContext, user_id: int) -> str:
        batch_id = f"batch_{uuid.uuid4().hex}"
        now = context.received_at
        with connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO receipt_batches(
                    id, household_id, group_jid, sender_identity, created_by,
                    status, closure_mode, opened_at, last_image_at,
                    close_after_at, received_at
                ) VALUES (?, 1, ?, ?, ?, 'open', 'long', ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    context.group_jid,
                    context.requester_sender_id,
                    user_id,
                    _iso(now),
                    _iso(now),
                    _iso(now),
                    _iso(now),
                ),
            )
        return batch_id

    def _batch_payload(self, batch_id: str) -> tuple[dict, list[dict]]:
        with connection(self.db_path) as conn:
            batch = conn.execute("SELECT * FROM receipt_batches WHERE id = ?", (batch_id,)).fetchone()
            if batch is None:
                raise KeyError(f"unknown batch: {batch_id}")
            images = conn.execute(
                "SELECT * FROM receipt_batch_images WHERE batch_id = ? ORDER BY ordinal",
                (batch_id,),
            ).fetchall()
        return dict(batch), [dict(row) for row in images]

    def _store_batch_result(self, batch_id: str, payload: dict) -> dict:
        with connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO receipt_batch_results(batch_id, payload_json)
                VALUES (?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET payload_json = excluded.payload_json
                """,
                (batch_id, json.dumps(payload, ensure_ascii=False)),
            )
        return payload

    def ingest(
        self,
        context: TrustedRequestContext,
        *,
        media_ids: list[str] | None = None,
        action: str = "append",
        correlated_batch_id: str | None = None,
    ) -> dict:
        principal = self.permissions.authorize(context)
        if action not in {"append", "finalize", "status"}:
            raise ValueError("unsupported ingest action")
        media_ids = media_ids or []
        if action == "append" and not media_ids:
            raise ValueError("append requires at least one media id")
        if len(media_ids) > 12:
            raise ValueError("a batch accepts at most 12 media objects per call")

        # Validate and extract every object before opening a persistent batch. A bad opaque ID or
        # OCR failure must not leave an empty due batch behind for the poller to trip over.
        staged_media = []
        if action == "append":
            for storage_id in dict.fromkeys(media_ids):
                media = self.media_store.resolve(storage_id)
                staged_media.append((media, self.ocr_engine.extract(media)))

        open_batch = self._open_batch(context.group_jid, context.requester_sender_id)
        if correlated_batch_id is not None and action in {"status", "finalize"}:
            with connection(self.db_path) as conn:
                open_batch = conn.execute(
                    """
                    SELECT * FROM receipt_batches
                    WHERE id = ? AND group_jid = ? AND sender_identity = ?
                    """,
                    (correlated_batch_id, context.group_jid, context.requester_sender_id),
                ).fetchone()
        if open_batch is None:
            if action == "status":
                return {"status": "no_open_batch"}
            if action != "append":
                return {"status": "no_open_batch"}
            batch_id = self._create_batch(context, principal.user_id)
        else:
            batch_id = str(open_batch["id"])

        if action == "status":
            batch, images = self._batch_payload(batch_id)
            if batch["status"] != "open":
                with connection(self.db_path) as conn:
                    stored_result = conn.execute(
                        "SELECT payload_json FROM receipt_batch_results WHERE batch_id = ?",
                        (batch_id,),
                    ).fetchone()
                if stored_result is not None:
                    return json.loads(stored_result["payload_json"])
            return {"batch_id": batch_id, "status": batch["status"], "image_count": len(images)}
        if action == "finalize":
            return self._finalize_batch(batch_id, expected_sender=context.requester_sender_id, explicit=True)

        for media, ocr in staged_media:
            with connection(self.db_path) as conn:
                ordinal = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM receipt_batch_images WHERE batch_id = ?",
                        (batch_id,),
                    ).fetchone()[0]
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO receipt_batch_images(
                        batch_id, ordinal, storage_id, ocr_text, ocr_confidence
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        ordinal,
                        media.storage_id,
                        ocr.text,
                        None if ocr.confidence is None else str(ocr.confidence),
                    ),
                )

        batch, images = self._batch_payload(batch_id)
        combined_text = merge_ocr_pages([image["ocr_text"] for image in images])
        media = [self.media_store.resolve(image["storage_id"]) for image in images]
        confidence = _confidence_average([image["ocr_confidence"] for image in images])
        now = self.clock()
        decision = assess_receipt_completeness(
            combined_text,
            [item.path for item in media],
            received_at=datetime.fromisoformat(batch["received_at"]),
            extraction_confidence=confidence,
        )
        with connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE receipt_batches
                SET closure_mode = ?, last_image_at = ?, close_after_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'open'
                """,
                (decision.closure_mode, _iso(now), _iso(decision.deadline(now)), batch_id),
            )
        return {
            "batch_id": batch_id,
            "status": "open",
            "image_count": len(images),
            "closure_mode": decision.closure_mode,
            "wait_seconds": decision.wait_seconds,
            "appears_complete": decision.appears_complete,
            "reason": decision.reason,
        }

    def finalize_due_batches(self, *, now: datetime | None = None) -> list[dict]:
        due_at = _iso(now or self.clock())
        with connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id FROM receipt_batches
                WHERE status = 'open' AND close_after_at <= ?
                ORDER BY close_after_at
                """,
                (due_at,),
            ).fetchall()
        return [self._finalize_batch(str(row["id"])) for row in rows]

    def _finalize_batch(
        self,
        batch_id: str,
        *,
        expected_sender: str | None = None,
        explicit: bool = False,
    ) -> dict:
        batch, images = self._batch_payload(batch_id)
        if batch["status"] != "open":
            return {"batch_id": batch_id, "status": batch["status"], "receipt_id": batch["receipt_id"]}
        if expected_sender is not None and batch["sender_identity"] != expected_sender:
            raise PermissionError("batch belongs to a different authenticated sender")
        if not images:
            with connection(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE receipt_batches
                    SET status = 'failed', error_code = 'empty_batch', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (batch_id,),
                )
            return self._store_batch_result(batch_id, {
                "batch_id": batch_id,
                "status": "failed",
                "error": "empty_batch",
                "message": "El lote no contenía ningún archivo válido; vuelve a enviar el ticket.",
            })
        media = [self.media_store.resolve(image["storage_id"]) for image in images]
        text = merge_ocr_pages([image["ocr_text"] for image in images])
        confidence = _confidence_average([image["ocr_confidence"] for image in images])
        try:
            receipt = process_receipt(
                text,
                [item.path for item in media],
                received_at=datetime.fromisoformat(batch["received_at"]),
                extraction_confidence=confidence,
            )
            ingested = insert_receipt(self.db_path, receipt, created_by=int(batch["created_by"]))
        except Exception as exc:
            with connection(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE receipt_batches
                    SET status = 'failed', error_code = 'unparseable', updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (batch_id,),
                )
            return self._store_batch_result(batch_id, {
                "batch_id": batch_id,
                "status": "failed",
                "error": "ticket_unparseable",
                "message": "No he podido reconciliar este ticket; revisa que estén todas las fotos.",
                "diagnostic": type(exc).__name__,
            })

        with connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE receipt_batches
                SET status = 'finalized', receipt_id = ?, closure_mode = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (ingested.receipt_id, "explicit" if explicit else batch["closure_mode"], batch_id),
            )
            if receipt.pending_questions:
                proposed = (
                    json.dumps(receipt.purchase_date.isoformat())
                    if receipt.purchase_date is not None and receipt.purchase_date_needs_confirmation
                    else None
                )
                for question in receipt.pending_questions:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO receipt_reviews(
                            receipt_id, batch_id, field_path, question, proposed_value_json
                        ) VALUES (?, ?, '/purchase_date', ?, ?)
                        """,
                        (ingested.receipt_id, batch_id, question, proposed),
                    )

        article_count = receipt.article_count
        if article_count is None:
            article_count = int(sum(item.purchase_quantity for item in receipt.items))
        amount = f"{receipt.amount_paid:.2f}".replace(".", ",")
        if receipt.status == "valid":
            message = f"🛒 {receipt.supermarket} · {amount} € · {article_count} artículos · guardado ✅"
        else:
            message = receipt.pending_questions[0] if receipt.pending_questions else "El ticket necesita revisión."
        return self._store_batch_result(batch_id, {
            "batch_id": batch_id,
            "status": receipt.status,
            "receipt_id": ingested.receipt_id,
            "inserted": ingested.inserted,
            "message": message,
            "pending_questions": receipt.pending_questions,
        })

    def review(self, context: TrustedRequestContext, *, answer: str | None = None) -> dict:
        principal = self.permissions.authorize(context)
        with connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT rr.* FROM receipt_reviews rr
                JOIN receipt_batches rb ON rb.id = rr.batch_id
                WHERE rr.status = 'open' AND rb.group_jid = ?
                ORDER BY rr.created_at, rr.id
                """,
                (context.group_jid,),
            ).fetchall()
        if not rows:
            return {"status": "no_pending_review"}
        if answer is None:
            return {
                "status": "needs_review",
                "count": len(rows),
                "questions": [dict(row) for row in rows],
            }
        if len(rows) != 1:
            return {
                "status": "ambiguous_review",
                "message": "Hay varias revisiones pendientes; indica el ticket.",
                "receipt_ids": sorted({int(row["receipt_id"]) for row in rows}),
            }
        review = rows[0]
        normalized_answer = answer.strip().casefold()
        if review["field_path"] != "/purchase_date":
            raise ValueError("unsupported review field")
        if normalized_answer in YES_ANSWERS:
            if review["proposed_value_json"] is None:
                raise ValueError("this review needs an explicit date")
            confirmed_date = date.fromisoformat(json.loads(review["proposed_value_json"]))
        else:
            try:
                confirmed_date = date.fromisoformat(answer.strip())
            except ValueError as exc:
                raise ValueError("answer must confirm or provide YYYY-MM-DD") from exc
        receipt = confirm_receipt_purchase_date(
            self.db_path,
            int(review["receipt_id"]),
            confirmed_date,
            corrected_by=principal.user_id,
        )
        with connection(self.db_path) as conn:
            conn.execute(
                """
                UPDATE receipt_reviews
                SET status = 'resolved', resolved_by = ?, answer_json = ?, resolved_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'open'
                """,
                (principal.user_id, json.dumps(answer, ensure_ascii=False), int(review["id"])),
            )
        return {
            "status": receipt.status,
            "receipt_id": int(review["receipt_id"]),
            "purchase_date": confirmed_date.isoformat(),
            "purchase_date_source": receipt.purchase_date_source,
        }

    def correct(self, context: TrustedRequestContext, *, receipt_id: int, field: str, value: str) -> dict:
        principal = self.permissions.authorize(context)
        if field != "purchase_date":
            raise ValueError("phase 2 local bridge currently permits only purchase_date corrections")
        corrected_date = date.fromisoformat(value)
        receipt = confirm_receipt_purchase_date(
            self.db_path,
            receipt_id,
            corrected_date,
            corrected_by=principal.user_id,
        )
        return {
            "status": receipt.status,
            "receipt_id": receipt_id,
            "field": field,
            "value": corrected_date.isoformat(),
        }

    def receipt(self, context: TrustedRequestContext, *, receipt_id: int) -> dict:
        self.permissions.authorize(context)
        stored = get_receipt(self.db_path, receipt_id)
        return {"untrusted_receipt_data": True, "receipt": stored}

    def query(self, context: TrustedRequestContext, *, intent: str, filters: dict | None = None) -> dict:
        self.permissions.authorize(context)
        return execute_query(self.db_path, intent, filters)

    def dashboard_status(self, context: TrustedRequestContext) -> dict:
        self.permissions.authorize(context)
        if self.dashboard_url is None:
            return {"status": "local_only", "available": False}
        return {"status": "available", "available": True, "url": self.dashboard_url}
