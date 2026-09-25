import os
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import chapters, config, jobs, library, metadata, source

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.STAGING_PATH.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="yarr", version="0.1.0", lifespan=lifespan)
store = jobs.JobStore()


class CancelRequest(BaseModel):
    reason: str = ""


class FetchRequest(BaseModel):
    url: str


class SaveRequest(BaseModel):
    title: str = ""
    artist: str = ""
    album_artist: str = ""
    album: str = ""
    date: str = ""
    genre: str = ""
    track: int | None = None


class SaveManyRequest(BaseModel):
    tracks: list[SaveRequest]


class BatchTrackRequest(SaveRequest):
    job_id: str


class BatchSaveRequest(BaseModel):
    tracks: list[BatchTrackRequest]


_SOURCE_LABELS = {"youtube": "YouTube", "spotify": "Spotify"}


def _info_subset(info: dict) -> dict:
    """Whitelist the info fields we expose on a job.

    YouTube jobs keep the same keys as before; ``source`` records which
    downloader produced the result, and Spotify's extra keys are included when
    present.
    """
    subset = {
        k: info.get(k)
        for k in ("id", "title", "uploader", "channel", "webpage_url", "duration", "thumbnail")
    }
    subset["source"] = info.get("source")
    # Spotify-only keys must not leak onto YouTube jobs (keeps the pre-change
    # YouTube payload exactly as before).
    if info.get("source") == "spotify":
        for k in ("artist", "album", "cover_url", "type", "count"):
            if info.get(k) is not None:
                subset[k] = info[k]
    return subset


def _run_fetch(job_id: str, url: str) -> None:
    job = store.get(job_id)
    if job is None:
        return
    job.status = "running"
    staging = config.STAGING_PATH / job_id
    try:
        if job.cancel_event.is_set():
            raise jobs.JobCancelled()
        src = source.detect(url)
        job.source = src
        job.stage = f"Contacting {_SOURCE_LABELS.get(src, src)}"
        result = source.fetch(job, url, staging, config.AUDIO_QUALITY)
        job.info = _info_subset(result.info)
        if result.tracks:
            # Spotify: metadata comes from the Song objects, not from re-reading
            # tags. A single track uses the ordinary single-save flow; multiple
            # tracks stay in staging until save_tracks moves them.
            job.track_paths = result.mp3_paths
            if len(result.tracks) == 1:
                job.metadata = result.tracks[0]
                job.mp3_path = result.mp3_paths[0]
                job.tracks = None
            else:
                job.tracks = result.tracks
                job.mp3_path = result.mp3_paths[0]
        else:
            job.stage = "Reading metadata"
            mp3 = result.mp3_paths[0]
            job.metadata = metadata.read_tags(mp3, result.info)
            job.mp3_path = mp3
        job.staging_dir = str(staging)
        job.chapters = chapters.normalize_chapters(result.chapters, result.info.get("duration"))
        if job.cancel_event.is_set():
            raise jobs.JobCancelled()
        job.progress = 1.0
        job.stage = "Ready"
        job.status = "done"
    except jobs.JobCancelled:
        # The user aborted (or asked to abort) a fetch that was still starting
        # up. Leave the status/error text to the cancel endpoint when it already
        # reported the cancel, and clean the staging dir here too so the partial
        # download goes away as soon as this thread lets go of it.
        if job.status != "cancelled":
            job.status = "cancelled"
            job.stage = "Cancelled"
            job.error = "Cancelled by you."
            job.progress = 0.0
        job.clear_result()
        job.clean_staging()
    except Exception as exc:
        if job.status == "cancelled":
            # The cancel endpoint got there first; keep its status and message.
            job.clean_staging()
            return
        job.status = "error"
        job.stage = "Failed"
        job.error = str(exc)
        job.clean_staging()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    root = config.MUSIC_LIBRARY_PATH
    return {
        "status": "ok",
        "library_path": str(root),
        "library_exists": root.exists(),
    }


@app.post("/api/fetch")
def fetch(req: FetchRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Enter a YouTube or Spotify URL.")
    job = store.create(url)
    threading.Thread(target=_run_fetch, args=(job.id, url), daemon=True).start()
    return {"job_id": job.id}


def _cleanup_after_cancel(job) -> None:
    """Delete a cancelled job's staging dir, retrying once the worker stops.

    The worker holds its downloaded files open until its download call unwinds,
    and Docker Desktop for macOS lets such a file survive a delete (it reappears
    when the handle closes). So try immediately, then once more a moment later.
    """
    job.clean_staging()
    if not os.path.isdir(job.staging_dir_path()):
        return

    def retry() -> None:
        for _ in range(40):  # about 4 s
            time.sleep(0.1)
            job.clean_staging()
            if not os.path.isdir(job.staging_dir_path()):
                return

    threading.Thread(target=retry, daemon=True).start()


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, req: CancelRequest | None = None):
    """Abort an in-flight fetch and clean up after it.

    ``job.cancel()`` stops the running downloader: its hooks raise as soon as
    they see the cancel, and the downloader-side abort handle (see
    ``app.abort``) interrupts the read loop so no subprocess is left working.
    The job is marked cancelled and its result cleared right away, so the user
    gets a clean slate and nothing from the aborted fetch can be saved. The
    staging directory is then removed (repeatedly if needed, because the worker
    thread may still hold the files open for a moment).
    """
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    note = (req.reason if req else "") or "Cancelled by you."
    job.cancel()
    job.status = "cancelled"
    job.stage = "Cancelled"
    job.error = note
    job.progress = 0.0
    # Drop the result: a cancelled fetch must not be previewable or savable,
    # even if the worker thread finishes its download before it notices.
    job.clear_result()
    _cleanup_after_cancel(job)
    return {"status": "cancelled", "message": note}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job.public_dict()


