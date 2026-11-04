# Instalar Auction Watch en Home Assistant

Auction Watch es un add-on que vigila remates públicos y te avisa cuando
aparece algo que coincide con lo que buscás. Corre entero dentro de tu Home
Assistant: no manda tus datos a ningún servicio, no necesita cuenta y no
depende de nada más.

## Antes de empezar

- **Home Assistant OS o Supervised.** Los add-ons los instala Supervisor. Si
  usás Home Assistant Container o Core, no tenés store de add-ons; en ese caso
  andá a [Alternativa sin add-on](#alternativa-sin-add-on).
- **Arquitectura `amd64` o `aarch64`** (un mini PC, un NUC, una Raspberry Pi 4
  o 5 con el sistema de 64 bits). Las Pi viejas de 32 bits (`armv7`) no están
  soportadas.
- **Las fuentes son uruguayas:** Bavastro, Castells, Remotes, TodoRemates y
  Prado. Si no te interesan remates en Uruguay, el add-on va a funcionar pero
  no te va a encontrar nada útil.

## Instalación

1. En Home Assistant: **Settings → Add-ons → Add-on Store**. En las versiones
   más nuevas el menú se llama **Apps** en vez de Add-ons; es lo mismo.
2. Arriba a la derecha, menú **⋮ → Repositories**.
3. Pegá esta URL y dale **Add**:

   ```
   https://github.com/Pisnaquio/auction-watch-ha-addon
   ```

4. Cerrá el diálogo. Bajá hasta la sección **Auction Watch Home Assistant** y
   abrí el add-on **Auction Watch**.
5. **Install.** La primera vez compila la imagen en tu equipo, así que puede
   tardar varios minutos.

El add-on se publica en etapa **Experimental** y Home Assistant lo muestra con
esa etiqueta. No esconde nada ni bloquea la instalación: es solo la madurez
declarada del proyecto.

## Configuración

En la pestaña **Configuration** del add-on:

| Opción | Qué hace |
| --- | --- |
| `timezone` | Zona horaria IANA, por ejemplo `America/Montevideo`. Define a qué hora se interpretan los cierres de los remates. |
| `scheduler_enabled` | Viene en `true`: escanea solo según la frecuencia de cada perfil. Si querés armar los perfiles con calma antes de que empiece a buscar, ponelo en `false` y volvelo a `true` al terminar. |
| `worker_poll_seconds` | Dejalo como viene (`0.5`). |
| `smtp_*` | Avisos por mail. Opcional y apagado por defecto; ver abajo. |

Guardá y **reiniciá el add-on**. Las migraciones de base corren solas antes de
que arranque la web.

No hace falta abrir ningún puerto: el panel va por ingress.

### Avisos por mail (opcional)

Si querés que te escriba cuando encuentra algo, poné `smtp_enabled: true` y
completá los datos de tu servidor de correo:

- `smtp_host`, `smtp_port` (`587` con TLS es lo habitual)
- `smtp_sender` — de qué dirección sale el mail
- `smtp_recipient` — a dónde te llega
- `smtp_username`, `smtp_password` — credenciales de tu proveedor
- `smtp_use_tls: true`

Con Gmail necesitás una **contraseña de aplicación**, no la de tu cuenta.

Los avisos salen solo por hallazgos nuevos o cambios relevantes, nunca un mail
de "no encontré nada". La cola de envío es durable: si el mail falla, reintenta
con backoff y te lo marca en el historial.

## Primer uso

Abrí **Auction Watch** en la barra lateral de Home Assistant.

Viene con un perfil de ejemplo llamado **Auction Watch Consolas**, que busca
consolas y juegos retro. Ese perfil está protegido: podés correrlo, pausarlo y
**clonarlo**, pero no editarlo. La copia clonada ya es tuya y la editás libre.

Para armar lo tuyo, creá un perfil nuevo y definí:

- **Keywords** `any` (alcanza con una) o `all` (tienen que estar todas)
- **Frases** exactas y **exclusiones** para sacar ruido
- **Fuentes** y **categorías** donde buscar
- **Límites de precio**, **frecuencia** de escaneo y **notificaciones**

Si el primer perfil te trae mucho ruido, lo normal es ir agregando exclusiones
en vez de acortar las keywords.

## Actualizaciones

En la página del add-on, activá el switch **Auto update** y listo: Supervisor
aplica cada versión nueva por su cuenta.

Si preferís a mano: **⋮ → Check for updates** en el store y después **Update**.

Actualizar no toca tus datos. Perfiles, oportunidades, descartes, historial y
cola de mails viven en el almacenamiento persistente del add-on y sobreviven
cualquier actualización.

## Backups

El add-on está marcado como `backup: cold`, así que entra en los backups de
Home Assistant como cualquier otro. Un backup parcial del add-on Auction Watch
alcanza para guardar tus perfiles y tu historial.

## Si algo no anda

- **El add-on no arranca:** mirá la pestaña **Log** del add-on. Los errores de
  arranque más comunes son una `timezone` inválida o un reinicio quedado a
  mitad de una migración.
- **Una fuente falla:** la corrida queda marcada como parcial y **no** borra
  las oportunidades que ya tenías. No hace falta hacer nada; se recupera en la
  corrida siguiente.
- **No llegan los mails:** revisá host, puerto y destinatario. El add-on nunca
  imprime tus credenciales en el log, así que podés compartir el log para pedir
  ayuda sin exponer nada.
- **No aparece nada:** confirmá que el perfil está habilitado, que
  `scheduler_enabled` está en `true` y corré el perfil a mano una vez desde la
  pantalla de perfiles.

Cuando pidas ayuda, mandá el log del add-on. **No** adjuntes `options.json` ni
tus variables de entorno: ahí están tus credenciales de SMTP.

## Alternativa sin add-on

Si no tenés Supervisor, podés correrlo con Docker desde el repositorio del
proyecto, [`Pisnaquio/auction-watch`](https://github.com/Pisnaquio/auction-watch):

```bash
git clone https://github.com/Pisnaquio/auction-watch.git
cd auction-watch
docker compose up --build
```

Queda en <http://localhost:8789> y guarda los datos en el volumen
`auction-watch-data`. No tenés el panel integrado en la barra lateral de Home
Assistant, pero la aplicación es la misma.

Por este camino el scheduler arranca apagado y la configuración no va por
Supervisor, así que lo que en el add-on son opciones acá son variables de
entorno en `docker-compose.yml`:

```yaml
environment:
  AW_SCHEDULER_ENABLED: "true"
  AW_TIMEZONE: America/Montevideo
```

Si no agregás `AW_SCHEDULER_ENABLED: "true"`, los perfiles solo corren cuando
los disparás a mano desde la pantalla de perfiles. Los avisos por mail se
configuran igual, con `AW_SMTP_ENABLED`, `AW_SMTP_HOST`, `AW_SMTP_PORT`,
`AW_SMTP_SENDER`, `AW_SMTP_RECIPIENT`, `AW_SMTP_USERNAME`, `AW_SMTP_PASSWORD` y
`AW_SMTP_USE_TLS`.
