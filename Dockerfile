# syntax=docker/dockerfile:1

# 构建阶段：用 uv 官方镜像装依赖，pyproject/uv.lock 不变就复用缓存层。
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# 运行阶段：只带 .venv 和代码，不含 uv、ruff、构建缓存。
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system app \
    && useradd --system --gid app --create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /app /app
RUN mkdir -p /app/artifacts && chown app:app /app/artifacts

USER app
EXPOSE 8000

# 用标准库探活，镜像里不需要额外装 curl。
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["python", "day06_api.py", "--host", "0.0.0.0"]
