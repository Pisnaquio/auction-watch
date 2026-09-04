# Releases y deploy a Home Assistant

El add-on instalado en Home Assistant es un *repository add-on*: Supervisor lee
este repo de GitHub directamente y construye la imagen desde `Dockerfile` +
`config.yaml`. Sólo considera que hay una versión nueva cuando cambia
`version:` en `config.yaml`. Por eso un release es siempre: **bump de versión →
merge a `main` → tag `vX.Y.Z`**, y el tag dispara el pipeline.

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

## Qué hace el pipeline (`.github/workflows/release.yml`)

Se dispara con el push de un tag `vX.Y.Z`:

1. `verify`: el commit taggeado tiene que estar en `main`; el tag, los cinco
   archivos de versión y la sección del CHANGELOG tienen que coincidir.
2. `checks`: reutiliza el CI completo (`ruff`, `mypy`, `pytest`, `npm test`,
   `npm run build`, `docker build`).
3. `release`: `check_public_safety.py`, empaqueta con `package_addon.sh`,
   audita el tarball con `audit_addon_artifact.py` y publica un GitHub Release
   `vX.Y.Z` con las notas del CHANGELOG y el tarball adjunto.
4. `deploy-ha` (opcional, ver abajo).

Si cualquier paso falla no se publica nada y Supervisor no ve la versión.

## Cómo llega a Home Assistant

GitHub Actions corre en la nube y tu Home Assistant está en la LAN, sin
exposición externa, así que el pipeline no puede empujar el deploy por sí solo.
Hay tres caminos, de menor a mayor infraestructura:

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
