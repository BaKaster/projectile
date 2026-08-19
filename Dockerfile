# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        g++ \
        libarchive-tools \
        libgl1 \
        libglib2.0-0 \
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

ENV HF_HOME=/home/appuser/.cache/huggingface

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/storage /home/appuser/.cache \
    && chown -R appuser:appuser /app \
    && chown -R appuser:appuser /home/appuser/.cache

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
