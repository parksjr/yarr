"""Spotify download runner built on spotDL.

spotDL resolves a public track/album/playlist URL and writes tagged MP3s the
same way yt-dlp handles YouTube, so the rest of the app can treat both sources
identically. spotDL is imported at module level here; this module itself is
imported lazily by app/source.py only when a Spotify URL is fetched, so
YouTube-only runs never pay the spotDL import cost.

spotDL's ``SpotifyClient`` is a process-wide singleton: ``SpotifyClient.init``
raises if it is called twice. yarr therefore initializes it exactly once
(guarded by a lock) and builds a fresh ``Downloader`` (which owns its own
asyncio loop) for every fetch, so repeated and concurrent Spotify fetches in
one process work.
"""

import logging
import shutil
import threading
from pathlib import Path
from urllib.parse import urlparse

from spotdl.download.downloader import Downloader
from spotdl.utils.search import parse_query
from spotdl.utils.spotify import SpotifyClient

from app import config
from app.jobs import Job
from app.source import FetchResult

logger = logging.getLogger("yarr.spotify")

# spotDL's bundled public client credentials. Used only when the operator has
# not supplied SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET.
SPOTDL_DEFAULT_CLIENT_ID = "5f573c9620494bae87890c0f08a60293"
SPOTDL_DEFAULT_CLIENT_SECRET = "212476d9b0f3472eaa762d90b19b0ba8"

_client_lock = threading.Lock()


def _ensure_spotify_client(client_id: str, client_secret: str) -> None:
    """Initialize spotDL's process-wide Spotify client exactly once."""
    with _client_lock:
        if SpotifyClient._instance is None:
            SpotifyClient.init(
                client_id=client_id,
                client_secret=client_secret,
                no_cache=True,
                headless=True,
            )


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


def _source_type(url: str) -> str:
    path = (urlparse(url).path or "").lower()
    if "/playlist/" in path:
        return "playlist"
    if "/album/" in path:
        return "album"
    return "track"


def _sort_key(song):
    """Order songs: playlists by list_position, albums by disc/track number."""
    position = getattr(song, "list_position", None)
    if position is not None:
        return (0, 0, position)
    disc = getattr(song, "disc_number", None) or 0
    track = getattr(song, "track_number", None) or 0
    return (1, disc, track)


def _clean_spotdl_errors(errors) -> list:
    """Turn spotDL's internal error strings into short, human messages.

    spotDL formats downloader errors as ``"<url> - <ExceptionClass>: <message>"``
    (for example ``"https://open.spotify.com/track/... - LookupError: No results
    found for song: Madonna - Vogue"``). The URL and exception class add noise
    for a user, so strip them and keep the message.
    """
    cleaned = []
    for err in errors or []:
        text = str(err)
        if " - " in text:
            text = text.split(" - ", 1)[1]
        if ": " in text:
            text = text.split(": ", 1)[1]
        text = text.strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _build_info(songs, tracks, url) -> dict:
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
        "type": _source_type(url),
        "count": len(tracks),
    }


def fetch(job: Job, url: str, staging_dir: Path, quality: str = "192") -> FetchResult:
    staging_dir.mkdir(parents=True, exist_ok=True)

    settings = {
        "simple_tui": True,
        # The track number makes staging filenames unique per album/playlist so
        # spotDL cannot collide two tracks that share an artist + title.
        "output": str(staging_dir / "{track-number} - {artists} - {title}.{output-ext}"),
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
    if bool(config.SPOTIFY_CLIENT_ID) != bool(config.SPOTIFY_CLIENT_SECRET):
        logger.warning(
            "Only one of SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET is set; "
            "the missing one falls back to spotDL's bundled default and Spotify "
            "authentication will likely fail."
        )

    try:
        _ensure_spotify_client(client_id, client_secret)
    except Exception as exc:
        raise RuntimeError(f"Could not start the Spotify downloader: {exc}") from exc

    try:
        songs = parse_query(
            [url],
            threads=settings["threads"],
            playlist_numbering=settings["playlist_numbering"],
            playlist_retain_track_cover=settings["playlist_retain_track_cover"],
        )
    except Exception as exc:
        raise RuntimeError(f"Could not look up that Spotify URL: {exc}") from exc

    if not songs:
        raise RuntimeError(
            "No tracks were found at that Spotify URL. Check that it is a public "
            "track, album, or playlist link."
        )

    songs.sort(key=_sort_key)

    job.stage = f"Downloading {len(songs)} track{'s' if len(songs) != 1 else ''}"
    try:
        downloader = Downloader(settings=settings)
        downloader.progress_handler.update_callback = _progress_callback(job)
        results = downloader.download_multiple_songs(songs)
    except Exception as exc:
        raise RuntimeError(f"Spotify download failed: {exc}") from exc

    spotdl_errors = _clean_spotdl_errors(getattr(downloader, "errors", None))

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
        detail = "; ".join(spotdl_errors or failures[:3])
        if detail:
            raise RuntimeError(f"Could not download this Spotify item. {detail}")
        raise RuntimeError("Could not download this Spotify item.")

    if failures:
        logger.warning(
            "Spotify download was partial; %d track(s) failed: %s",
            len(failures),
            "; ".join(spotdl_errors or failures),
        )

    info = _build_info(songs, tracks, url)
    return FetchResult(mp3_paths=mp3_paths, info=info, tracks=tracks)
