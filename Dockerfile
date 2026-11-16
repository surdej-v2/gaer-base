# One image: the Vite frontend is built and served by the Python server.

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# Only typecheck + bundle here. `npm run build` also runs scripts/app-map.mjs --check, which needs the whole
# repo (server/, specs/, README.md, .gaer/); that check runs in the test workflow, which deploy depends on.
RUN npm run build:dist

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 STATIC_DIR=/app/web/dist
WORKDIR /app/server
COPY server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server/app ./app
COPY --from=web /web/dist /app/web/dist
# Zipped on request by /api/extension/download with the instance's address filled in
COPY extension /app/extension
ARG GAER_VERSION=dev
ENV GAER_VERSION=$GAER_VERSION
RUN useradd --system --uid 10001 gaer
USER gaer
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
