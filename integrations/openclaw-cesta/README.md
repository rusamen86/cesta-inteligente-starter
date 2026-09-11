# cesta-local

Plugin local y privilegiado del Gateway para Cesta Inteligente.

- No ejecuta comandos ni procesos.
- No recibe rutas de medios del modelo.
- El hook trusted de entrada acepta únicamente medios locales dentro de `inboundMediaRoot`, los
  convierte en referencias relativas y deja que el bridge vuelva a validar ruta, symlinks y tipo.
- Solo materializa seis tools cuando agente, sandbox, canal, sesión, grupo y remitente coinciden.
- Se comunica con el bridge Python mediante un socket Unix configurado por el operador.
- Todavía no está instalado ni registrado en OpenClaw.
