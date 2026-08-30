# Changelog

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

This release has not been published or deployed.  A release still requires a
maintainer review of the target Home Assistant base images and a supervised
installation test in a disposable environment.
