FROM node:22-bookworm-slim AS node

FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

COPY requirements.txt ./requirements.txt
COPY mobile/backend/requirements.txt ./mobile/backend/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r mobile/backend/requirements.txt

COPY music.py disk_cache.py services.py playlist_search.py ./
COPY mobile ./mobile

RUN useradd --system --uid 10001 --home /var/lib/ember ember \
    && mkdir -p /var/lib/ember/cache /var/lib/ember/library \
    && chown -R ember:ember /var/lib/ember

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HOME=/var/lib/ember \
    EMBER_BIND=0.0.0.0 \
    EMBER_PORT=8080 \
    EMBER_CACHE_DIR=/var/lib/ember/cache \
    EMBER_LIBRARY_DIR=/var/lib/ember/library

USER ember
EXPOSE 8080

CMD ["python", "-m", "mobile.backend"]
