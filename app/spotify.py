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

import asyncio
import contextlib
import functools
import logging
import shutil
import threading
from pathlib import Path
from urllib.parse import urlparse

from spotdl.download.downloader import Downloader
from spotdl.utils.search import parse_query
from spotdl.utils.spotify import SpotifyClient

from app import abort, config
from app.jobs import Job, JobAborted, JobCancelled
from app.source import FetchResult

logger = logging.getLogger("yarr.spotify")

# spotDL's bundled public client credentials. Used only when the operator has
# not supplied SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET.
SPOTDL_DEFAULT_CLIENT_ID = "5f573c9620494bae87890c0f08a60293"
SPOTDL_DEFAULT_CLIENT_SECRET = "212476d9b0f3472eaa762d90b19b0ba8"

_client_lock = threading.Lock()
# Serializes the YoutubeDL class patch in ``_ytdlp_abort_hook``.
_patch_lock = threading.Lock()


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


def _cancel_tasks(downloader, live_tasks) -> None:
    """Cancel every running spotDL download task.

    spotDL gathers all per-song coroutines into one ``asyncio.gather`` call, so
    cancelling the gathered tasks unwinds the whole album download instead of
    just the current track. Called from the progress callback, which runs inside
    spotDL's own event loop, so touching its task set is safe there.
    """
    try:
        tasks = [t for t in list(live_tasks) if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            # Let the cancellations take effect before the loop stops.
            downloader.loop.run_until_complete(
                asyncio.gather(*tasks, return_exceptions=True)
            )
    except Exception:
        # Cancellation is best effort; the armed hook already aborts the track.
        logger.debug("Could not cancel spotDL tasks cleanly", exc_info=True)


def _download_multiple_songs(downloader, live_tasks, songs):
    """``Downloader.download_multiple_songs`` with task tracking.

    ``asyncio.gather`` turns the coroutines into tasks once it runs, which is
    too late to cancel them by hand. The tasks are therefore created (and
    registered) inside the loop first and removed as they finish, so a cancel
    request can stop the downloads that are still in flight. Everything else
    mirrors spotDL: every song is pooled in parallel, limited by its semaphore.
    """
    tasks = []

    async def run() -> list:
        tasks.extend(asyncio.ensure_future(downloader.pool_download(song)) for song in songs)
        for task in tasks:
            live_tasks.add(task)
            task.add_done_callback(live_tasks.discard)
        return list(await asyncio.gather(*tasks))

    return downloader.loop.run_until_complete(run())


@contextlib.contextmanager
def _ytdlp_abort_hook(cancel):
    """Make every ``YoutubeDL`` spotDL creates install ``cancel`` as a hook.

    spotDL's audio providers build their ``YoutubeDL`` objects on the fly, so
    the hook cannot be attached to an existing instance. The provider module's
    ``YoutubeDL`` name is patched for the duration of the fetch and restored
    afterwards. Only one fetch patches at a time (guarded by a lock), so
    concurrent Spotify fetches cannot see each other's hook.
    """
    with _patch_lock:
        from spotdl.providers.audio import base as audio_base

        original = audio_base.YoutubeDL

        class AbortableYoutubeDL(original):  # type: ignore[misc, valid-type]
            def __init__(self, params=None, *args, **kwargs):
                super().__init__(params, *args, **kwargs)
                self.add_progress_hook(cancel)

        audio_base.YoutubeDL = AbortableYoutubeDL
        try:
            yield
        finally:
            audio_base.YoutubeDL = original


def _progress_callback(job: Job, cancel, cancel_tasks):
    """Build a spotDL progress callback that updates the shared Job state.

    The first call is the cancellation path: spotDL fires this callback on every
    progress tick, so an armed cancel stops the current track at once.
    ``cancel_tasks`` also cancels spotDL's remaining asyncio tasks before the
    raise, because spotDL catches every exception per song: raising from the
    callback alone would only stop the one track and continue with the next.
    """

    def callback(tracker, message: str) -> bool:
        if job.cancel_event.is_set():
            cancel()
            cancel_tasks()
            raise JobAborted("spotify fetch aborted")
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
        except JobAborted:
            raise
        except Exception:
            # Progress reporting must never break the download worker.
            pass
        return True

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
    cancel = abort.armed_abort(job.cancel_event, "spotify fetch aborted")

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

    if job.cancel_event.is_set():
        raise JobCancelled()

    if not songs:
        raise RuntimeError(
            "No tracks were found at that Spotify URL. Check that it is a public "
            "track, album, or playlist link."
        )

    songs.sort(key=_sort_key)

    if job.cancel_event.is_set():
        raise JobCancelled()

    job.stage = f"Downloading {len(songs)} track{'s' if len(songs) != 1 else ''}"
    downloader = Downloader(settings=settings)
    # spotDL 4.5 has no public abort API, so cancellation needs three parts.
    #
    # 1. The yt-dlp download spotDL runs. spotDL builds a fresh ``YoutubeDL``
    #    inside each audio provider for every download, so adding a hook to the
    #    handler up front does nothing (that object is replaced). Instead the
    #    provider's ``YoutubeDL`` class is replaced for the duration of this
    #    fetch with a subclass that installs the abort hook in ``__init__``.
    # 2. The tracked asyncio tasks below, which stop the rest of the album.
    # 3. The progress callback, which is spotDL's own per-song tick.
    #
    # ``cancel`` (``app.abort``) is the flag: it reads as True and raises once
    # the user has asked to cancel, so every hook that shares it aborts its
    # download mid-file.
    live_tasks: set = set()
    downloader.progress_handler.update_callback = _progress_callback(
        job, cancel, lambda: _cancel_tasks(downloader, live_tasks)
    )
    downloader.download_multiple_songs = functools.partial(
        _download_multiple_songs, downloader, live_tasks
    )
    try:
        with _ytdlp_abort_hook(cancel):
            results = downloader.download_multiple_songs(songs)
    except JobAborted as exc:
        raise JobCancelled() from exc
    except JobCancelled:
        raise
    except Exception as exc:
        if job.cancel_event.is_set():
            raise JobCancelled() from exc
        raise RuntimeError(f"Spotify download failed: {exc}") from exc

    if job.cancel_event.is_set():
        raise JobCancelled()

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