@app.get("/api/jobs/{job_id}/cover")
def get_cover(job_id: str):
    job = store.get(job_id)
    if job is None or not job.mp3_path or not os.path.exists(job.mp3_path):
        raise HTTPException(status_code=404, detail="Cover art not available.")
    cover = metadata.read_cover(job.mp3_path)
    if cover is None:
        raise HTTPException(status_code=404, detail="Cover art not available.")
    mime, data = cover
    return Response(content=data, media_type=mime)


@app.get("/api/library")
def get_library():
    return {
        "root": str(config.MUSIC_LIBRARY_PATH),
        "artists": library.list_artists(config.MUSIC_LIBRARY_PATH),
    }


@app.get("/api/library/albums")
def get_library_albums(artist: str = Query("")):
    return {"albums": library.list_albums(config.MUSIC_LIBRARY_PATH, artist)}


@app.post("/api/jobs/{job_id}/preview")
def preview(job_id: str, req: SaveRequest):
    job = store.get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=400, detail="This download is not ready to preview.")
    root = config.MUSIC_LIBRARY_PATH.resolve()
    dest = library.build_path(root, req.model_dump())
    return {"path": str(dest.relative_to(root)), "absolute": str(dest)}


@app.post("/api/jobs/{job_id}/split")
def split(job_id: str):
    job = store.get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=400, detail="This download is not ready to split.")
    if not job.chapters or len(job.chapters) < 2:
        raise HTTPException(status_code=400, detail="This video does not have chapters to split.")
    if not job.mp3_path or not os.path.exists(job.mp3_path):
        raise HTTPException(status_code=400, detail="The MP3 file is missing. Fetch the video again.")
    if not job.split_paths:
        split_dir = Path(job.staging_dir) / "split"
        try:
            job.split_paths = chapters.split_mp3_by_chapters(
                job.mp3_path, job.chapters, split_dir, job
            )
        except jobs.JobCancelled:
            raise HTTPException(status_code=400, detail="The split was cancelled.") from None
    tracks = metadata.chapter_tracks(job.chapters, job.info, job.metadata)
    return {"tracks": tracks}


def _save_paths(job, root, src_paths, track_models, cover, missing_message):
    """Apply metadata and move each track into the library.

    Shared by the chapter-split and Spotify multi-track save flows. The caller
    validates path counts up front; files move one at a time, so a mid-way
    failure can still leave earlier tracks saved.
    """
    saved = []
    for i, (src_path, track) in enumerate(zip(src_paths, track_models)):
        job.stage = f"Saving track {i + 1} of {len(src_paths)}"
        src = Path(src_path)
        if not src.exists():
            raise RuntimeError(f"Track {i + 1} is missing. {missing_message}")
        meta = track.model_dump()
        metadata.apply_metadata(str(src), meta, cover=cover)
        dest = library.place_file(src, root, meta)
        relative = str(dest.relative_to(root))
        saved.append({
            "path": str(dest),
            "relative": relative,
            "folder": relative.rsplit("/", 1)[0] if "/" in relative else "",
            "title": meta.get("title") or "",
            "artist": meta.get("artist") or "",
        })
    return saved


