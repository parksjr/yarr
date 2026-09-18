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

1. Push this folder to a Git repo.
2. In Dokploy (or plain `docker compose`), point at the repo with
   `docker-compose.yml`.
3. Set `MUSIC_HOST_PATH` to the same folder Lidarr/Plex use for music, and set
   `PUID`/`PGID` to your media user.
4. Deploy and open the UI on port 7734 (or put it behind your reverse proxy).

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
