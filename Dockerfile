# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      ca-certificates \
      curl \
      unzip \
 && rm -rf /var/lib/apt/lists/*

# yt-dlp needs a JS runtime to solve YouTube's bot-check JS challenges for
# some videos (e.g. long compilations). Deno is yt-dlp's default runtime.
ARG TARGETARCH
ENV DENO_VERSION=v2.9.7
RUN if [ "$TARGETARCH" = "arm64" ]; then DENO_ARCH="aarch64-unknown-linux-gnu"; else DENO_ARCH="x86_64-unknown-linux-gnu"; fi \
 && curl -fsSL "https://github.com/denoland/deno/releases/download/${DENO_VERSION}/deno-${DENO_ARCH}.zip" -o /tmp/deno.zip \
 && unzip -o /tmp/deno.zip -d /usr/local/bin \
 && chmod +x /usr/local/bin/deno \
 && rm /tmp/deno.zip \
 && deno --version

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PORT=7734 \
    HOST=0.0.0.0 \
    HOME=/data \
    MUSIC_LIBRARY_PATH=/music \
    STAGING_PATH=/data/staging \
    AUDIO_QUALITY=192 \
    PUID=1000 \
    PGID=1000

RUN mkdir -p /music /data/staging

EXPOSE 7734

ENTRYPOINT ["/entrypoint.sh"]
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7734} --proxy-headers"]
