const MAX_BODY_BYTES = 750_000;
const SCHEMA_VERSION = 1;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    try {
      if (url.pathname === "/api/sync" && request.method === "POST") {
        return syncSnapshot(request, env);
      }

      if (url.pathname === "/api/dashboard" && request.method === "GET") {
        const snapshot = await readSnapshot(env);
        return json(snapshot || emptySnapshot(), 200, { "Cache-Control": "public, max-age=30, stale-while-revalidate=120" });
      }

      if (url.pathname === "/api/health" && request.method === "GET") {
        const snapshot = await readSnapshot(env);
        return json({
          ok: true,
          ready: Boolean(snapshot),
          updatedAt: snapshot?.generatedAt || null,
          receipts: snapshot?.receipts?.length || 0,
          items: snapshot?.items?.length || 0
        }, 200, { "Cache-Control": "no-store" });
      }

      if (url.pathname === "/manifest.webmanifest") {
        return json({
          name: "Cesta Inteligente",
          short_name: "Cesta",
          description: "Gasto, supermercados y evolución de la cesta de casa",
          start_url: "/",
          scope: "/",
          display: "standalone",
          background_color: "#f6f7f2",
          theme_color: "#0f7b62",
          lang: "es",
          icons: [{ src: "/app-icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any maskable" }]
        }, 200, { "Content-Type": "application/manifest+json; charset=utf-8" });
      }

      if (url.pathname === "/app-icon.svg" || url.pathname === "/favicon.ico") {
        return new Response(appIcon(), {
          headers: { "Content-Type": "image/svg+xml; charset=utf-8", "Cache-Control": "public, max-age=86400" }
        });
      }

      if (url.pathname === "/" || url.pathname === "/index.html") {
        return new Response(renderHtml(), {
          headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=60" }
        });
      }

      return new Response("Not found", { status: 404 });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Error desconocido";
      if (url.pathname.startsWith("/api/")) return json({ ok: false, error: message }, 500);
      return new Response(renderErrorHtml(), { status: 500, headers: { "Content-Type": "text/html; charset=utf-8" } });
    }
  }
};

async function ensureDatabase(env) {
  if (!env.DB) throw new Error("Base de datos no disponible");
  await env.DB.exec("CREATE TABLE IF NOT EXISTS cesta_snapshot (id INTEGER PRIMARY KEY CHECK (id = 1), schema_version INTEGER NOT NULL, generated_at TEXT NOT NULL, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)");
}

async function readSnapshot(env) {
  await ensureDatabase(env);
  const row = await env.DB.prepare("SELECT payload_json FROM cesta_snapshot WHERE id = 1").first();
  if (!row?.payload_json) return null;
  return JSON.parse(row.payload_json);
}

async function syncSnapshot(request, env) {
  if (!env.SYNC_TOKEN) return json({ ok: false }, 503);
  const auth = request.headers.get("authorization") || "";
  if (!safeEqual(auth, `Bearer ${env.SYNC_TOKEN}`)) return json({ ok: false }, 401);

  const length = Number(request.headers.get("content-length") || 0);
  if (length > MAX_BODY_BYTES) return json({ ok: false, error: "Snapshot demasiado grande" }, 413);
  const text = await request.text();
  if (new TextEncoder().encode(text).length > MAX_BODY_BYTES) return json({ ok: false, error: "Snapshot demasiado grande" }, 413);

  const payload = validateSnapshot(JSON.parse(text));
  await ensureDatabase(env);
  await env.DB.prepare(`
    INSERT INTO cesta_snapshot (id, schema_version, generated_at, payload_json, updated_at)
    VALUES (1, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(id) DO UPDATE SET
      schema_version = excluded.schema_version,
      generated_at = excluded.generated_at,
      payload_json = excluded.payload_json,
      updated_at = CURRENT_TIMESTAMP
  `).bind(payload.schemaVersion, payload.generatedAt, JSON.stringify(payload)).run();

  return json({ ok: true, receipts: payload.receipts.length, items: payload.items.length, updatedAt: payload.generatedAt });
}

