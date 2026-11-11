# syntax=docker/dockerfile:1

FROM node:22.21.0-bookworm-slim AS ui
WORKDIR /src
COPY --link frontend/package.json frontend/package-lock.json ./
RUN --mount=type=secret,id=npmrc,target=/root/.npmrc,required=false \
    --mount=type=cache,target=/root/.npm \
    npm ci
COPY --link frontend/ ./
RUN npm run build

FROM python:3.11.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LIMINALGATE_HOST=0.0.0.0 \
    LIMINALGATE_PORT=8080 \
    LIMINALGATE_DATA_DIR=/data

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      libgomp1 \
      libdbus-1-3 \
      libusb-1.0-0 \
      libglib2.0-0 \
      alsa-utils

WORKDIR /app
COPY --link requirements.txt requirements-optional.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt -r requirements-optional.txt

COPY --link liminalgate ./liminalgate
COPY --link --from=ui /src/dist ./frontend/dist

RUN useradd --system --uid 1000 --create-home --home-dir /home/liminal liminal \
    && mkdir -p /data \
    && chown -R liminal:liminal /data /app

USER liminal
VOLUME /data
EXPOSE 8080/tcp
EXPOSE 5000/udp
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=2).read()"

CMD ["python", "-m", "liminalgate"]
