
FROM python:3.12-slim AS deps

COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

FROM python:3.12-slim AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app
WORKDIR /app
RUN mkdir -p /app/logs /app/.files && touch /app/chainlit.md \
 && chmod 1777 /app/logs /app/.files && chmod 666 /app/chainlit.md

COPY --from=deps --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src/ ./src/
COPY --chown=app:app scripts/ ./scripts/
COPY --chown=app:app eval/ ./eval/
COPY --chown=app:app .chainlit/ ./.chainlit/
COPY --chown=app:app public/ ./public/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    HF_HOME=/models \
    HF_TOKEN_PATH=/tmp/no-hf-token

USER app
EXPOSE 8020

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8020/healthz || exit 1

CMD ["uvicorn", "fin_assistant.api.main:app", "--host", "0.0.0.0", "--port", "8020"]
