# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend ----
FROM node:22-slim AS web-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev

# Land the built frontend at web/public/ -- the exact path
# export_web_b_data already writes its runtime output to (receipts.json,
# pdfs/), so that function needs zero changes to work inside the
# container. See docs/superpowers/specs/2026-08-25-docker-packaging-design.md.
# Stage 1's outDir is repo-root site/ (web/vite.config.ts), which from
# /app/web is /app/site.
COPY --from=web-build /app/site/ /app/web/public/

ENV XDG_DATA_HOME=/data
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" || exit 1

CMD ["receipt-radar", "serve", "--web-dir", "web", "--host", "0.0.0.0"]
