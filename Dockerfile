FROM node:22-alpine AS frontend-build
WORKDIR /build/web
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AW_DATA_DIR=/data \
    AW_HOST=0.0.0.0 \
    AW_PORT=8789 \
    AW_WEB_DIST=/app/web/dist
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY --from=frontend-build /build/web/dist /app/web/dist
COPY alembic.ini ./
COPY migrations ./migrations
RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /data \
    && chown -R app:app /app /data
USER app
VOLUME ["/data"]
EXPOSE 8789
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8789/api/v1/health', timeout=3)"
CMD ["uvicorn", "auction_watch.main:app", "--host", "0.0.0.0", "--port", "8789"]
