# Home Assistant packaging

The formal add-on metadata now lives at the repository root in `config.yaml`,
with the image definition in `Dockerfile` and supervised services under
`rootfs/`. This directory remains a short pointer for repository browsers.

See [../../docs/addon.md](../../docs/addon.md) for installation, configuration,
backup/restoration, notification guarantees, and safe troubleshooting. The
add-on is independent of Consolas and persists only under
`/data/auction-watch`.
