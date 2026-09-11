from __future__ import annotations

import json
import socket
import stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cesta_inteligente.bridge import CestaBridge
from cesta_inteligente.bridge_server import CestaUnixServer, dispatch
from cesta_inteligente.database import connection, receipt_count
from cesta_inteligente.media_store import MediaStore, UnsafeMediaReference
from cesta_inteligente.ocr import MappingOCR
from cesta_inteligente.permissions import AuthorizationError, PermissionService, TrustedRequestContext
from cesta_inteligente.query_api import QueryValidationError


GROUP_JID = "120363000000000000@g.us"
OWNER_ID = "34000000001@s.whatsapp.net"
SAM_ID = "34300000000000@lid"


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def context(sender: str, at: datetime, *, owner: bool = False) -> TrustedRequestContext:
    return TrustedRequestContext(
        agent_id="cesta",
        channel="whatsapp",
        group_jid=GROUP_JID,
        session_key=f"agent:cesta:whatsapp:group:{GROUP_JID}",
        requester_sender_id=sender,
        sender_is_owner=owner,
        received_at=at,
    )


def build_bridge(tmp_path: Path, texts: list[tuple[str, str]]):
    inbound = tmp_path / "inbound"
    inbound.mkdir()
    db_path = tmp_path / "cesta.db"
    media_store = MediaStore(db_path, inbound_root=inbound, object_root=tmp_path / "objects")
    mapping: dict[str, str] = {}
    stored = []
    for filename, text in texts:
        (inbound / filename).write_bytes(f"fixture:{filename}".encode())
        media = media_store.register_trusted_inbound(filename)
        mapping[media.storage_id] = text
        stored.append(media)
    permissions = PermissionService(db_path, agent_id="cesta", group_jid=GROUP_JID)
    owner_id = permissions.register_identity(
        name="Alex",
        role="owner",
        channel="whatsapp",
        identity_kind="requester_id",
        identity_value=OWNER_ID,
    )
    permissions.register_identity(
        name="Sam",
        role="user",
        channel="whatsapp",
        identity_kind="requester_id",
        identity_value=SAM_ID,
        verified_by_user_id=owner_id,
    )
    clock = MutableClock(datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc))
    bridge = CestaBridge(
        db_path=db_path,
        media_store=media_store,
        ocr_engine=MappingOCR(mapping),
        permissions=permissions,
        clock=clock,
    )
    return bridge, media_store, stored, clock, db_path


def test_complete_single_image_uses_short_window_and_auto_finalizes(tmp_path: Path):
    ticket = (Path(__file__).parent / "fixtures" / "mercadona" / "ticket.txt").read_text(encoding="utf-8")
    bridge, _, stored, clock, db_path = build_bridge(tmp_path, [("mercadona.jpg", ticket)])
    ctx = context(OWNER_ID, clock(), owner=True)

    queued = bridge.ingest(ctx, media_ids=[stored[0].storage_id])

    assert queued["status"] == "open"
    assert queued["appears_complete"] is True
    assert queued["closure_mode"] == "short"
    assert queued["wait_seconds"] == 10
    clock.advance(9)
    assert bridge.finalize_due_batches() == []
    clock.advance(2)
    finalized = bridge.finalize_due_batches()
    assert finalized[0]["status"] == "valid"
    assert finalized[0]["message"] == "🛒 Mercadona · 14,11 € · 6 artículos · guardado ✅"
    assert bridge.ingest(
        ctx,
        action="status",
        correlated_batch_id=queued["batch_id"],
    )["receipt_id"] == finalized[0]["receipt_id"]
    assert receipt_count(db_path) == 1


def test_status_never_associates_a_completed_batch_with_a_new_message(tmp_path: Path):
    ticket = (Path(__file__).parent / "fixtures" / "mercadona" / "ticket.txt").read_text(encoding="utf-8")
    bridge, _, stored, clock, _ = build_bridge(tmp_path, [("mercadona.jpg", ticket)])
    first_context = context(OWNER_ID, clock(), owner=True)
    bridge.ingest(first_context, media_ids=[stored[0].storage_id])
    bridge.ingest(first_context, action="finalize")

    new_message_context = context(OWNER_ID, clock(), owner=True)

    assert bridge.ingest(new_message_context, action="status") == {"status": "no_open_batch"}


def test_status_returns_only_the_explicitly_correlated_completed_batch(tmp_path: Path):
    ticket = (Path(__file__).parent / "fixtures" / "mercadona" / "ticket.txt").read_text(encoding="utf-8")
    bridge, _, stored, clock, _ = build_bridge(tmp_path, [("mercadona.jpg", ticket)])
    request_context = context(OWNER_ID, clock(), owner=True)
    staged = bridge.ingest(request_context, media_ids=[stored[0].storage_id])
    finalized = bridge.ingest(request_context, action="finalize")

    result = bridge.ingest(
        request_context,
        action="status",
        correlated_batch_id=staged["batch_id"],
    )

    assert result == finalized


