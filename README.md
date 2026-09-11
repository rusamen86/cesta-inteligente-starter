# Cesta Inteligente

Sistema local y auditable para convertir tickets de supermercado en una base de datos útil: gasto, ahorro, categorías, productos comparables y evolución de precios.

La edición de este repositorio es un *starter* sin datos domésticos. Incluye código, pruebas, tickets de ejemplo desidentificados y plantillas de configuración; no incluye bases SQLite, imágenes, OCR reales, números de teléfono, identificadores de canal, secretos ni rutas del equipo original.

## Qué incluye

- Procesador de OCR normalizado para Mercadona, Carrefour, Aldi, DIA y Lupa/Semark.
- Validaciones contables antes de aceptar un ticket.
- SQLite como fuente de verdad local y auditable.
- Taxonomía `SKU → producto comparable → familia → categoría`.
- Dashboard Streamlit con filtros, gasto, cesta media, ahorro y comparador entre cadenas.
- Bridge Unix y plugin opcional para integrarlo de forma restringida con OpenClaw y WhatsApp.
- Dashboard web opcional con D1 y sincronización firmada desde SQLite.

## Requisitos

- macOS 14 o posterior para el OCR nativo incluido.
- Python 3.12.
- [`uv`](https://docs.astral.sh/uv/) para instalar y ejecutar el proyecto.
- Node.js 22 si se va a usar el plugin de OpenClaw.
- Docker si se activa el agente de OpenClaw con el sandbox propuesto.

## Arranque local en cinco minutos

```bash
git clone <URL_DEL_REPO>
cd cesta-inteligente
uv sync --extra dev
uv run pytest
uv run cesta demo
uv run streamlit run dashboard/app.py
```

El comando `cesta demo` crea `data/demo-cesta.db` a partir de un ticket de ejemplo. Después, el dashboard queda disponible en `http://localhost:8501`.

Para empezar con una base vacía basta con abrir el dashboard: el esquema se crea en el primer uso. Puedes cambiar la ruta y el nombre visible del hogar:

```bash
export CESTA_DB_PATH="$PWD/data/cesta.db"
export CESTA_HOUSEHOLD_LABEL="Mi hogar"
uv run streamlit run dashboard/app.py
```

## Procesar un ticket desde texto OCR

```bash
uv run cesta process \
  --text tests/fixtures/mercadona/ticket.txt \
  --source tests/fixtures/mercadona/ticket.txt \
  --confirmed-date 2025-01-15 \
  --output data/resultado.json
```

La CLI genera un resultado normalizado y validado. Para la integración completa con WhatsApp, el bridge se encarga del OCR, la persistencia, los lotes multiimagen y las revisiones.

## Integración opcional con OpenClaw

La integración está aislada deliberadamente:

- El agente `cesta` solo recibe seis herramientas `cesta_*`.
- El plugin no ejecuta shell ni acepta rutas propuestas por el modelo.
- Los tickets se tratan siempre como entrada no confiable.
- La identidad se toma del contexto autenticado del canal, nunca del nombre mostrado.
- El bridge usa un socket Unix `0600` y SQLite permanece fuera del sandbox.

Consulta [docs/openclaw-setup.md](docs/openclaw-setup.md) y adapta las plantillas de `config/openclaw/`. Los valores `<...>` son obligatorios y deben sustituirse por los del nuevo hogar; nunca subas la configuración real al repositorio.

## Dashboard web opcional

`deploy/hosted-dashboard/` contiene una versión alojable de solo lectura. Mantiene un único snapshot saneado en D1 y recibe actualizaciones mediante `POST /api/sync` con un secreto.

La copia web solo necesita:

- fechas y supermercados;
- cantidades e importes;
- nombres normalizados de productos y categorías.

No envía imágenes, OCR, usuarios, teléfonos, identificadores de WhatsApp, hashes de tickets ni metadatos de mensajería. La base SQLite local sigue siendo la fuente de verdad.

Pasos generales:

1. Crea un Site/Worker con un binding D1 llamado `DB`.
2. Define `SYNC_TOKEN` como variable secreta del servidor.
3. Guarda el mismo secreto en el llavero del Mac.
4. Ejecuta `tools/sync_hosted_dashboard.py` con la URL publicada.
5. Programa el comando con `launchd`, cron o el planificador que prefieras.

El dashboard alojado tiene su propia prueba:

```bash
cd deploy/hosted-dashboard
npm test
npm run build
```

## Privacidad y copias de seguridad

- `data/`, `receipts/`, logs, secretos y credenciales están excluidos de Git.
- Guarda una copia cifrada de `data/cesta.db` y del directorio de originales.
- No publiques el dashboard si contiene información que no quieras compartir con quien tenga la URL.
- Revisa [SECURITY.md](SECURITY.md) antes de conectar mensajería o acceso remoto.

## Pruebas

```bash
uv run pytest
cd integrations/openclaw-cesta
npm ci
npm test
npm run build
```

Los fixtures versionados son ejemplos desidentificados. Para añadir regresiones con tickets reales, guárdalos únicamente en una ruta ignorada por Git.

## Licencia

MIT. Consulta [LICENSE](LICENSE).
