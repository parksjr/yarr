import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Job:
    id: str
    url: str
    status: str = "queued"
    stage: str = "Waiting"
    progress: float = 0.0
    error: Optional[str] = None
    info: Optional[dict] = None
    metadata: Optional[dict] = None
    staging_dir: Optional[str] = None
    mp3_path: Optional[str] = None
    result_path: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": round(self.progress, 3),
            "error": self.error,
            "info": self.info,
            "metadata": self.metadata,
            "result_path": self.result_path,
            "has_cover": bool(self.mp3_path and os.path.exists(self.mp3_path)),
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
        with self._lock:
            stale = [jid for jid, j in self._jobs.items() if now - j.created_at > self._ttl]
            for jid in stale:
                self._jobs.pop(jid, None)
