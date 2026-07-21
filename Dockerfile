FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    REG_CONSOLE_HOST=0.0.0.0 \
    REG_CONSOLE_PORT=18080 \
    REG_CONSOLE_DATA_DIR=/app/data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY grok2api ./grok2api
COPY scripts ./scripts
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN mkdir -p /app/data && chown -R nobody:nogroup /app
USER nobody
EXPOSE 18080
HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=5 CMD curl -fsS http://127.0.0.1:18080/health || exit 1
CMD ["python", "-m", "app.main"]