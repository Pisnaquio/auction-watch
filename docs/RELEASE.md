# Releases y deploy a Home Assistant

**Supervisor no lee este repositorio.** Lee
[`Pisnaquio/auction-watch-ha-addon`](https://github.com/Pisnaquio/auction-watch-ha-addon),
un repo de distribución aparte cuyo directorio `auctionwatch/` es un espejo del
add-on. Ese es el que está registrado como repositorio de add-ons en Home
Assistant (slug `9b3464ac`), y construye la imagen desde `Dockerfile` +
`config.yaml`. Supervisor sólo ofrece una versión nueva cuando cambia
`version:` en el `config.yaml` **del espejo**.

Un release es entonces: **bump de versión → merge a `main` → tag `vX.Y.Z` →
publicar el espejo → aplicar en Home Assistant**. El tag dispara el pipeline de
verificación y empaquetado; publicar el espejo es lo que hace la versión
instalable.

## Procedimiento

1. Escribir la entrada `## X.Y.Z` en `CHANGELOG.md` (el tag se niega a salir
   sin ella; su contenido son las notas del GitHub Release).
2. Bumpear la versión en todos los archivos a la vez:

   ```bash
   ./scripts/bump_version.sh X.Y.Z
   ```

   Actualiza `pyproject.toml`, `config.yaml`, `src/auction_watch/__init__.py`,
   `web/package.json` y `web/package-lock.json`, y verifica que quedaron iguales.
3. Abrir un PR con el bump + CHANGELOG y mergearlo a `main` (`main` está
   protegida; CI corre `check_release_version.py`, así que un bump a medias
   no pasa).
4. Desde `main` actualizado, cortar el tag:

   ```bash
   ./scripts/tag_release.sh
   ```

   Verifica rama, árbol limpio, sincronía con `origin/main`, consistencia de
   versión y CHANGELOG, y que el tag no exista; recién ahí crea `vX.Y.Z` y lo
   pushea. `--dry-run` sólo corre los chequeos.
5. Publicar el espejo en el repo de distribución:

   ```bash
   ./scripts/publish_addon_repo.sh vX.Y.Z          # abre el PR
   ./scripts/publish_addon_repo.sh vX.Y.Z --merge  # lo abre y lo mergea
   ```

   Reemplaza `auctionwatch/` con el árbol del tag (así también se propagan los
   borrados), quita el `repository.yaml` de la app —el repo de distribución
   publica el suyo— y abre un PR `release: publish Auction Watch X.Y.Z` con las
   notas del CHANGELOG. `--dry-run` muestra el diff sin tocar nada. **Hasta que
   este PR se mergea, Home Assistant no ve la versión.**
6. Aplicarlo en Home Assistant (ver más abajo).

## Qué hace el pipeline (`.github/workflows/release.yml`)

Se dispara con el push de un tag `vX.Y.Z`:

1. `verify`: el commit taggeado tiene que estar en `main`; el tag, los cinco
   archivos de versión y la sección del CHANGELOG tienen que coincidir.
2. `checks`: reutiliza el CI completo (`ruff`, `mypy`, `pytest`, `npm test`,
   `npm run build`, `docker build`).
3. `release`: `check_public_safety.py`, empaqueta con `package_addon.sh`,
   audita el tarball con `audit_addon_artifact.py` y publica un GitHub Release
   `vX.Y.Z` con las notas del CHANGELOG y el tarball adjunto.
4. `publish-addon-repo` (opcional): corre `publish_addon_repo.sh` contra el repo
   de distribución. Requiere un token con permiso de escritura sobre
   `auction-watch-ha-addon` en el secret `AW_ADDON_REPO_TOKEN` y la variable de
   repositorio `AW_PUBLISH_ADDON_REPO=true`; sin eso el job se saltea y el paso
   5 del procedimiento se corre a mano.
5. `deploy-ha` (opcional, ver abajo).

Si cualquier paso falla no se publica nada y Supervisor no ve la versión.

## Cómo llega a Home Assistant

Una vez publicado el espejo (paso 5 del procedimiento), falta que Supervisor
aplique la versión. GitHub Actions corre en la nube y tu Home Assistant está en
la LAN, sin exposición externa, así que el pipeline no puede empujar el deploy
por sí solo. Hay tres caminos, de menor a mayor infraestructura:

**A. Auto-update de Supervisor (recomendado, sin infraestructura).** Una sola
vez, activá el auto-update del add-on:

```bash
./scripts/ha_update.sh --enable-auto-update
```

(o el switch "Auto update" en la página del add-on). A partir de ahí Supervisor
aplica cada release en su próximo ciclo de refresco del store, sin intervención.
La latencia es la del ciclo de Supervisor, no la del pipeline.

**B. Deploy inmediato por SSH.** Cuando el workflow del tag terminó en verde:

```bash
./scripts/ha_update.sh X.Y.Z
```

Recarga el store, corre `ha apps update --backup` y espera hasta que el add-on
reporte `X.Y.Z` y `started`. Usa el alias SSH `homeassistant`
(`AW_HA_SSH_HOST` para cambiarlo). Si Supervisor todavía no ofrece esa versión,
el script lo dice y no hace nada.

**C. Job `deploy-ha` sincrónico.** Registrá un runner self-hosted de GitHub en
una máquina de la LAN con acceso SSH al host (por ejemplo la Mac) y definí la
variable de repositorio `AW_HA_DEPLOY=true`. El mismo workflow corre entonces
`ha_update.sh` al final y el deploy queda en el log del release. Sin runner,
el job se salta.

## Verificar y volver atrás

- Estado en vivo: `ssh homeassistant "ha apps info 9b3464ac_auctionwatch"`
  (`version`, `state`, `auto_update`).
- El add-on guarda todo bajo `/data/auction-watch`, que sobrevive
  actualizaciones; `ha apps update --backup` deja además un backup parcial.
- Supervisor no puede instalar una versión específica anterior. Para volver
  atrás: revertir el commit en `main`, bumpear a una versión *nueva* (por
  ejemplo `X.Y.Z+1`) y taggear de nuevo.

## Sólo desarrollo: reconstruir sin bump

`./scripts/ha_update.sh X.Y.Z --rebuild` reconstruye la imagen desde el estado
actual del repo en Supervisor aunque la versión no haya cambiado. Sirve para
probar en vivo, no reemplaza un release.
