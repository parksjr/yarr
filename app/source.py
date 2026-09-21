"""Source routing for yarr downloads.

``main.py`` calls ``source.detect`` and ``source.fetch`` instead of importing a
specific downloader directly, so YouTube and Spotify share one fetch/save flow.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from app import youtube
from app.jobs import Job


@dataclass
class FetchResult:
    """What a source produced in the per-job staging directory."""

    mp3_paths: list[str]
    info: dict
    tracks: Optional[list] = None     # per-track metadata for multi-track Spotify
    chapters: Optional[list] = None   # raw YouTube chapters


def detect(url: str) -> str:
    """Return the source name for a URL, or raise ValueError.

    Only trusted domains are allowed. ``urlparse().hostname`` is lowercased
    before matching so a mixed-case host cannot slip through.
    """
    host = (urlparse(url).hostname or "").lower()
    if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
        return "youtube"
    raise ValueError("Enter a YouTube URL.")


def fetch(job: Job, url: str, staging_dir: Path, quality: str) -> FetchResult:
    """Detect the source and run its downloader into ``staging_dir``."""
    src = detect(url)
    if src == "youtube":
        result = youtube.fetch(job, url, staging_dir, quality)
    else:
        raise ValueError(f"Unsupported source: {src}")
    result.info["source"] = src
    return result
