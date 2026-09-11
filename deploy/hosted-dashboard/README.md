# Dashboard alojado

Worker de solo lectura para visualizar un snapshot saneado de Cesta Inteligente.

- D1 binding: `DB`.
- Secreto de escritura: `SYNC_TOKEN`.
- Lectura: `GET /api/dashboard`.
- Salud: `GET /api/health`.
- Sincronización: `POST /api/sync` con `Authorization: Bearer <token>`.

El despliegue no incluye datos. La primera sincronización se realiza desde la base SQLite local con `../../tools/sync_hosted_dashboard.py`.

