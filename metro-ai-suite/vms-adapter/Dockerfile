# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN groupadd -g 1000 appuser && useradd -u 1000 -g appuser -m appuser

WORKDIR /app

COPY pyproject.toml ./
COPY plugin ./plugin
COPY vms_shim ./vms_shim
COPY core_app_shim ./core_app_shim

RUN uv pip install --system --no-cache -e . && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

ENTRYPOINT ["uvicorn", "plugin.core.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