function validateSnapshot(input) {
  if (!input || typeof input !== "object") throw new Error("Snapshot inválido");
  if (input.schemaVersion !== SCHEMA_VERSION) throw new Error("Versión de snapshot no compatible");
  if (!Array.isArray(input.receipts) || !Array.isArray(input.items)) throw new Error("Colecciones inválidas");
  if (input.receipts.length > 10_000 || input.items.length > 100_000) throw new Error("Snapshot fuera de límites");
  if (!/^\d{4}-\d{2}-\d{2}T/.test(String(input.generatedAt || ""))) throw new Error("Fecha de actualización inválida");

  const receipts = input.receipts.map((row) => ({
    id: integer(row.id),
    date: dateString(row.date),
    supermarket: shortText(row.supermarket, 80),
    store: shortText(row.store || "", 120),
    articleCount: nullableNumber(row.articleCount),
    amountPaidCents: nonNegative(row.amountPaidCents),
    savingsCents: nonNegative(row.savingsCents),
    validationStatus: row.validationStatus === "needs_review" ? "needs_review" : "valid"
  }));

  const validReceiptIds = new Set(receipts.map((row) => row.id));
  const items = input.items.map((row) => {
    const receiptId = integer(row.receiptId);
    if (!validReceiptIds.has(receiptId)) throw new Error("Artículo sin ticket válido");
    return {
      id: integer(row.id),
      receiptId,
      productStableId: shortText(row.productStableId || "", 96),
      product: shortText(row.product || "Sin nombre", 180),
      comparableProductStableId: shortText(row.comparableProductStableId || "", 96),
      comparableProduct: shortText(row.comparableProduct || row.product || "Sin nombre", 180),
      family: shortText(row.family || "Sin familia", 120),
      category: shortText(row.category || "Sin categoría", 120),
      date: dateString(row.date),
      supermarket: shortText(row.supermarket, 80),
      quantity: nullableNumber(row.quantity),
      normalizedQuantity: nullableNumber(row.normalizedQuantity),
      normalizedUnit: shortText(row.normalizedUnit || "other", 24),
      lineFinalCents: nonNegative(row.lineFinalCents),
      normalizedPriceCents: nullableNumber(row.normalizedPriceCents)
    };
  });

  return { schemaVersion: SCHEMA_VERSION, generatedAt: input.generatedAt, receipts, items };
}

function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let different = 0;
  for (let i = 0; i < a.length; i += 1) different |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return different === 0;
}

function shortText(value, max) {
  return String(value ?? "").trim().slice(0, max);
}

function integer(value) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < 0) throw new Error("Identificador inválido");
  return number;
}

function nonNegative(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) throw new Error("Importe inválido");
  return number;
}

function nullableNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error("Cantidad inválida");
  return number;
}

function dateString(value) {
  const text = String(value || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) throw new Error("Fecha inválida");
  return text;
}

function emptySnapshot() {
  return { schemaVersion: SCHEMA_VERSION, generatedAt: null, receipts: [], items: [] };
}

function json(payload, status = 200, headers = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers }
  });
}

function appIcon() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="112" fill="#0f7b62"/><path d="M123 208h266l-25 171a34 34 0 0 1-34 29H182a34 34 0 0 1-34-29l-25-171Z" fill="#fffdf8"/><path d="M186 218c8-74 132-74 140 0" fill="none" stroke="#fffdf8" stroke-width="30" stroke-linecap="round"/><path d="M202 272v73M256 272v73M310 272v73" stroke="#0f7b62" stroke-width="18" stroke-linecap="round"/></svg>`;
}

function renderHtml() {
  return `<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#0f7b62">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <meta name="apple-mobile-web-app-title" content="Cesta">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/app-icon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="/app-icon.svg">
  <title>Cesta Inteligente</title>
  <style>${styles()}</style>
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div>
        <p class="kicker">Mi hogar</p>
        <h1>Cesta Inteligente</h1>
        <p class="subtitle">Qué gastamos, dónde compramos y cómo evoluciona nuestra cesta.</p>
      </div>
      <div class="freshness"><span>Actualizado</span><strong id="updated">—</strong></div>
    </header>
    <label class="period-control">Periodo
      <select id="period">
        <option value="month">Este mes</option>
        <option value="quarter">Últimos 3 meses</option>
        <option value="year" selected>Este año</option>
        <option value="all">Todo</option>
      </select>
    </label>
    <section id="status" class="status-panel">Cargando la cesta…</section>
    <section id="app" class="hidden"></section>
    <footer>Solo se incluyen tickets válidos en los cálculos · Copia web saneada</footer>
  </main>
  <script>${clientScript()}</script>
