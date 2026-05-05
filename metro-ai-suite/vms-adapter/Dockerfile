FROM python:3.13-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN groupadd -g 1000 appuser && useradd -u 1000 -g appuser -m appuser

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY plugin ./plugin
COPY vms_shim ./vms_shim
COPY core_app_sim ./core_app_sim
COPY alembic ./alembic

RUN uv pip install --system --no-cache -e . && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

ENTRYPOINT ["sh", "-c", "alembic upgrade head && uvicorn plugin.core.main:app --host 0.0.0.0 --port 8080 --workers 1"]
