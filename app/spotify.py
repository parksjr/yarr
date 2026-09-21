"""Spotify download runner built on spotDL.

spotDL resolves a public track/album/playlist URL and writes tagged MP3s the
same way yt-dlp handles YouTube, so the rest of the app can treat both sources
identically. spotDL is imported at module level here; this module itself is
imported lazily by app/source.py only when a Spotify URL is fetched, so
YouTube-only runs never pay the spotDL import cost.
"""

import logging
import shutil
from pathlib import Path

from spotdl import Spotdl

from app import config
from app.jobs import Job
from app.source import FetchResult

logger = logging.getLogger("yarr.spotify")

# spotDL's bundled public client credentials. Used only when the operator has
# not supplied SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET.
SPOTDL_DEFAULT_CLIENT_ID = "5f573c9620494bae87890c0f08a60293"
SPOTDL_DEFAULT_CLIENT_SECRET = "212476d9b0f3472eaa762d90b19b0ba8"


def _progress_callback(job: Job):
    """Build a spotDL progress callback that updates the shared Job state."""

    def callback(tracker, message: str) -> None:
        try:
            song = getattr(tracker, "song", None)
            position = getattr(song, "list_position", None)
            total = getattr(song, "list_length", None)
            prefix = ""
            if position is not None and total:
                prefix = f"track {position} of {total}: "
            progress = getattr(tracker, "progress", 0) or 0
            job.progress = round(min(0.95, max(0.0, progress / 100.0)), 3)
            job.stage = f"Downloading {prefix}{message or 'working'}"
        except Exception:
            # Progress reporting must never break the download worker.
            pass

    return callback


def _song_metadata(song) -> dict:
    """Convert a spotDL Song into the app's metadata form fields."""
    artists = [a for a in (getattr(song, "artists", None) or []) if a]
    artist = getattr(song, "artist", None) or (artists[0] if artists else "") or ""
    album_artist = getattr(song, "album_artist", None) or artist or ""
    year = getattr(song, "year", None)
    date = str(year) if year else ""

    genres = []
    for genre in getattr(song, "genres", None) or []:
        cleaned = str(genre).strip()
        if cleaned and cleaned not in genres:
            genres.append(cleaned)
    genre = ", ".join(genres)

    track_number = getattr(song, "track_number", None)
    try:
        track = int(track_number) if track_number not in (None, "", 0) else None
    except (TypeError, ValueError):
        track = None

    return {
        "title": getattr(song, "name", None) or "",
        "artist": artist,
        "album_artist": album_artist,
        "album": getattr(song, "album_name", None) or "",
        "date": date,
        "genre": genre,
        "track": track,
    }


def _build_info(songs, tracks) -> dict:
    """Build the compact per-job info dict for single- and multi-track results."""
    if len(tracks) == 1:
        song = songs[0]
        return {
            "title": tracks[0].get("title") or "",
            "artist": tracks[0].get("artist") or "",
            "album": tracks[0].get("album") or "",
            "cover_url": getattr(song, "cover_url", None) or "",
        }
    first = songs[0]
    return {
        "title": getattr(first, "list_name", None) or getattr(first, "album_name", None) or "",
        "type": "playlist" if getattr(first, "list_url", None) else "album",
        "count": len(tracks),
    }


def fetch(job: Job, url: str, staging_dir: Path, quality: str = "192") -> FetchResult:
    staging_dir.mkdir(parents=True, exist_ok=True)

    settings = {
        "simple_tui": True,
        "output": str(staging_dir / "{artists} - {title}.{output-ext}"),
        "format": "mp3",
        "bitrate": f"{quality}k",
        "threads": 2,
        "print_errors": True,
        "playlist_numbering": True,
        "playlist_retain_track_cover": True,
        "max_filename_length": 200,
        "overwrite": "skip",
        "lyrics_providers": [],
    }

    if config.YTDLP_COOKIEFILE:
        cookie_path = Path(config.YTDLP_COOKIEFILE)
        if cookie_path.is_file():
            # spotDL passes this cookie jar to its yt-dlp audio provider, which
            # can write it back, so work on a writable copy inside staging.
            cookie_copy = staging_dir / "cookies.txt"
            shutil.copyfile(cookie_path, cookie_copy)
            settings["cookie_file"] = str(cookie_copy)
        else:
            logger.warning(
                "YTDLP_COOKIEFILE is set but %s is not a readable file; "
                "continuing without cookies.",
                cookie_path,
            )

    client_id = config.SPOTIFY_CLIENT_ID or SPOTDL_DEFAULT_CLIENT_ID
    client_secret = config.SPOTIFY_CLIENT_SECRET or SPOTDL_DEFAULT_CLIENT_SECRET

    try:
        spotdl = Spotdl(
            client_id=client_id,
            client_secret=client_secret,
            no_cache=True,
            headless=True,
            downloader_settings=settings,
        )
    except Exception as exc:
        raise RuntimeError(f"Could not start the Spotify downloader: {exc}") from exc

    spotdl.downloader.progress_handler.update_callback = _progress_callback(job)

    try:
        songs = spotdl.search([url])
    except Exception as exc:
        raise RuntimeError(f"Could not look up that Spotify URL: {exc}") from exc

    if not songs:
        raise RuntimeError(
            "No tracks were found at that Spotify URL. Check that it is a public "
            "track, album, or playlist link."
        )

    songs = sorted(songs, key=lambda s: getattr(s, "list_position", 0) or 0)

    job.stage = f"Downloading {len(songs)} track{'s' if len(songs) != 1 else ''}"
    try:
        results = spotdl.download_songs(songs)
    except Exception as exc:
        raise RuntimeError(f"Spotify download failed: {exc}") from exc

    mp3_paths = []
    tracks = []
    failures = []
    for song, path in results:
        if path is not None and Path(path).exists():
            mp3_paths.append(str(path))
            tracks.append(_song_metadata(song))
        else:
            failures.append(getattr(song, "name", None) or "unknown track")

    if not mp3_paths:
        detail = "; ".join(failures[:3])
        raise RuntimeError(
            "None of the Spotify tracks could be downloaded."
            + (f" Failures: {detail}" if detail else "")
        )

    if failures:
        logger.warning(
            "Spotify download was partial; %d track(s) failed: %s",
            len(failures),
            failures,
        )

    info = _build_info(songs, tracks)
    return FetchResult(mp3_paths=mp3_paths, info=info, tracks=tracks)