def test_incomplete_carrefour_waits_long_then_second_page_switches_to_short(tmp_path: Path):
    ticket = (Path(__file__).parent / "fixtures" / "carrefour" / "ticket.txt").read_text(encoding="utf-8")
    split_at = ticket.index("QUESO CARREFOUR 200G")
    page_1, page_2 = ticket[:split_at], ticket[split_at:]
    bridge, _, stored, clock, _ = build_bridge(
        tmp_path,
        [("carrefour-1.jpg", page_1), ("carrefour-2.jpg", page_2)],
    )
    first = bridge.ingest(context(OWNER_ID, clock(), owner=True), media_ids=[stored[0].storage_id])
    assert first["closure_mode"] == "long"
    assert first["wait_seconds"] == 90

    clock.advance(20)
    second = bridge.ingest(context(OWNER_ID, clock(), owner=True), media_ids=[stored[1].storage_id])
    assert second["image_count"] == 2
    assert second["closure_mode"] == "short"
    assert second["wait_seconds"] == 10

    finalized = bridge.ingest(context(OWNER_ID, clock(), owner=True), action="finalize")
    assert finalized["status"] == "needs_review"
    assert finalized["receipt_id"] > 0
    assert finalized["pending_questions"] == [
        "No aparece la fecha en el ticket. He inferido 2026-08-20 por la recepción; ¿la confirmas?"
    ]

    resolved = bridge.review(context(SAM_ID, clock()), answer="sí")
    assert resolved["status"] == "valid"
    assert resolved["purchase_date_source"] == "user_confirmed"


def test_reingesting_same_ticket_does_not_duplicate(tmp_path: Path):
    ticket = (Path(__file__).parent / "fixtures" / "mercadona" / "ticket.txt").read_text(encoding="utf-8")
    bridge, _, stored, clock, db_path = build_bridge(tmp_path, [("mercadona.jpg", ticket)])
    ctx = context(OWNER_ID, clock(), owner=True)
    bridge.ingest(ctx, media_ids=[stored[0].storage_id])
    first = bridge.ingest(ctx, action="finalize")
    bridge.ingest(ctx, media_ids=[stored[0].storage_id])
    duplicate = bridge.ingest(ctx, action="finalize")

    assert first["inserted"] is True
    assert duplicate["inserted"] is False
    assert duplicate["receipt_id"] == first["receipt_id"]
    assert receipt_count(db_path) == 1


def test_permissions_use_exact_authenticated_identity_not_display_name(tmp_path: Path):
    ticket = (Path(__file__).parent / "fixtures" / "mercadona" / "ticket.txt").read_text(encoding="utf-8")
    bridge, _, stored, clock, _ = build_bridge(tmp_path, [("mercadona.jpg", ticket)])
    fake_alex = context("Alex", clock(), owner=False)

    with pytest.raises(AuthorizationError, match="not enrolled"):
        bridge.ingest(fake_alex, media_ids=[stored[0].storage_id])

    mismatched_owner_bit = context(SAM_ID, clock(), owner=True)
    with pytest.raises(AuthorizationError, match="owner bit"):
        bridge.ingest(mismatched_owner_bit, media_ids=[stored[0].storage_id])


def test_media_ingress_rejects_absolute_traversal_and_symlinks(tmp_path: Path):
    inbound = tmp_path / "inbound"
    inbound.mkdir()
    (inbound / "ticket.jpg").write_bytes(b"ticket")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    (inbound / "link.jpg").symlink_to(outside)
    store = MediaStore(tmp_path / "cesta.db", inbound_root=inbound, object_root=tmp_path / "objects")

    with pytest.raises(UnsafeMediaReference):
        store.register_trusted_inbound(str(outside))
    with pytest.raises(UnsafeMediaReference):
        store.register_trusted_inbound("../outside.jpg")
    with pytest.raises(UnsafeMediaReference, match="symlink"):
        store.register_trusted_inbound("link.jpg")


def test_query_interface_is_enumerated_and_parameterized(tmp_path: Path):
    ticket = (Path(__file__).parent / "fixtures" / "mercadona" / "ticket.txt").read_text(encoding="utf-8")
    bridge, _, stored, clock, db_path = build_bridge(tmp_path, [("mercadona.jpg", ticket)])
    ctx = context(OWNER_ID, clock(), owner=True)
    bridge.ingest(ctx, media_ids=[stored[0].storage_id])
    bridge.ingest(ctx, action="finalize")

    result = bridge.query(ctx, intent="month_total", filters={"month": "2026-08"})
    assert result["amount_paid_cents"] == 1411
    injection = bridge.query(
        ctx,
        intent="supermarket_total",
        filters={"supermarket": "mercadona' OR 1=1 --"},
    )
    assert injection["receipt_count"] == 0
    with pytest.raises(QueryValidationError):
        bridge.query(ctx, intent="raw_sql", filters={"sql": "DROP TABLE receipts"})
    with connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1


