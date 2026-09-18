import os
from pathlib import Path

MUSIC_LIBRARY_PATH = Path(os.environ.get("MUSIC_LIBRARY_PATH", "/music"))
STAGING_PATH = Path(os.environ.get("STAGING_PATH", "/data/staging"))
AUDIO_QUALITY = os.environ.get("AUDIO_QUALITY", "192")
PORT = int(os.environ.get("PORT", "7734"))
HOST = os.environ.get("HOST", "0.0.0.0")

# Optional Netscape cookies.txt (mounted into the container) for age-restricted
# or login-required videos, and to reduce YouTube 403 rate limiting.
YTDLP_COOKIEFILE = os.environ.get("YTDLP_COOKIEFILE", "/cookies.txt")
# Optional comma-separated yt-dlp YouTube player clients, e.g. "android,tv".
YTDLP_PLAYER_CLIENTS = os.environ.get("YTDLP_PLAYER_CLIENTS", "")
