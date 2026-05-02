# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY apps/node_agent/pyproject.toml apps/node_agent/pyproject.toml
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY packages/shared/src packages/shared/src
COPY apps/node_agent/src apps/node_agent/src

RUN uv sync --frozen --no-dev --package gamehost-node


FROM python:3.12-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Add user to a group with the typical docker socket gid (999); the host can
# remap if needed by mounting the socket with a different group.
RUN groupadd -g 999 docker || true \
    && useradd --create-home --shell /usr/sbin/nologin --groups docker app
WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

USER app

EXPOSE 8080
CMD ["uvicorn", "gamehost_node.main:app", "--host", "0.0.0.0", "--port", "8080"]
