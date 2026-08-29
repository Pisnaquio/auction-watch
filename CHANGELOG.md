# Changelog

## 0.1.0 — proposed

- First independent Home Assistant add-on packaging for Auction Watch.
- Ingress-only web/API service with idempotent migrations under
  `/data/auction-watch`.
- Recoverable run worker, scheduler opt-in, and bounded notification outbox
  delivery.
- Safe installation defaults: no scheduled scans and no SMTP delivery.
- Artifact packaging and private-data audit scripts.

This release has not been published or deployed.  A release still requires a
maintainer review of the target Home Assistant base images and a supervised
installation test in a disposable environment.