@app.post("/api/jobs/{job_id}/save_many")
def save_many(job_id: str, req: SaveManyRequest):
    job = store.get(job_id)
    if job is None or job.status == "cancelled":
        raise HTTPException(status_code=400, detail="This download was cancelled.")
    if job.status not in ("done", "save_error"):
        raise HTTPException(status_code=400, detail="There is nothing ready to save. Fetch it first.")
    if not req.tracks:
        raise HTTPException(status_code=400, detail="No tracks to save.")
    if not job.split_paths or len(job.split_paths) != len(req.tracks):
        raise HTTPException(status_code=400, detail="Split the video into chapters first.")
    root = config.MUSIC_LIBRARY_PATH.resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    cover = metadata.read_cover(job.mp3_path) if job.mp3_path else None
    job.status = "saving"
    job.stage = "Saving tracks"
    try:
        saved = _save_paths(
            job, root, job.split_paths, req.tracks, cover, "Split the video again."
        )
        job.status = "saved"
        job.stage = "Saved"
        if job.staging_dir and os.path.isdir(job.staging_dir):
            shutil.rmtree(job.staging_dir, ignore_errors=True)
        return {"status": "saved", "tracks": saved}
    except Exception as exc:
        job.status = "save_error"
        job.stage = "Save failed"
        job.error = str(exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/jobs/{job_id}/save_tracks")
def save_tracks(job_id: str, req: SaveManyRequest):
    job = store.get(job_id)
    if job is None or job.status == "cancelled":
        raise HTTPException(status_code=400, detail="This download was cancelled.")
    if job.status not in ("done", "save_error"):
        raise HTTPException(status_code=400, detail="There is nothing ready to save. Fetch it first.")
    if not req.tracks:
        raise HTTPException(status_code=400, detail="No tracks to save.")
    if not job.track_paths or len(job.track_paths) != len(req.tracks):
        raise HTTPException(status_code=400, detail="This download does not have that many tracks. Fetch it again.")
    root = config.MUSIC_LIBRARY_PATH.resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    job.status = "saving"
    job.stage = "Saving tracks"
    try:
        # cover=None: each Spotify MP3 already carries its own embedded art, so
        # we must not overwrite per-track covers with the first track's cover.
        saved = _save_paths(job, root, job.track_paths, req.tracks, None, "Fetch it again.")
        job.status = "saved"
        job.stage = "Saved"
        if job.staging_dir and os.path.isdir(job.staging_dir):
            shutil.rmtree(job.staging_dir, ignore_errors=True)
        return {"status": "saved", "tracks": saved}
    except Exception as exc:
        job.status = "save_error"
        job.stage = "Save failed"
        job.error = str(exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/batch/save")
def batch_save(req: BatchSaveRequest):
    if not req.tracks:
        raise HTTPException(status_code=400, detail="No songs to save.")
    root = config.MUSIC_LIBRARY_PATH.resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)

    # Validate every queued job up front so a bad one does not leave a
    # half-saved batch behind.
    jobs = []
    for i, track in enumerate(req.tracks):
        job = store.get(track.job_id)
        if job is None or job.status == "cancelled":
            raise HTTPException(status_code=400, detail=f"Song {i + 1} was cancelled. Fetch it again.")
        if job.status not in ("done", "save_error"):
            raise HTTPException(status_code=400, detail=f"Song {i + 1} is not ready to save. Fetch it again.")
        if not job.mp3_path or not os.path.exists(job.mp3_path):
            raise HTTPException(status_code=400, detail=f"Song {i + 1} is missing its audio file. Fetch it again.")
        jobs.append(job)

    saved = []
    for i, (track, job) in enumerate(zip(req.tracks, jobs)):
        # One song per job, saved on its own. Each track keeps its own
        # artist/album metadata, so a batch of unrelated songs is expected.
        meta = track.model_dump(exclude={"job_id"})
        label = meta.get("title") or f"song {i + 1}"
        try:
            metadata.apply_metadata(job.mp3_path, meta)
            dest = library.place_file(Path(job.mp3_path), root, meta)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Save stopped at song {i + 1} of {len(req.tracks)} ({label}): {exc} "
                    f"The {len(saved)} song(s) saved before it are already in the library."
                ),
            ) from exc
        relative = str(dest.relative_to(root))
        saved.append({
            "path": str(dest),
            "relative": relative,
            "folder": relative.rsplit("/", 1)[0] if "/" in relative else "",
            "title": meta.get("title") or "",
            "artist": meta.get("artist") or "",
        })
        job.status = "saved"
        job.stage = "Saved"
        if job.staging_dir and os.path.isdir(job.staging_dir):
            shutil.rmtree(job.staging_dir, ignore_errors=True)
    return {"status": "saved", "tracks": saved}


@app.post("/api/jobs/{job_id}/save")
def save(job_id: str, req: SaveRequest):
    job = store.get(job_id)
    if job is None or job.status not in ("done", "save_error"):
        raise HTTPException(status_code=400, detail="There is nothing ready to save. Fetch a video first.")
    if not job.mp3_path or not os.path.exists(job.mp3_path):
        raise HTTPException(status_code=400, detail="The MP3 file is missing. Fetch the video again.")
    job.status = "saving"
    job.stage = "Applying metadata"
    try:
        metadata.apply_metadata(job.mp3_path, req.model_dump())
        root = config.MUSIC_LIBRARY_PATH.resolve()
        if not root.exists():
            root.mkdir(parents=True, exist_ok=True)
        dest = library.place_file(Path(job.mp3_path), root, req.model_dump())
        job.result_path = str(dest)
        job.status = "saved"
        job.stage = "Saved"
        if job.staging_dir and os.path.isdir(job.staging_dir):
            shutil.rmtree(job.staging_dir, ignore_errors=True)
        return {"status": "saved", "path": str(dest), "relative": str(dest.relative_to(root))}
    except Exception as exc:
        job.status = "save_error"
        job.stage = "Save failed"
        job.error = str(exc)
        raise HTTPException(status_code=400, detail=str(exc))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
