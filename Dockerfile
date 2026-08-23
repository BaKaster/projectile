# syntax=docker/dockerfile:1.7
FROM node:22-slim AS codex-cli

ARG CODEX_CLI_VERSION=0.149.0
RUN npm install --global "@openai/codex@${CODEX_CLI_VERSION}"

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY --from=codex-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=codex-cli /usr/local/bin/codex /usr/local/bin/codex
COPY --from=codex-cli /usr/local/lib/node_modules/@openai/codex /usr/local/lib/node_modules/@openai/codex

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        g++ \
        libarchive-tools \
        libgl1 \
        libglib2.0-0 \
        libreoffice-calc \
        default-jre-headless \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app/__init__.py ./app/__init__.py

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install \
        torch torchvision \
        --index-url https://download.pytorch.org/whl/cpu

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ".[recognition]"

# Application code changes no longer invalidate the heavyweight ML dependency layers.
COPY app ./app
COPY data ./data
COPY ["Универсальный_расчет_стоимости_MONSters_v2_упрощенный.xlsx", "/app/data/estimate-template.xlsx"]

ENV HF_HOME=/home/appuser/.cache/huggingface

RUN rm -f /usr/local/bin/codex \
    && ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/storage /home/appuser/.cache /home/appuser/.codex \
    && chown -R appuser:appuser /app \
    && chown -R appuser:appuser /home/appuser/.cache /home/appuser/.codex

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
