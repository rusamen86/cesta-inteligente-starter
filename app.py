from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from typing import Callable, Iterable


def _demo_snapshot() -> dict[str, object]:
    now = datetime.now(timezone.utc)

    def day(days_ago: int) -> str:
        return (now - timedelta(days=days_ago)).date().isoformat()

    receipts = [
        {"id": 1, "date": day(24), "supermarket": "Mercado Norte", "articleCount": 8, "amountPaidCents": 2840, "savingsCents": 210, "validationStatus": "valid"},
        {"id": 2, "date": day(15), "supermarket": "Super Ahorro", "articleCount": 12, "amountPaidCents": 4315, "savingsCents": 380, "validationStatus": "valid"},
        {"id": 3, "date": day(7), "supermarket": "Mercado Norte", "articleCount": 6, "amountPaidCents": 2260, "savingsCents": 90, "validationStatus": "valid"},
        {"id": 4, "date": day(2), "supermarket": "La Despensa", "articleCount": 10, "amountPaidCents": 3675, "savingsCents": 250, "validationStatus": "valid"},
    ]
    items = [
        {"receiptId": 1, "product": "Leche", "category": "Alimentación", "lineFinalCents": 360},
        {"receiptId": 1, "product": "Plátano", "category": "Alimentación", "lineFinalCents": 285},
        {"receiptId": 2, "product": "Tomate", "category": "Alimentación", "lineFinalCents": 340},
        {"receiptId": 2, "product": "Huevos", "category": "Alimentación", "lineFinalCents": 310},
        {"receiptId": 3, "product": "Yogur natural", "category": "Alimentación", "lineFinalCents": 260},
        {"receiptId": 4, "product": "Papel de cocina", "category": "Limpieza y hogar", "lineFinalCents": 295},
    ]
    return {"schemaVersion": 1, "generatedAt": now.isoformat(), "receipts": receipts, "items": items}


PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#0f7b62"><title>Cesta Inteligente</title>
<style>
:root{--ink:#182019;--muted:#687168;--paper:#fffdf8;--line:#dddcd3;--green:#0f7b62;--orange:#e1843b;--bg:#f6f7f2}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{max-width:1060px;margin:auto;padding:44px 22px 64px}.kicker{color:var(--green);font-size:.76rem;font-weight:800;letter-spacing:.13em;text-transform:uppercase;margin:0 0 9px}h1{font-size:clamp(2.2rem,6vw,3.7rem);letter-spacing:-.055em;margin:0}.sub{color:var(--muted);margin:8px 0 30px}.notice{padding:13px 16px;background:#dcefe8;border:1px solid #badbce;border-radius:12px;margin-bottom:18px;color:#245d4d;font-size:.9rem}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:13px}.card,.panel{background:var(--paper);border:1px solid var(--line);border-radius:15px;box-shadow:0 8px 24px rgba(24,32,25,.045)}.card{padding:19px;min-height:108px}.label{color:var(--muted);font-size:.84rem}.value{font-size:1.65rem;font-weight:800;margin-top:16px;letter-spacing:-.04em}.grid{display:grid;grid-template-columns:1fr 1fr;gap:13px;margin-top:13px}.panel{padding:21px}h2{font-size:1.02rem;margin:0 0 19px}.bar{display:grid;grid-template-columns:120px 1fr 82px;align-items:center;gap:9px;margin:12px 0;font-size:.82rem}.track{height:10px;background:#ecefe8;border-radius:99px;overflow:hidden}.fill{display:block;height:100%;background:var(--orange);border-radius:99px}.money{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}table{border-collapse:collapse;width:100%;font-size:.84rem}th,td{padding:11px 8px;border-bottom:1px solid #ecece5;text-align:left}th{color:var(--muted);font-size:.74rem;text-transform:uppercase}footer{color:var(--muted);font-size:.77rem;text-align:center;margin-top:24px}@media(max-width:700px){.metrics{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}.bar{grid-template-columns:96px 1fr 74px}.shell{padding-top:28px}}
</style></head><body><main class="shell"><p class="kicker">Proyecto de ejemplo</p><h1>Cesta Inteligente</h1><p class="sub">Qué gastamos, dónde compramos y cómo evoluciona la cesta.</p><div class="notice">Demo desplegable con datos ficticios. La instalación privada guarda los tickets y la base de datos en tu propio entorno.</div><section id="metrics" class="metrics"></section><section class="grid"><article class="panel"><h2>Supermercados</h2><div id="stores"></div></article><article class="panel"><h2>Últimos tickets</h2><div id="tickets"></div></article></section><footer>Sin datos personales · Starter auditable y local-first</footer></main>
<script>
const euro=c=>new Intl.NumberFormat('es-ES',{style:'currency',currency:'EUR'}).format(c/100);const esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
fetch('/api/dashboard').then(r=>r.json()).then(s=>{const rs=s.receipts.filter(r=>r.validationStatus==='valid'),total=rs.reduce((n,r)=>n+r.amountPaidCents,0),articles=rs.reduce((n,r)=>n+r.articleCount,0),saving=rs.reduce((n,r)=>n+r.savingsCents,0);document.querySelector('#metrics').innerHTML=[['Gasto',euro(total)],['Tickets',rs.length],['Artículos',articles],['Ahorro',euro(saving)]].map(x=>`<article class="card"><div class="label">${x[0]}</div><div class="value">${x[1]}</div></article>`).join('');const grouped={};rs.forEach(r=>grouped[r.supermarket]=(grouped[r.supermarket]||0)+r.amountPaidCents);const stores=Object.entries(grouped).sort((a,b)=>b[1]-a[1]),max=Math.max(...stores.map(x=>x[1]));document.querySelector('#stores').innerHTML=stores.map(([name,value])=>`<div class="bar"><span>${esc(name)}</span><span class="track"><span class="fill" style="width:${value/max*100}%"></span></span><span class="money">${euro(value)}</span></div>`).join('');document.querySelector('#tickets').innerHTML='<table><thead><tr><th>Fecha</th><th>Tienda</th><th class="money">Total</th></tr></thead><tbody>'+[...rs].reverse().map(r=>`<tr><td>${esc(r.date)}</td><td>${esc(r.supermarket)}</td><td class="money">${euro(r.amountPaidCents)}</td></tr>`).join('')+'</tbody></table>'});
</script></body></html>"""


class CestaDemo:
    def __call__(
        self,
        environ: dict[str, object],
        start_response: Callable[[str, list[tuple[str, str]]], object],
    ) -> Iterable[bytes]:
        path = str(environ.get("PATH_INFO", "/"))
        if path in {"/", "/index.html"}:
            return self._respond(start_response, HTTPStatus.OK, PAGE, "text/html; charset=utf-8")
        if path == "/api/dashboard":
            return self._json(start_response, HTTPStatus.OK, _demo_snapshot())
        if path == "/api/health":
            return self._json(start_response, HTTPStatus.OK, {"ok": True, "mode": "demo"})
        return self._respond(start_response, HTTPStatus.NOT_FOUND, "Not found", "text/plain; charset=utf-8")

    @staticmethod
    def _json(start_response, status: HTTPStatus, payload: object) -> Iterable[bytes]:
        return CestaDemo._respond(start_response, status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

    @staticmethod
    def _respond(start_response, status: HTTPStatus, body: str, content_type: str) -> Iterable[bytes]:
        encoded = body.encode("utf-8")
        start_response(
            f"{status.value} {status.phrase}",
            [
                ("Content-Type", content_type),
                ("Content-Length", str(len(encoded))),
                ("Cache-Control", "public, max-age=60"),
                ("X-Content-Type-Options", "nosniff"),
                ("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'"),
            ],
        )
        return [encoded]


app = CestaDemo()
