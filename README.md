# yarr

Paste a YouTube URL, get an MP3 with editable metadata, and save it into your
Plex music library with the right `Artist/Album/Track - Title.mp3` folder
structure. Runs as a Docker container next to your other home-lab apps.

## What it does

1. Downloads the audio from a single YouTube video with `yt-dlp`.
2. Converts it to MP3 with `ffmpeg` (default 192 kbps).
3. Embeds the cover art and any metadata YouTube provides.
4. Shows you the result so you can fix artist, title, album, year, genre, and
   track number before saving.
5. Saves the file into your mounted music library. The Artist and Album fields
   become the folder names, so typing an existing artist/album reuses that
   folder and typing a new one creates it.

## Default port

`7734`. It does not conflict with the common *arr apps (Radarr 7878, Sonarr
8989, Lidarr 8686, Readarr 8787, Prowlarr 9696, Bazarr 6767). Change the host
port with `HOST_PORT` in `.env`; the container always listens on 7734.

## Quick start (local test)

```bash
cd yarr
cp .env.example .env
# edit .env and point MUSIC_HOST_PATH at a scratch folder, e.g. /tmp/plex-music
mkdir -p /tmp/plex-music
docker compose up --build
# open http://localhost:7734
```

To test with a real download, paste a YouTube URL, wait for the metadata form,
adjust anything, and click **Save to library**. The file lands under
`MUSIC_HOST_PATH/<Artist>/<Album>/`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST_PORT` | `7734` | Host port for the web UI. |
| `MUSIC_HOST_PATH` | `./music` | Host path to your Plex music library, mounted at `/music`. |
| `AUDIO_QUALITY` | `192` | MP3 bitrate in kbps. |
| `PUID` / `PGID` | `1000` / `1000` | UID/GID used inside the container so files written to the library are owned by you on the host. |
| `TZ` | `UTC` | Timezone for logs. |

## Install on the home lab

A multi-arch image (`linux/amd64` + `linux/arm64`) is published to GitHub
Container Registry on every push to `main`:

```text
ghcr.io/parksjr/yarr:latest
```

Use a compose file like this, replacing the paths and the uid/gid:

```yaml
services:
  yarr:
    image: ghcr.io/parksjr/yarr:latest
    container_name: yarr
    restart: unless-stopped
    ports:
      - "7734:7734"
    environment:
      MUSIC_LIBRARY_PATH: /music
      STAGING_PATH: /data/staging
      AUDIO_QUALITY: "192"
      PUID: "1000"          # set to your media user id
      PGID: "1000"          # set to your media group id
      TZ: "America/New_York"
    volumes:
      # The same music folder Lidarr/Plex use:
      - /path/to/your/plex/music:/music
      # Scratch space for in-flight downloads:
      - yarr_data:/data
      # Optional: cookies.txt from a logged-in browser (avoids many 403s):
      # - /path/to/cookies.txt:/cookies.txt:ro

volumes:
  yarr_data:
```

Then run `docker compose up -d` and open `http://YOUR_SERVER_IP:7734`.

To build from source instead, clone the repo and use the included
`docker-compose.yml`.

## Notes and limits

- One video at a time. Playlists are rejected on purpose.
- The library mount must be writable by the `PUID`/`PGID` user.
- This app has no login. Keep it on your private network or put it behind your
  normal auth proxy.

## Troubleshooting

### YouTube 403 Forbidden

A `403` comes from YouTube, not from yarr. It usually means one of:

- YouTube rate-limited your IP after several downloads in a short time.
- The video is age-restricted or needs a signed-in account.
- The video is blocked in your region.

Fixes, in order:

1. Wait a few minutes and try again.
2. Add a cookies file from a logged-in browser:
   - Install a browser extension such as "Get cookies.txt LOCALLY".
   - Export `cookies.txt` while you are signed into YouTube.
   - In `.env`, set `COOKIES_HOST_PATH=/path/to/cookies.txt` (your real file
     location). The compose file already mounts it read-only at `/cookies.txt`.
   - Leave `COOKIES_HOST_PATH` unset to use the empty placeholder shipped with
     this repo (no cookies, downloads run anonymously).
3. Try different yt-dlp player clients by setting
   `YTDLP_PLAYER_CLIENTS=android,tv,web_embedded` in `.env`, then restart the
   container.
