# Changelog

## Release and deployment

El proceso de publicación, actualización y verificación del add-on está en
[docs/RELEASE.md](docs/RELEASE.md). El estado vivo debe consultarse en el
Supervisor; no se mantiene aquí porque cambia entre releases.

## 0.1.17

- Fija la barra lateral a la altura de la ventana: deja de cortarse al
  desplazar formularios largos y el cambio de perfil queda siempre alcanzable.
- Agrupa los criterios sueltos en secciones con título e ícono («Términos de
  búsqueda» y «Alcance»), en línea con las secciones de fuentes y precio.
- Reemplaza el cuadrado de puntaje de cada oportunidad por un anillo de
  progreso calculado sobre el puntaje real, y tacha el título de las
  descartadas.
- Agrega sobre el espacio de trabajo una franja con la última corrida, la
  próxima corrida programada y las notificaciones, construida con datos que la
  interfaz ya tenía y sin endpoints nuevos.
- Convierte la barra lateral en una barra inferior en pantallas angostas, en
  lugar de apilar los perfiles como fichas.
- Incorpora el pipeline de release por tag y las herramientas de versionado
  (`bump_version.sh`, `tag_release.sh`, `ha_update.sh`); ver
  [docs/RELEASE.md](docs/RELEASE.md).

## 0.1.16

- Mantiene el texto crudo de los criterios mientras se edita, permitiendo
  espacios, frases y comas finales sin que el campo reescriba cada tecla.
- Convierte los valores separados por comas recién al validar o guardar y
  agrega pruebas del flujo con frases compuestas, acentos y separadores vacíos.

## 0.1.15

- Reemplaza las consultas SQLite por lote durante reconciliación por una
  precarga transaccional constante de inventario y ciclo de vida por grupo.
- Mantiene exactamente las mismas reglas fail-closed y agrega una regresión de
  250 lotes que limita la reconciliación a cuatro lecturas SQL.

## 0.1.14

- Agrega un decoder estructural acotado para recuperar automáticamente
  envelopes nuevos de Castells sólo cuando existe un único candidato con IDs y
  títulos estables.
- Separa HTML, payloads de error, envelopes ambiguos, vacíos no verificables y
  cambios en la forma de los lotes en causas de drift específicas.
- Publica fingerprints exclusivamente estructurales y mantiene los casos de
  confianza media o baja en modo sombra, sin inventario, mail ni autoridad.
- Conserva paginación, límites de profundidad/nodos y validación fail-closed.
- Evita que una omisión transitoria, un grupo vacío o una caída superior al
  75% desactive el último inventario sano de Castells; esos casos quedan
  `partial` y diagnosticados.
- Limita Castells a 8 segundos por solicitud y 60 segundos por corrida, sin
  agregar consultas de recuperación en segundo plano.

## 0.1.13

- Omite antes de paginar los remates de Castells cuyo título identifica de
  forma inequívoca inventario exclusivamente artístico.
- Mantiene remates mixtos o ambiguos dentro de la consulta y registra cada
  descarte como `skipped_irrelevant` en el snapshot, sin receipts ficticios.
- Conserva el estado `partial` cuando cualquier grupo potencialmente relevante
  falla, aunque otros grupos hayan sido descartados correctamente.

## 0.1.12

- Programa el perfil protegido Consolas todos los días a las 09:00 en
  `America/Montevideo`, con alertas ante hallazgos o fallos.
- Limita la recuperación automática a 15 minutos desde cada horario y cuenta
  snapshots parciales válidos como cobertura del slot, evitando duplicados.
- Expone en la UI el estado no sensible del worker y scheduler, y separa el
  interruptor de automatización de los horarios editables en perfiles propios.

## 0.1.11

- Mejora la cobertura de Castells con paginación acotada, menor concurrencia,
  conservación de páginas válidas y causas agregadas por categoría.
- Separa advertencias opcionales de precio/moneda de los fallos reales de
  cobertura y mantiene los resultados sanos ante grupos parciales.
- Agrega la guía “Cómo buscar mejor” en la interfaz y en Markdown, junto con
  advertencias no destructivas antes de guardar criterios demasiado cerrados.

## 0.1.10

- Corrige la persistencia secuencial de todas las fuentes después de
  consultarlas en paralelo, antes de construir el snapshot.
- Agrega una regresión determinista de dos fuentes que exige ejecución,
  persistencia y snapshot completos.

## 0.1.9

- Consulta las fuentes en paralelo con un transporte aislado por fuente y
  mantiene las escrituras de SQLite en orden determinista.
- Resume errores repetidos de lotes de Castells por grupo, sin ocultar la
  cantidad real en el receipt ni llenar el snapshot con miles de copias.

## 0.1.8

- Una cobertura fallida de Castells ya no invalida la corrida completa: conserva
  el inventario previo del remate afectado y publica el resto como parcial.
- El perfil protegido Consolas se actualiza a criterios de consola correctos,
  sin pisar sus opciones operativas de pausa, agenda o notificaciones.
- Agrega una prueba local completa de API, cola, worker y snapshot, más un
  diagnóstico de fuentes que no persiste publicaciones ni envía correo.
- La UI muestra el endpoint y el campo rechazado cuando la API devuelve 422.

## 0.1.7

- Exige contexto de videojuegos para términos ambiguos del perfil Consolas como
  `mario`, `family`, `ds` y `switch`.
- Migra automáticamente el perfil protegido a la segunda versión del seed.

## 0.1.6

- Deduplica remates y lotes repetidos de Castells antes de reconciliar el inventario.
- Marca como parcial y no autoritativa cualquier identidad duplicada con datos conflictivos.
- Rechaza resultados estructuralmente inválidos de una fuente sin derribar las demás.

## 0.1.5

- Conserva `Content-Type: application/json` al enviar la clave de idempotencia y evita estados visuales contradictorios ante rechazos.

## 0.1.4

- Normaliza la doble barra que Supervisor antepone a rutas Ingress.

## 0.1.3

- Soporta el prefijo de Ingress reenviado sin cabecera auxiliar por Supervisor.

## 0.1.2

- Normaliza el prefijo reenviado por Home Assistant Ingress para servir UI y API.

## 0.1.1

- Corrige el arranque bajo s6-overlay y la operación completa mediante Home Assistant Ingress.

## 0.1.0

- First independent Home Assistant add-on packaging for Auction Watch.
- Ingress-only web/API service with idempotent migrations under
  `/data/auction-watch`.
- Recoverable run worker, scheduler opt-in, and bounded notification outbox
  delivery.
- Safe installation defaults: no scheduled scans and no SMTP delivery.
- Artifact packaging and private-data audit scripts.
