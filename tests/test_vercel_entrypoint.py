from __future__ import annotations

import json
import importlib.util
from pathlib import Path


APP_PATH = Path(__file__).parents[1] / "app.py"
SPEC = importlib.util.spec_from_file_location("cesta_vercel_app", APP_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
app = MODULE.app


def request(path: str) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app({"PATH_INFO": path}, start_response))
    return str(captured["status"]), captured["headers"], body


def test_vercel_root_serves_demo_dashboard():
    status, headers, body = request("/")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "Cesta Inteligente" in body.decode()


def test_vercel_dashboard_api_uses_only_demo_data():
    status, _, body = request("/api/dashboard")
    payload = json.loads(body)
    assert status == "200 OK"
    assert payload["schemaVersion"] == 1
    assert len(payload["receipts"]) == 4
    assert {row["supermarket"] for row in payload["receipts"]} == {"Mercado Norte", "Super Ahorro", "La Despensa"}
