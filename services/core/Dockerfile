# Build from services/core:  docker build -t hs-core --build-arg GIT_SHA=$(git rev-parse --short HEAD) .
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src

FROM python:3.12-slim AS runtime
ARG GIT_SHA=unknown
ARG APP_VERSION=0.1.0
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/src PYTHONUNBUFFERED=1 \
    HS_ENV=production GIT_SHA=${GIT_SHA} APP_VERSION=${APP_VERSION}
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
USER app
EXPOSE 8080
# Secrets (APPWRITE_API_KEY etc.) are injected at runtime (fly secrets), never baked in.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "--factory", "hs_core.main:app_factory", "--host", "0.0.0.0", "--port", "8080"]
