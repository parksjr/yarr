import os
import re
import shutil
from pathlib import Path

ILLEGAL_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}


def sanitize(name) -> str:
    name = str(name or "")
    if not name.strip(" ."):
        return ""
    name = "".join(" " if ch in ILLEGAL_CHARS else ch for ch in name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:150].rstrip(" .")


def safe_join(root: Path, *parts: str) -> Path:
    root = root.resolve()
    path = root
    for part in parts:
        path = path / sanitize(part)
    path = path.resolve()
    if path != root and not str(path).startswith(str(root) + os.sep):
        raise ValueError("Destination escapes the music library.")
    return path


def list_dirs(path: Path):
    if not path.exists() or not path.is_dir():
        return []
    return sorted(p.name for p in path.iterdir() if p.is_dir() and not p.name.startswith("."))


def list_artists(root: Path):
    return list_dirs(root)


def list_albums(root: Path, artist: str):
    if not artist:
        return []
    try:
        return list_dirs(safe_join(root, artist))
    except ValueError:
        return []


def build_path(root: Path, meta: dict) -> Path:
    artist = sanitize(meta.get("artist")) or "Unknown Artist"
    album = sanitize(meta.get("album"))
    title = sanitize(meta.get("title")) or "Unknown Title"
    track = meta.get("track")
    try:
        track_int = int(track) if track not in (None, "", 0) else None
    except (TypeError, ValueError):
        track_int = None
    filename = f"{track_int:02d} - {title}.mp3" if track_int else f"{title}.mp3"
    if album:
        return safe_join(root, artist, album) / filename
    return safe_join(root, artist) / filename


def place_file(mp3_path: Path, root: Path, meta: dict) -> Path:
    dest = build_path(root, meta)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        counter = 2
        while dest.exists():
            dest = dest.with_name(f"{stem} ({counter}){suffix}")
            counter += 1
    shutil.move(str(mp3_path), str(dest))
    return dest
