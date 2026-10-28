# AIRA on Meta backend: FastAPI + uvicorn, built with uv.
# Build context is backend/:   docker build -t meta-aira backend/

# ---- builder: resolve and install dependencies into /app/.venv ----
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first (cached layer). The uv.lock* glob tolerates a missing lock file.
COPY pyproject.toml uv.lock* ./
RUN if [ -f uv.lock ]; then \
        uv sync --frozen --no-dev --no-install-project; \
    else \
        uv sync --no-dev --no-install-project; \
    fi

# Then the application itself (installed non-editable so the venv is self-contained).
COPY aira ./aira
RUN if [ -f uv.lock ]; then \
        uv sync --frozen --no-dev --no-editable; \
    else \
        uv sync --no-dev --no-editable; \
    fi

# ---- runtime: no uv, no build tools, non-root ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8080

RUN groupadd --system --gid 10001 aira \
 && useradd --system --uid 10001 --gid aira --no-create-home --shell /usr/sbin/nologin aira

WORKDIR /app
COPY --from=builder --chown=aira:aira /app/.venv /app/.venv
COPY --from=builder --chown=aira:aira /app/aira /app/aira

USER aira
EXPOSE 8080

# Shell form so ${PORT} (set by Cloud Run) expands; exec so uvicorn receives SIGTERM directly.
CMD ["sh", "-c", "exec uvicorn aira.main:app --host 0.0.0.0 --port ${PORT:-8080} --ws websockets --proxy-headers --forwarded-allow-ips='*'"]