</body>
</html>`;
}

function renderErrorHtml() {
  return `<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Cesta Inteligente</title><style>${styles()}</style><main class="shell"><section class="status-panel error">La cesta no está disponible ahora mismo. La copia local sigue intacta.</section></main></html>`;
}

function styles() {
  return `
    :root{--ink:#182019;--muted:#687168;--paper:#fffdf8;--line:#dddcd3;--accent:#0f7b62;--accent-soft:#dcefe8;--warm:#e1843b;--bg:#f6f7f2;--shadow:0 8px 24px rgba(24,32,25,.045)}
    *{box-sizing:border-box}html{background:var(--bg);color:var(--ink);font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}body{margin:0;background:var(--bg)}
    .shell{max-width:1180px;margin:0 auto;padding:42px 24px 64px}.topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:24px}
    .kicker{margin:0 0 8px;color:var(--accent);font-size:.78rem;font-weight:760;letter-spacing:.12em;text-transform:uppercase}.subtitle{margin:6px 0 0;color:var(--muted);font-size:1.02rem}
    h1,h2{letter-spacing:-.03em}h1{font-size:clamp(2.2rem,5vw,3.4rem);line-height:1;margin:0}h2{font-size:1.08rem;margin:0 0 22px}
    .freshness{min-width:150px;text-align:right;color:var(--muted);font-size:.78rem;padding-top:8px}.freshness span,.freshness strong{display:block}.freshness strong{color:var(--ink);font-size:.9rem;margin-top:4px}
    .period-control{display:flex;flex-direction:column;gap:7px;width:min(310px,100%);font-size:.84rem;font-weight:680;color:var(--muted);margin-bottom:24px}
    select{appearance:none;background:var(--paper) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%23687168' stroke-width='2'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E") no-repeat right 14px center;border:1px solid var(--line);border-radius:11px;padding:12px 42px 12px 14px;color:var(--ink);font:650 .95rem/1.2 inherit}
    .status-panel,.panel,.metric{background:var(--paper);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}.status-panel{padding:22px;color:var(--muted)}.error{color:#943b31}.hidden{display:none}
    .metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:14px}.metric{min-height:112px;padding:18px}.metric span{display:block;color:var(--muted);font-size:.86rem}.metric strong{display:block;font-size:1.75rem;line-height:1.15;margin-top:14px;letter-spacing:-.035em}
    .grid{display:grid;gap:14px;margin-top:14px}.charts{grid-template-columns:1.35fr 1fr}.details{grid-template-columns:1fr 1fr}.panel{padding:22px;min-width:0}.wide{margin-top:14px}
    .bars{display:grid;gap:12px}.bar-row{display:grid;grid-template-columns:minmax(80px,150px) 1fr 74px;gap:10px;align-items:center;font-size:.82rem}.bar-label{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.bar-track{height:11px;background:#eef0e9;border-radius:999px;overflow:hidden}.bar-fill{display:block;height:100%;border-radius:999px;background:var(--warm);transform-origin:left;animation:grow .5s ease-out}.bar-fill.green{background:var(--accent)}.bar-value{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted)}
    .line-chart{width:100%;height:260px;display:block}.axis-label{fill:var(--muted);font-size:11px}.grid-line{stroke:#e7e7df;stroke-width:1}.area{fill:var(--accent);opacity:.1}.line{fill:none;stroke:var(--accent);stroke-width:3;stroke-linecap:round;stroke-linejoin:round}.dot{fill:var(--accent)}
    .table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px}table{border-collapse:collapse;width:100%;min-width:520px;background:#fff}th,td{text-align:left;padding:12px 11px;border-bottom:1px solid #ecece5;font-size:.84rem}th{color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.045em;background:#fafaf6}tbody tr:last-child td{border-bottom:0}.money{text-align:right;font-variant-numeric:tabular-nums}
    .empty{padding:32px 8px;color:var(--muted);text-align:center}.empty strong{display:block;color:var(--ink);font-size:1.12rem;margin-bottom:7px}footer{color:var(--muted);font-size:.78rem;margin-top:24px;text-align:center}
    @keyframes grow{from{transform:scaleX(0)}}@media(prefers-reduced-motion:reduce){*{animation:none!important}}
    @media(max-width:760px){.shell{padding:28px 14px 42px}.topbar{display:block}.freshness{text-align:left;padding:14px 0 0}.metrics{grid-template-columns:repeat(2,1fr)}.charts,.details{grid-template-columns:1fr}.panel{padding:18px}.metric{min-height:96px;padding:15px}.metric strong{font-size:1.45rem}.bar-row{grid-template-columns:100px 1fr 66px}.line-chart{height:220px}}
    @media(max-width:420px){.metrics{grid-template-columns:1fr 1fr;gap:10px}.metric{min-height:88px}.metric strong{font-size:1.28rem}.subtitle{font-size:.94rem}}
  `;
}

function clientScript() {
  return `
    const state={snapshot:null,period:'year'};
    const euro=cents=>new Intl.NumberFormat('es-ES',{style:'currency',currency:'EUR'}).format((Number(cents)||0)/100);
    const dateLabel=value=>value?new Intl.DateTimeFormat('es-ES',{day:'2-digit',month:'2-digit',year:'numeric'}).format(new Date(value+'T12:00:00')):'—';
    const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
    const cutoff=period=>{const now=new Date();if(period==='all')return null;if(period==='month')return new Date(now.getFullYear(),now.getMonth(),1);if(period==='quarter')return new Date(now.getFullYear(),now.getMonth()-2,1);return new Date(now.getFullYear(),0,1)};
    const inPeriod=(date,period)=>{const start=cutoff(period);return !start||new Date(date+'T12:00:00')>=start};
    const groupSum=(rows,key,value)=>{const map=new Map();rows.forEach(row=>{const k=key(row);map.set(k,(map.get(k)||0)+value(row))});return [...map].map(([name,total])=>({name,total}))};
    const metric=(label,value)=>'<article class="metric"><span>'+escape(label)+'</span><strong>'+escape(value)+'</strong></article>';
    const bars=(rows,color='')=>{const max=Math.max(1,...rows.map(row=>row.total));return '<div class="bars">'+rows.map(row=>'<div class="bar-row"><span class="bar-label" title="'+escape(row.name)+'">'+escape(row.name)+'</span><span class="bar-track"><span class="bar-fill '+color+'" style="width:'+Math.max(2,row.total/max*100)+'%"></span></span><span class="bar-value">'+euro(row.total)+'</span></div>').join('')+'</div>'};
    const lineChart=rows=>{if(!rows.length)return '<div class="empty">Sin datos mensuales</div>';const w=700,h=260,p=34,max=Math.max(1,...rows.map(r=>r.total));const x=i=>rows.length===1?w/2:p+i*(w-2*p)/(rows.length-1);const y=v=>h-p-v/max*(h-2*p);const points=rows.map((r,i)=>x(i)+','+y(r.total)).join(' ');const area=p+','+(h-p)+' '+points+' '+(w-p)+','+(h-p);return '<svg class="line-chart" viewBox="0 0 '+w+' '+h+'" role="img" aria-label="Evolución mensual">'+[0,.5,1].map(v=>'<line class="grid-line" x1="'+p+'" x2="'+(w-p)+'" y1="'+y(max*v)+'" y2="'+y(max*v)+'"/>').join('')+'<polygon class="area" points="'+area+'"/><polyline class="line" points="'+points+'"/>'+rows.map((r,i)=>'<circle class="dot" cx="'+x(i)+'" cy="'+y(r.total)+'" r="4"/><text class="axis-label" text-anchor="middle" x="'+x(i)+'" y="'+(h-8)+'">'+escape(r.name)+'</text>').join('')+'</svg>'};
    const table=(headers,rows)=>'<div class="table-wrap"><table><thead><tr>'+headers.map(h=>'<th class="'+(h.money?'money':'')+'">'+escape(h.label)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+headers.map(h=>'<td class="'+(h.money?'money':'')+'">'+(h.html?row[h.key]:escape(row[h.key]))+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';

    function render(){
      const snapshot=state.snapshot||{receipts:[],items:[]};
      const allReceipts=snapshot.receipts||[];
      const valid=allReceipts.filter(r=>r.validationStatus==='valid'&&inPeriod(r.date,state.period));
      const pending=allReceipts.filter(r=>r.validationStatus==='needs_review');
      const validIds=new Set(valid.map(r=>r.id));
      const items=(snapshot.items||[]).filter(i=>validIds.has(i.receiptId)&&inPeriod(i.date,state.period));
      const total=valid.reduce((n,r)=>n+r.amountPaidCents,0),savings=valid.reduce((n,r)=>n+r.savingsCents,0),articles=valid.reduce((n,r)=>n+(r.articleCount||0),0);
      document.getElementById('updated').textContent=snapshot.generatedAt?new Intl.DateTimeFormat('es-ES',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(snapshot.generatedAt)):'Sin datos';
      const app=document.getElementById('app'),status=document.getElementById('status');status.classList.add('hidden');app.classList.remove('hidden');
      let html='<section class="metrics">'+metric('Gasto',euro(total))+metric('Tickets',valid.length)+metric('Cesta media',euro(valid.length?total/valid.length:0))+metric('Artículos',articles)+metric('Ahorro aplicado',euro(savings))+metric('Pendientes',pending.length)+'</section>';
      if(!valid.length){app.innerHTML=html+'<section class="panel wide empty"><strong>Sin compras en este periodo</strong>Prueba otro periodo o envía un ticket al grupo Cesta.</section>';return}
      const monthly=groupSum(valid,r=>r.date.slice(0,7),r=>r.amountPaidCents).sort((a,b)=>a.name.localeCompare(b.name));
      const stores=groupSum(valid,r=>r.supermarket,r=>r.amountPaidCents).sort((a,b)=>b.total-a.total);
      const categories=groupSum(items,r=>r.category,r=>r.lineFinalCents).sort((a,b)=>b.total-a.total).slice(0,8);
      const frequentMap=new Map();items.forEach(i=>{const k=i.comparableProduct;const row=frequentMap.get(k)||{product:k,category:i.category,purchases:0,total:0};row.purchases+=1;row.total+=i.lineFinalCents;frequentMap.set(k,row)});
      const frequent=[...frequentMap.values()].sort((a,b)=>b.purchases-a.purchases||b.total-a.total).slice(0,8).map(r=>({product:r.product,category:r.category,purchases:r.purchases,total:euro(r.total)}));
      const observations=new Map();items.filter(i=>i.normalizedPriceCents!==null&&['kg','L','unit','egg'].includes(i.normalizedUnit)).forEach(i=>{const k=i.comparableProductStableId+'|'+i.normalizedUnit;const row=observations.get(k)||[];row.push(i);observations.set(k,row)});
      const comparable=[...observations.values()].filter(rows=>new Set(rows.map(r=>r.supermarket)).size>1).flat().sort((a,b)=>b.date.localeCompare(a.date)).slice(0,16).map(i=>({date:dateLabel(i.date),store:i.supermarket,product:i.comparableProduct,unit:i.normalizedUnit,price:euro(i.normalizedPriceCents)}));
      const recent=[...valid].sort((a,b)=>b.date.localeCompare(a.date)||b.id-a.id).slice(0,10).map(r=>({date:dateLabel(r.date),store:r.supermarket,shop:r.store||'—',articles:r.articleCount??'—',total:euro(r.amountPaidCents)}));
      html+='<section class="grid charts"><article class="panel"><h2>Evolución mensual</h2>'+lineChart(monthly)+'</article><article class="panel"><h2>Supermercados</h2>'+bars(stores)+'</article></section>';
      html+='<section class="grid details"><article class="panel"><h2>Categorías</h2>'+bars(categories,'green')+'</article><article class="panel"><h2>Productos frecuentes</h2>'+table([{key:'product',label:'Producto'},{key:'category',label:'Categoría'},{key:'purchases',label:'Compras'},{key:'total',label:'Gasto',money:true}],frequent)+'</article></section>';
      html+='<section class="panel wide"><h2>Comparador entre cadenas</h2>'+(comparable.length?table([{key:'date',label:'Fecha'},{key:'store',label:'Supermercado'},{key:'product',label:'Producto'},{key:'unit',label:'Unidad'},{key:'price',label:'Precio normalizado',money:true}],comparable):'<div class="empty">Necesita el mismo producto y unidad en dos supermercados distintos.</div>')+'</section>';
      html+='<section class="panel wide"><h2>Últimos tickets</h2>'+table([{key:'date',label:'Fecha'},{key:'store',label:'Supermercado'},{key:'shop',label:'Tienda'},{key:'articles',label:'Artículos'},{key:'total',label:'Total',money:true}],recent)+'</section>';
      app.innerHTML=html;
    }

    document.getElementById('period').addEventListener('change',event=>{state.period=event.target.value;render()});
    fetch('/api/dashboard',{cache:'no-store'}).then(response=>{if(!response.ok)throw new Error();return response.json()}).then(data=>{state.snapshot=data;render()}).catch(()=>{const status=document.getElementById('status');status.textContent='No he podido cargar la copia web. La base local sigue intacta.';status.classList.add('error')});
  `;
}
