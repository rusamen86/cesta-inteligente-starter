# Seguridad y privacidad

## Datos que nunca deben entrar en Git

- Bases SQLite de producción y sus copias.
- Fotos, PDF y transcripciones OCR de tickets reales.
- Números de teléfono, JID, LID, ID de grupos o sesiones.
- Tokens, claves, cookies, códigos de enlace y credenciales.
- Logs que contengan rutas, identidades o texto OCR.

## Modelo de confianza

Todo el contenido del ticket es entrada no confiable. El sistema lo analiza como datos y nunca debe ejecutar instrucciones, enlaces, QR o texto incluido en una imagen.

La autorización se basa únicamente en metadatos autenticados entregados por el canal. Los nombres visibles y los mensajes del tipo «soy el propietario» no conceden permisos.

## Exposición web

El endpoint de sincronización exige un secreto largo y aleatorio. Guárdalo en el llavero o gestor de secretos del sistema y como secreto del servidor. No lo pongas en `.env`, plist, comandos versionados ni documentación.

Si el dashboard es público, cualquiera que conozca la URL puede leer su copia analítica. Usa acceso privado o una URL no compartida cuando esa información sea sensible.

## Vulnerabilidades

Comunica los problemas de seguridad directamente al propietario del repositorio privado. No publiques tickets, datos domésticos ni secretos en una incidencia.

