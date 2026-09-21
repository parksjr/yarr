import logging
import shutil
from pathlib import Path

import yt_dlp
from yt_dlp.utils import DownloadError

from app import config
from app.jobs import Job

logger = logging.getLogger("yarr.youtube")


def _progress_hook(job: Job, data: dict) -> None:
    status = data.get("status")
    if status == "downloading":
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        downloaded = data.get("downloaded_bytes") or 0
        if total:
            job.progress = round(min(0.9, 0.05 + 0.85 * (downloaded / total)), 3)
        job.stage = "Downloading audio"
    elif status == "finished":
        job.progress = 0.9
        job.stage = "Converting to MP3"


def fetch(job: Job, url: str, staging_dir: Path, quality: str = "192"):
    staging_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(staging_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "writethumbnail": True,
        "retries": 2,
        "fragment_retries": 2,
        "progress_hooks": [lambda d: _progress_hook(job, d)],
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(quality)},
            {"key": "FFmpegMetadata"},
            {"key": "EmbedThumbnail"},
        ],
    }
    if config.YTDLP_COOKIEFILE:
        cookie_path = Path(config.YTDLP_COOKIEFILE)
        if cookie_path.is_file():
            # yt-dlp writes the cookie jar back after extraction, so work on a
            # writable copy inside the per-job staging dir. The mounted source
            # can stay read-only.
            cookie_copy = staging_dir / "cookies.txt"
            shutil.copyfile(cookie_path, cookie_copy)
            opts["cookiefile"] = str(cookie_copy)
        else:
            logger.warning(
                "YTDLP_COOKIEFILE is set but %s is not a readable file; "
                "continuing without cookies.",
                cookie_path,
            )
    if config.YTDLP_PLAYER_CLIENTS:
        clients = [c.strip() for c in config.YTDLP_PLAYER_CLIENTS.split(",") if c.strip()]
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": clients}}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        message = str(exc)
        if "403" in message or "Forbidden" in message:
            raise RuntimeError(
                "YouTube returned 403 Forbidden. This usually means YouTube rate-limited "
                "your IP after recent downloads, or the video needs a logged-in account "
                "(age-restricted or music content). Wait a few minutes and retry, or add a "
                "cookies file via the YTDLP_COOKIEFILE environment variable."
            ) from exc
        raise RuntimeError(message) from exc
    if not info:
        raise RuntimeError("Could not read that URL. Check that it is a valid YouTube link.")
    if info.get("_type") in ("playlist", "multi_video"):
        raise RuntimeError("Playlists are not supported. Paste a single video URL.")
    mp3 = _find_mp3(staging_dir)
    if mp3 is None:
        raise RuntimeError("The download finished but no MP3 file was produced.")
    # Imported lazily here to avoid a circular import (app.source imports this
    # module at startup).
    from app.source import FetchResult

    return FetchResult(mp3_paths=[mp3], info=info, chapters=info.get("chapters"))


def _find_mp3(staging_dir: Path):
    files = sorted(staging_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(files[0]) if files else None
