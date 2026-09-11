from __future__ import annotations

import argparse
import json
import os
import socketserver
import stat
from pathlib import Path

from .bridge import CestaBridge
from .media_store import MediaStore
from .ocr import MacVisionOCR
from .permissions import PermissionService, TrustedRequestContext


ALLOWED_OPERATIONS = {
    "ingest",
    "query",
    "review",
    "correct",
    "receipt",
    "dashboard_status",
}
TRUSTED_INTERNAL_OPERATIONS = {"stage_inbound"}


def dispatch(bridge: CestaBridge, request: dict) -> dict:
    operation = str(request.get("operation", ""))
    if operation not in ALLOWED_OPERATIONS | TRUSTED_INTERNAL_OPERATIONS:
        raise ValueError("unsupported bridge operation")
    context = TrustedRequestContext.from_dict(dict(request.get("context") or {}))
    params = dict(request.get("params") or {})
    if operation == "stage_inbound":
        refs = params.get("media_refs") or []
        if not isinstance(refs, list) or not 1 <= len(refs) <= 12 or not all(isinstance(ref, str) for ref in refs):
            raise ValueError("media_refs must contain between 1 and 12 relative references")
        stored = [bridge.media_store.register_trusted_inbound(ref) for ref in refs]
        return bridge.ingest(context, media_ids=[media.storage_id for media in stored], action="append")
    if operation == "ingest":
        return bridge.ingest(
            context,
            media_ids=list(params.get("media_ids") or []),
            action=str(params.get("action", "append")),
            correlated_batch_id=(
                str(params["batch_id"])
                if isinstance(params.get("batch_id"), str)
                and str(params["batch_id"]).startswith("batch_")
                else None
            ),
        )
    if operation == "query":
        return bridge.query(context, intent=str(params["intent"]), filters=dict(params.get("filters") or {}))
    if operation == "review":
        answer = params.get("answer")
        return bridge.review(context, answer=None if answer is None else str(answer))
    if operation == "correct":
        return bridge.correct(
            context,
            receipt_id=int(params["receipt_id"]),
            field=str(params["field"]),
            value=str(params["value"]),
        )
    if operation == "receipt":
        return bridge.receipt(context, receipt_id=int(params["receipt_id"]))
    return bridge.dashboard_status(context)


class _RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(1024 * 1024)
        if not raw or not raw.endswith(b"\n"):
            return
        try:
            request = json.loads(raw)
            result = dispatch(self.server.bridge, request)  # type: ignore[attr-defined]
            response = {"ok": True, "result": result}
        except Exception as exc:
            response = {
                "ok": False,
                "error": type(exc).__name__,
                "message": str(exc)[:300],
            }
        self.wfile.write(json.dumps(response, ensure_ascii=False, default=str).encode("utf-8") + b"\n")


class CestaUnixServer(socketserver.UnixStreamServer):
    def __init__(self, socket_path: str | Path, bridge: CestaBridge) -> None:
        path = Path(socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            mode = path.lstat().st_mode
            if not stat.S_ISSOCK(mode):
                raise RuntimeError("refusing to replace a non-socket bridge path")
            path.unlink()
        self.socket_path = path
        self.bridge = bridge
        super().__init__(str(path), _RequestHandler)
        os.chmod(path, 0o600)

    def service_actions(self) -> None:
        """Close due persistent batches during the normal server poll loop."""
        self.bridge.finalize_due_batches()

    def server_close(self) -> None:
        super().server_close()
        if self.socket_path.exists() and stat.S_ISSOCK(self.socket_path.lstat().st_mode):
            self.socket_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(prog="cesta-bridge")
    parser.add_argument("--db", required=True)
    parser.add_argument("--inbound-root", required=True)
    parser.add_argument("--object-root", required=True)
    parser.add_argument("--vision-helper", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--group-jid", required=True)
    parser.add_argument("--agent-id", default="cesta")
    parser.add_argument("--dashboard-url")
    args = parser.parse_args()

    media_store = MediaStore(args.db, inbound_root=args.inbound_root, object_root=args.object_root)
    permissions = PermissionService(args.db, agent_id=args.agent_id, group_jid=args.group_jid)
    bridge = CestaBridge(
        db_path=args.db,
        media_store=media_store,
        ocr_engine=MacVisionOCR(args.vision_helper),
        permissions=permissions,
        dashboard_url=args.dashboard_url,
    )
    with CestaUnixServer(args.socket, bridge) as server:
        server.serve_forever(poll_interval=0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
