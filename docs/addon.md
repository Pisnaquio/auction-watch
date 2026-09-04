# Auction Watch como add-on de Home Assistant

Auction Watch es una aplicación independiente. No importa, ejecuta ni lee
Consolas, su add-on, sus watchlists ni su estado. El add-on usa únicamente el
almacenamiento persistente `/data/auction-watch`.

## Instalación y configuración inicial

1. Agregá el repositorio de add-ons de Auction Watch en Supervisor.
2. Instalá **Auction Watch** y abrí su panel mediante ingress.
3. En la configuración inicial mantené `scheduler_enabled: false` y
   `smtp_enabled: false` hasta haber creado y revisado un perfil.
4. Elegí una zona horaria IANA válida. El host y el puerto internos son
   administrados por el add-on; no hace falta publicar un puerto del host.
5. Guardá la configuración y reiniciá el add-on. Supervisor ejecuta las
   migraciones antes del proceso web.

La opción `worker_enabled` no se expone como configuración del usuario. El
bootstrap del add-on establece `AW_WORKER_ENABLED=true` sólo dentro del
contenedor; el default del proyecto y las ejecuciones locales siguen siendo
seguros y no ejecutan trabajos en segundo plano.

## Actualizaciones

El add-on se distribuye desde
[`Pisnaquio/auction-watch-ha-addon`](https://github.com/Pisnaquio/auction-watch-ha-addon),
que es el repositorio a agregar en Supervisor. Ofrece una versión nueva sólo
cuando cambia `version:` en el `config.yaml` de ese espejo. Con "Auto update"
activado en la página del add-on, la aplica solo; si no, **Check for updates →
Update** en el store, o `scripts/ha_update.sh` por SSH. El proceso completo
está en [RELEASE.md](RELEASE.md).

## Perfiles

La pantalla de perfiles permite crear búsquedas personalizadas para libros,
discos, mesas de pool o cualquier combinación de términos. Se pueden editar
keywords `any`/`all`, frases, exclusiones, boosts, reglas contextuales,
fuentes, categorías, límites de precio, frecuencia y notificaciones.

**Auction Watch Consolas** es el perfil seed protegido. Puede pausarse,
ejecutarse y clonarse, pero no editarse ni eliminarse. Una copia clonada es un
perfil de usuario independiente.

## Backups y restauración

El backup debe incluir sólo `/data/auction-watch`; no es necesario ni seguro
copiar el resto de `/data`. Como alternativa a un backup de Supervisor, los
scripts locales `scripts/backup_addon_data.sh` y
`scripts/restore_addon_data.sh` aceptan un archivo tar comprimido. La
restauración debe hacerse con el add-on detenido y luego iniciarse para que las
migraciones idempotentes verifiquen la base.

Una actualización de la imagen no reinstala ni sobrescribe la base, perfiles,
oportunidades, descartes, historial ni outbox: todo eso vive en el volumen de
datos persistente.

## Notificaciones

SMTP es opcional y está desactivado por defecto. Las notificaciones se
encolan en una outbox durable y se deduplican lógicamente por corrida, perfil
y canal. Sólo se generan por hallazgos nuevos, cambios relevantes o fallos
según el modo del perfil. Nunca se envía “cero resultados” cuando la cobertura
no es autoritativa. Los reintentos son acotados y usan backoff; el historial
de entrega distingue pendiente, enviado y fallido.

Las credenciales configuradas en Supervisor son sensibles. No deben solicitarse
ni copiarse en diagnósticos, logs, health/readiness, capturas o salidas raw.

## Troubleshooting seguro

- Si readiness no está disponible, revisá el estado del add-on y el log
  saneado de Supervisor; no adjuntes `options.json`, `.env` ni variables de
  entorno.
- Si una migración falla, detené el add-on, conservá el backup de
  `/data/auction-watch` y revisá sólo el tipo de error y la revisión indicada.
- Si un origen falla, la corrida queda parcial/fallida y no elimina
  oportunidades anteriores a partir de una respuesta no autoritativa.
- Si SMTP falla, confirmá host, puerto y destinatario sin revelar usuario,
  password ni tokens. El transporte nunca imprime el contenido completo de la
  configuración.

El add-on no ejecuta scans reales durante la instalación, no envía correo por
defecto y no depende de Consolas.
