# AGENTS.md

## What this is

yarr turns a YouTube URL into an MP3 with editable metadata and saves it into a
Plex music library. FastAPI backend + one vanilla-JS page (no framework, no
build step). Runs as a Docker container; default port 7734.

## Where things live

- app/main.py — FastAPI routes and job orchestration (fetch / preview / save)
- app/youtube.py — yt-dlp download (audio -> MP3 + thumbnail)
- app/chapters.py — chapter normalization + ffmpeg stream-copy split
- app/metadata.py — mutagen ID3 tag read/write + artist/title guessing
- app/library.py — destination path building + file placement
- app/jobs.py — in-memory Job / JobStore
- app/static/ — index.html, app.js, style.css (served as-is)
- Dockerfile, docker-compose.yml (local build), requirements.txt, entrypoint.sh
- .github/workflows/docker-publish.yml — multi-arch GHCR publish on push to main

## Run locally

    docker compose up -d --build    # http://localhost:7734

MUSIC_HOST_PATH in .env (gitignored) points the music mount. music/ is
gitignored for scratch saves.

## Gotchas

- No build step, but static files are COPYed into the image at build time.
  After editing app/static/\* you MUST `docker compose up -d --build`.
  A plain restart serves stale files.
- Container paths: WORKDIR is /app and the code is `COPY app ./app`, so files
  live at /app/app/... when you exec into the container to inspect.
- YouTube bot check: some videos (long compilations) need the JS challenge
  solver. Keep `yt-dlp-ejs` (requirements.txt) and the Deno install (Dockerfile)
  or those videos fail with "This video is not available".
- macOS Docker: do not bind-mount a host /tmp/... path for testing. Docker
  Desktop can bind to the Linux VM's /tmp instead of the Mac's, so files the
  container writes never appear on the host. Use a path under /Users (or the
  gitignored music/ dir).
- docker-compose.yml is the LOCAL build (build: ., image yarr:latest). Homelab
  deployments use `image: ghcr.io/parksjr/yarr:latest` with no build section
  (see README). Keep these straight: the local compose ignores a pulled GHCR
  image.
- JobStore is in-memory; jobs disappear on restart and staging dirs are only
  cleaned on a successful save, so abandoned downloads leak staging files.
- save_many and batch/save move files one at a time (validated up front). A
  mid-way failure can leave a partial save; it is not atomic.
- Frontend is stateful vanilla JS in app.js. Single / split / batch modes share
  one metadata form, so keep field IDs stable (f-artist, f-title, ...) and the
  sharedFieldMap mapping in sync when adding a field.

## Validate before committing

    python3.11 -m py_compile app/*.py     # code uses 3.10+ `X | None` syntax
    node --check app/static/app.js

No lint or test scripts exist in this repo. Manual/Playwright checks were done
ad hoc.

## Agent context

This repo may be reached from a parent workspace that holds several unrelated
apps and is sometimes driven by its own agent sessions. When that is the case:
yarr is its own git repo under yarr/, so scope git and code edits to yarr/
and never git init the parent. The parent also holds a
www.youtube.com_cookies.txt that yarr's local .env may reference.
