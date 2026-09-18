import re
from typing import Optional

from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TRCK,
    ID3NoHeaderError,
)
from mutagen.mp3 import MP3

_TITLE_SEPARATORS = (" - ", " – ", " — ", " | ")


def _text(tags, frame_id: str) -> Optional[str]:
    frame = tags.get(frame_id)
    if frame is None:
        return None
    value = getattr(frame, "text", None)
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    text = str(value).strip()
    return text or None


def _track_number(tags, frame_id: str) -> Optional[int]:
    raw = _text(tags, frame_id)
    if raw is None:
        return None
    digits = raw.split("/")[0].strip()
    if digits.isdigit():
        return int(digits)
    return None


def _year(value) -> str:
    if not value:
        return ""
    match = re.search(r"(19|20)\d{2}", str(value))
    return match.group(0) if match else ""


def guess_artist_track(title: str, uploader: str):
    title = (title or "").strip()
    uploader = (uploader or "").strip()
    for sep in _TITLE_SEPARATORS:
        if sep in title:
            left, right = title.split(sep, 1)
            left, right = left.strip(), right.strip()
            if left and right:
                return left, right
    return (uploader or "Unknown Artist"), (title or "Unknown Title")


def read_tags(mp3_path: str, info: dict) -> dict:
    tags: dict = {}
    try:
        audio = MP3(mp3_path, ID3=ID3)
        if audio.tags is not None:
            tags["title"] = _text(audio.tags, "TIT2")
            tags["artist"] = _text(audio.tags, "TPE1")
            tags["album_artist"] = _text(audio.tags, "TPE2")
            tags["album"] = _text(audio.tags, "TALB")
            tags["date"] = _text(audio.tags, "TDRC")
            tags["genre"] = _text(audio.tags, "TCON")
            tags["track"] = _track_number(audio.tags, "TRCK")
    except Exception:
        pass

    info = info or {}
    title = tags.get("title") or info.get("title") or "Unknown Title"
    uploader = info.get("uploader") or info.get("channel") or ""
    artist = tags.get("artist") or info.get("artist") or ""
    if artist:
        track_title = title
    else:
        artist, track_title = guess_artist_track(title, uploader)

    album = tags.get("album") or info.get("album") or ""
    album_artist = tags.get("album_artist") or info.get("artist") or artist or "Unknown Artist"
    date = _year(tags.get("date")) or _year(info.get("release_date") or info.get("upload_date"))
    genre = tags.get("genre") or info.get("genre") or ""

    track = tags.get("track")
    if track is None:
        for key in ("track_number", "track"):
            raw = info.get(key)
            if raw is not None:
                try:
                    track = int(str(raw).split("/")[0])
                    break
                except (TypeError, ValueError):
                    continue

    return {
        "title": track_title or "Unknown Title",
        "artist": artist or "Unknown Artist",
        "album_artist": album_artist or "Unknown Artist",
        "album": album,
        "date": date,
        "genre": genre,
        "track": track,
    }


def read_cover(mp3_path: str) -> Optional[tuple]:
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        return None
    pics = tags.getall("APIC")
    if not pics:
        return None
    pic = pics[0]
    return pic.mime or "image/jpeg", pic.data


def apply_metadata(mp3_path: str, meta: dict) -> None:
    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()

    def set_text(frame_cls, value) -> None:
        frame_id = frame_cls.__name__
        if value is None or str(value).strip() == "":
            tags.delall(frame_id)
        else:
            tags.setall(frame_id, [frame_cls(encoding=3, text=[str(value).strip()])])

    set_text(TIT2, meta.get("title"))
    set_text(TPE1, meta.get("artist"))
    set_text(TALB, meta.get("album"))
    set_text(TPE2, meta.get("album_artist"))
    set_text(TCON, meta.get("genre"))

    date = str(meta.get("date") or "").strip()
    if date:
        tags.setall("TDRC", [TDRC(encoding=3, text=[date])])
    else:
        tags.delall("TDRC")

    track = meta.get("track")
    try:
        track_int = int(track) if track not in (None, "", 0) else None
    except (TypeError, ValueError):
        track_int = None
    if track_int:
        tags.setall("TRCK", [TRCK(encoding=3, text=[str(track_int)])])
    else:
        tags.delall("TRCK")

    # YouTube metadata embeds the full video description and source URL as
    # comment/custom frames. Drop them so the MP3 stays clean in Plex.
    tags.delall("COMM")
    tags.delall("TXXX")

    tags.save(mp3_path)
