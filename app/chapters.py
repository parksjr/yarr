"""Chapter detection and audio splitting.

yt-dlp already returns the video's chapters in the ``info`` dict (the same
chapters the uploader created in the description / chapter editor). We normalize
them into a small shape and split a single MP3 into one MP3 per chapter with
``ffmpeg -c copy``, which is fast and does not re-encode the audio.
"""

import subprocess
from pathlib import Path
from typing import Optional

from app.jobs import Job, JobCancelled


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_chapters(raw_chapters, duration=None) -> list[dict]:
    """Turn yt-dlp chapter dicts into ``{index, title, start, end}`` entries.

    ``end`` is ``None`` for the final chapter (it runs to the end of the file).
    Chapters that start at or beyond the known duration are dropped.
    """
    dur = _to_float(duration)
    chapters: list[dict] = []
    for i, ch in enumerate(raw_chapters or []):
        if not isinstance(ch, dict):
            continue
        title = str(ch.get("title") or "").strip() or f"Track {i + 1}"
        start = _to_float(ch.get("start_time"))
        end = _to_float(ch.get("end_time"))
        if start is None:
            start = 0.0
        if dur is not None and start >= dur - 0.5:
            continue
        chapters.append({"index": i + 1, "title": title, "start": start, "end": end})
    return chapters


def split_mp3_by_chapters(
    mp3_path: str, chapters: list[dict], out_dir: Path, job: Optional[Job] = None
) -> list[str]:
    """Split ``mp3_path`` into one MP3 per chapter using stream copy.

    Returns the list of output paths in chapter order. Stream copy keeps the
    original MP3 encoding, so the split is quick and introduces no quality loss.

    When ``job`` is given, a cancel request stops the split between chapters and
    raises ``JobCancelled``. An ffmpeg process already running is left to finish
    (a chapter copy takes about a second); the partial output is then removed by
    the caller's staging cleanup.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for ch in chapters:
        if job is not None and job.cancel_event.is_set():
            raise JobCancelled()
        index = int(ch.get("index", len(paths) + 1))
        start = float(ch.get("start") or 0.0)
        end = ch.get("end")
        out_path = out_dir / f"{index:02d}.mp3"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            # Input seek is fast. It resets output timestamps, so the stop
            # time below must be relative to the chapter start.
            "-ss",
            f"{start:.3f}",
            "-i",
            str(mp3_path),
        ]
        if end is not None and float(end) > start:
            cmd += ["-to", f"{float(end) - start:.3f}"]
        cmd += ["-c", "copy", "-f", "mp3", str(out_path)]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to split chapter {index} ({ch.get('title')}): {proc.stderr.strip()}"
            )
        if job is not None and job.cancel_event.is_set():
            raise JobCancelled()
        paths.append(str(out_path))
    return paths
