# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# Install dependencies first for layer caching
COPY README.md pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1 \
    OVO_EXPORTER_PORT=9225

EXPOSE 9225

ENTRYPOINT ["uv", "run", "--no-sync", "ovo-prometheus"]
