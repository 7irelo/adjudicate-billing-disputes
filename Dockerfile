FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.11.23

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra ui

COPY src ./src
COPY evals ./evals
COPY data ./data
COPY corpus ./corpus
COPY db ./db
RUN uv sync --frozen --no-dev --extra ui

ENV PATH="/app/.venv/bin:$PATH"
RUN mkdir /shared && chown nobody /shared
USER nobody
ENV HOME=/tmp
