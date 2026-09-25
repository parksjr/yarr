import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("yarr.jobs")


class JobAborted(Exception):
    """Raised to unwind a downloader that was told to stop mid-flight.

    Real downloaders (yt-dlp, spotDL) treat a foreign exception from their
    hooks as a per-download failure and carry on with the next item, so this
    only has to unwind the call stack quickly. Whichever module's cancel hook
    raises it, the fetch worker converts it to ``JobCancelled``.
    """


class JobCancelled(JobAborted):
    """Raised inside a worker when the user cancelled the job.

    ``main._run_fetch`` catches this before the generic handler so a cancelled
    job keeps its ``cancelled`` status instead of being reported as an error.
    """


@dataclass
class Job:
    id: str
    url: str
    status: str = "queued"
    stage: str = "Waiting"
    progress: float = 0.0
    error: Optional[str] = None
    source: Optional[str] = None
    info: Optional[dict] = None
    metadata: Optional[dict] = None
    chapters: Optional[list] = None
    split_paths: Optional[list] = None
    tracks: Optional[list] = None
    track_paths: Optional[list] = None
    staging_dir: Optional[str] = None
    mp3_path: Optional[str] = None
    result_path: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    # Cancellation state. ``cancel_event`` is set by the API and checked by the
    # downloader's hooks, which is what stops a fetch mid-download; ``abort`` is
    # an optional downloader-side handle that is called as well (see ``cancel``).
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    abort: Optional[object] = field(default=None, repr=False)

    def cancel(self) -> None:
        """Ask the running worker to stop.

        Two mechanisms, because one is not enough:

        * ``cancel_event`` — every progress hook checks it and raises, which is
          what stops a download that is inside a subprocess (ffmpeg, or yt-dlp's
          embedded JS runtime) and cannot be interrupted from the outside.
        * ``abort`` — an optional callable the downloader module registered. It
          can interrupt a download between progress ticks (spotDL's remaining
          asyncio tasks, for example). Best effort: a failure here never blocks
          the event.
        """
        self.cancel_event.set()
        abort = self.abort
        if abort is not None:
            try:
                abort()
            except Exception:
                pass

    def clear_result(self) -> None:
        """Drop the fetch result so a cancelled job can never be saved."""
        self.info = None
        self.metadata = None
        self.chapters = None
        self.tracks = None
        self.track_paths = None
        self.split_paths = None
        self.mp3_path = None
        self.staging_dir = None

    def staging_dir_path(self) -> Optional[str]:
        """The staging directory to clean: the recorded one, or this job's own.

        A fetch records ``staging_dir`` only once it succeeds, so a job stopped
        early (cancelled while downloading) still knows its directory from its
        id: ``main`` stages every job under ``STAGING_PATH / job.id``.
        """
        if self.staging_dir:
            return self.staging_dir
        # Imported here to keep app.jobs free of a config import at module level
        # (tests and tooling import this module without the app environment).
        from app import config

        return str(config.STAGING_PATH / self.id)

    def clean_staging(self) -> None:
        """Remove this job's staging directory, including any partial download.

        Deletion is verified: on Docker Desktop for macOS a file that the worker
        still holds open reappears after ``rmtree``, so one pass can silently
        leave a partial download behind. Repeat once, and report whether the
        directory is really gone so the caller can retry later.
        """
        path = self.staging_dir_path()
        if not path or not os.path.isdir(path):
            return
        for _ in range(2):
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.isdir(path):
                return
            time.sleep(0.05)
        if os.path.isdir(path):
            logger.warning(
                "Staging directory %s was not fully removed; it will be cleaned "
                "on the next request or restart.",
                path,
            )

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "error": self.error,
            "source": self.source,
            "info": self.info,
            "metadata": self.metadata,
            "chapters": self.chapters,
            "has_chapters": bool(self.chapters and len(self.chapters) > 1),
            "split_ready": bool(self.split_paths),
            "tracks": self.tracks,
            "multi_track": bool(self.tracks and len(self.tracks) > 1),
            "result_path": self.result_path,
            "has_cover": bool(self.mp3_path and os.path.exists(self.mp3_path)),
            "cancellable": self.status in ("queued", "running"),
        }


class JobStore:
    def __init__(self, ttl: int = 86400):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._ttl = ttl

    def create(self, url: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], url=url)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        self._purge()
        with self._lock:
            return self._jobs.get(job_id)

    def _purge(self) -> None:
        now = time.time()
        removed = []
        with self._lock:
            stale = [jid for jid, j in self._jobs.items() if now - j.created_at > self._ttl]
            for jid in stale:
                removed.append(self._jobs.pop(jid, None))
        # Stale jobs are normally long finished, so their staging dirs are
        # already gone; a job abandoned before saving would otherwise leak.
        for job in removed:
            if job is not None:
                job.clean_staging()
