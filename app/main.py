import os
import shutil
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import chapters, config, jobs, library, metadata, youtube

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.STAGING_PATH.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="yarr", version="0.1.0", lifespan=lifespan)
store = jobs.JobStore()


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


def _run_fetch(job_id: str, url: str) -> None:
    job = store.get(job_id)
    if job is None:
        return
    job.status = "running"
    job.stage = "Contacting YouTube"
    staging = config.STAGING_PATH / job_id
    try:
        info, mp3 = youtube.download(job, url, staging, config.AUDIO_QUALITY)
        job.stage = "Reading metadata"
        meta = metadata.read_tags(mp3, info)
        job.metadata = meta
        job.mp3_path = mp3
        job.staging_dir = str(staging)
        job.info = {
            k: info.get(k)
            for k in ("id", "title", "uploader", "channel", "webpage_url", "duration", "thumbnail")
        }
        job.chapters = chapters.normalize_chapters(info.get("chapters"), info.get("duration"))
        job.progress = 1.0
        job.stage = "Ready"
        job.status = "done"
    except Exception as exc:
        job.status = "error"
        job.stage = "Failed"
        job.error = str(exc)


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
        raise HTTPException(status_code=400, detail="Enter a YouTube URL.")
    job = store.create(url)
    threading.Thread(target=_run_fetch, args=(job.id, url), daemon=True).start()
    return {"job_id": job.id}


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
        job.split_paths = chapters.split_mp3_by_chapters(job.mp3_path, job.chapters, split_dir)
    tracks = metadata.chapter_tracks(job.chapters, job.info, job.metadata)
    return {"tracks": tracks}


@app.post("/api/jobs/{job_id}/save_many")
def save_many(job_id: str, req: SaveManyRequest):
    job = store.get(job_id)
    if job is None or job.status not in ("done", "save_error"):
        raise HTTPException(status_code=400, detail="There is nothing ready to save. Fetch a video first.")
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
    saved = []
    try:
        for i, track in enumerate(req.tracks):
            job.stage = f"Saving track {i + 1} of {len(req.tracks)}"
            src = Path(job.split_paths[i])
            if not src.exists():
                raise RuntimeError(f"Track {i + 1} is missing. Split the video again.")
            meta = track.model_dump()
            metadata.apply_metadata(str(src), meta, cover=cover)
            dest = library.place_file(src, root, meta)
            saved.append({"path": str(dest), "relative": str(dest.relative_to(root))})
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
