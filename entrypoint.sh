#!/bin/sh
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
STAGING_PATH="${STAGING_PATH:-/data/staging}"

mkdir -p "$STAGING_PATH"

if [ "$(id -u)" = "0" ] && [ "$PUID" != "0" ] && command -v setpriv >/dev/null 2>&1; then
  chown -R "$PUID:$PGID" /data 2>/dev/null || true
  exec setpriv --reuid "$PUID" --regid "$PGID" --clear-groups -- "$@"
fi

exec "$@"
