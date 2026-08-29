ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12

FROM node:22-alpine AS frontend-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts
COPY web/index.html web/tsconfig.json web/vite.config.ts ./
COPY web/src ./src
RUN npm run build

FROM ${BUILD_FROM} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AW_DATA_DIR=/data/auction-watch \
    AW_HOST=127.0.0.1 \
    AW_PORT=8789 \
    AW_WEB_DIST=/opt/auction-watch/web/dist \
    AW_WORKER_ENABLED=true \
    AW_SCHEDULER_ENABLED=false \
    AW_SMTP_ENABLED=false
WORKDIR /opt/auction-watch
COPY pyproject.toml README.md /build/app/
COPY src /build/app/src
RUN pip install --no-cache-dir /build/app
COPY --from=frontend-build /build/web/dist /opt/auction-watch/web/dist
COPY rootfs /
RUN mkdir -p /data/auction-watch
VOLUME ["/data"]
EXPOSE 8789
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8789/api/v1/readiness', timeout=3)"
CMD ["/init"]
