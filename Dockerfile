FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock requirements-s3.txt ./
RUN uv sync --frozen --no-dev \
    && uv pip install --python /app/.venv/bin/python -r requirements-s3.txt

COPY alembic.ini config.yaml ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts

RUN chmod +x scripts/*.sh \
    && mkdir -p /app/data/users \
    && chown -R 10001:10001 /app

ENV PATH="/app/.venv/bin:$PATH"

USER 10001:10001

EXPOSE 8765

CMD ["./scripts/start-web.sh"]
