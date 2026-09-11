# Montaje con OpenClaw

Esta guía describe la arquitectura; no sustituye la documentación de la versión de OpenClaw instalada.

## 1. Preparar el proyecto

```bash
uv sync --extra dev
swiftc -O tools/macos-vision-ocr.swift -o artifacts/bin/cesta-vision-ocr
cd integrations/openclaw-cesta
npm ci
npm test
npm run build
```

## 2. Crear el agente aislado

Copia `config/openclaw/workspace-cesta/AGENTS.md` al workspace exclusivo del agente. No le des acceso al workspace principal ni al sistema de archivos del host.

Usa `config/openclaw/cesta-sandbox-candidate.json5` como referencia y sustituye las rutas de ejemplo. Antes de aplicarlo, verifica la política con las herramientas de diagnóstico de tu versión de OpenClaw.

## 3. Arrancar el bridge

Ejemplo manual:

```bash
uv run cesta-bridge \
  --db <PROJECT_DIR>/data/cesta.db \
  --inbound-root <OPENCLAW_INBOUND_MEDIA> \
  --object-root <PROJECT_DIR>/data/objects \
  --vision-helper <PROJECT_DIR>/artifacts/bin/cesta-vision-ocr \
  --socket <PROJECT_DIR>/data/cesta-bridge.sock \
  --group-jid <GROUP_JID> \
  --agent-id cesta \
  --dashboard-url http://localhost:8501
```

El socket debe quedar con permisos `0600`. En producción conviene ejecutarlo con `launchd` o un supervisor equivalente y guardar los logs fuera de Git.

## 4. Registrar el plugin

Añade `integrations/openclaw-cesta` a las rutas de plugins y configura `cesta-local` con:

```json5
{
  socketPath: "<PROJECT_DIR>/data/cesta-bridge.sock",
  inboundMediaRoot: "<OPENCLAW_INBOUND_MEDIA>",
  agentId: "cesta",
  groupJid: "<GROUP_JID>@g.us",
  allowedRequesterIds: ["<OWNER_AUTHENTICATED_ID>", "<MEMBER_AUTHENTICATED_ID>"]
}
```

Los IDs deben proceder de eventos autenticados reales. Nunca uses el nombre mostrado, números escritos en mensajes o valores inventados.

## 5. Binding y allowlists

Vincula únicamente el grupo elegido al agente `cesta`. Mantén el DM administrativo en el agente principal y limita las herramientas de Cesta a:

- `cesta_ingest`
- `cesta_query`
- `cesta_review`
- `cesta_correct`
- `cesta_receipt`
- `cesta_dashboard_status`

Deniega shell, filesystem, administración, elevated y acceso a otros agentes.

## 6. Prueba antes de usar tickets reales

1. Remitente autorizado: las herramientas aparecen.
2. Remitente no autorizado: fallo cerrado.
3. Nombre mostrado falsificado: no concede acceso.
4. Ticket sintético de una imagen: se inserta una vez.
5. Reenvío idéntico: no duplica el ticket.
6. Ticket multiimagen: conserva el lote hasta «ya está» o el timeout.
7. Dashboard y SQLite: cifras coherentes.

Haz una copia de seguridad antes de cualquier cambio de configuración o migración.