def test_ambiguous_yes_does_not_resolve_multiple_reviews(tmp_path: Path):
    ticket = (Path(__file__).parent / "fixtures" / "carrefour" / "ticket.txt").read_text(encoding="utf-8")
    bridge, _, stored, clock, db_path = build_bridge(
        tmp_path,
        [("carrefour-a.jpg", ticket), ("carrefour-b.jpg", ticket.replace("PAPEL ALUMINIO", "PAPEL ALUMINIO X"))],
    )
    for media in stored:
        ctx = context(OWNER_ID, clock(), owner=True)
        bridge.ingest(ctx, media_ids=[media.storage_id])
        bridge.ingest(ctx, action="finalize")
        clock.advance(1)

    result = bridge.review(context(SAM_ID, clock()), answer="sí")
    assert result["status"] == "ambiguous_review"
    with connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM receipt_reviews WHERE status = 'open'").fetchone()[0] == 2


def test_unix_bridge_exposes_only_narrow_protocol_and_socket_is_private(tmp_path: Path):
    bridge, _, _, clock, _ = build_bridge(tmp_path, [])
    socket_path = tmp_path / "run" / "cesta.sock"
    server = CestaUnixServer(socket_path, bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        request = {
            "operation": "dashboard_status",
            "context": context(OWNER_ID, clock(), owner=True).to_dict(),
            "params": {},
        }
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(json.dumps(request).encode() + b"\n")
            response = json.loads(client.makefile("rb").readline())
        assert response == {
            "ok": True,
            "result": {"status": "local_only", "available": False},
        }

        request["operation"] = "shell"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(json.dumps(request).encode() + b"\n")
            response = json.loads(client.makefile("rb").readline())
        assert response["ok"] is False
        assert response["message"] == "unsupported bridge operation"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not socket_path.exists()


def test_internal_media_staging_revalidates_relative_reference_and_ingests(tmp_path: Path):
    ticket = (Path(__file__).parent / "fixtures" / "mercadona" / "ticket.txt").read_text(encoding="utf-8")
    bridge, _, stored, clock, _ = build_bridge(tmp_path, [("incoming.jpg", ticket)])
    # Remove the already registered row so this exercise starts at the trusted bridge boundary.
    with connection(bridge.db_path) as conn:
        conn.execute("DELETE FROM media_objects WHERE storage_id = ?", (stored[0].storage_id,))
    result = dispatch(
        bridge,
        {
            "operation": "stage_inbound",
            "context": context(OWNER_ID, clock(), owner=True).to_dict(),
            "params": {"media_refs": ["incoming.jpg"]},
        },
    )
    assert result["status"] == "open"
    assert result["image_count"] == 1

    with pytest.raises(UnsafeMediaReference):
        dispatch(
            bridge,
            {
                "operation": "stage_inbound",
                "context": context(OWNER_ID, clock(), owner=True).to_dict(),
                "params": {"media_refs": ["../outside.jpg"]},
            },
        )


def test_invalid_opaque_media_id_does_not_create_an_empty_batch(tmp_path: Path):
    bridge, _, _, clock, db_path = build_bridge(tmp_path, [])
    ctx = context(OWNER_ID, clock(), owner=True)

    with pytest.raises((KeyError, UnsafeMediaReference)):
        bridge.ingest(ctx, media_ids=["cfe65f45-3eb8-4f21-b7b9-2a094f26f483"])

    with connection(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM receipt_batches").fetchone()[0] == 0
    assert bridge.finalize_due_batches() == []


def test_due_empty_batch_is_failed_without_crashing_the_poller(tmp_path: Path):
    bridge, _, _, clock, db_path = build_bridge(tmp_path, [])
    ctx = context(OWNER_ID, clock(), owner=True)
    principal = bridge.permissions.authorize(ctx)
    batch_id = bridge._create_batch(ctx, principal.user_id)

    result = bridge.finalize_due_batches()

    assert result == [
        {
            "batch_id": batch_id,
            "status": "failed",
            "error": "empty_batch",
            "message": "El lote no contenía ningún archivo válido; vuelve a enviar el ticket.",
        }
    ]
    with connection(db_path) as conn:
        row = conn.execute("SELECT status, error_code FROM receipt_batches WHERE id = ?", (batch_id,)).fetchone()
    assert tuple(row) == ("failed", "empty_batch")


def test_unix_server_poll_closes_due_batch_and_persists_result(tmp_path: Path):
    ticket = (Path(__file__).parent / "fixtures" / "mercadona" / "ticket.txt").read_text(encoding="utf-8")
    bridge, _, stored, clock, db_path = build_bridge(tmp_path, [("mercadona.jpg", ticket)])
    ctx = context(OWNER_ID, clock(), owner=True)
    queued = bridge.ingest(ctx, media_ids=[stored[0].storage_id])
    clock.advance(11)

    server = CestaUnixServer(tmp_path / "run" / "cesta.sock", bridge)
    try:
        server.service_actions()
    finally:
        server.server_close()

    assert receipt_count(db_path) == 1
    result = bridge.ingest(ctx, action="status", correlated_batch_id=queued["batch_id"])
    assert result["status"] == "valid"
    assert result["message"].endswith("guardado ✅")
