"""Cooperative abort for the download libraries yarr uses.

Why this module exists
----------------------
yt-dlp honours a ``KeyboardInterrupt`` raised from a progress hook by stopping
the download immediately. spotDL catches every exception in
``Downloader.async_search_and_download`` (it logs the error and moves on to the
next track), so raising from a spotDL progress hook does *not* cancel the rest
of an album.

This module restores that behaviour without forking either library:

``armed_abort`` is used as the "cancel requested" signal inside a hook. It reads
as ``bool`` and is callable, so the same object works with yt-dlp's hooks and
spotDL's progress callback:

* yt-dlp's own progress wrapper checks ``if ph is cancel: raise ...`` and, when
  the hook is not callable, treats any exception the hook raises as a fatal
  download error. Both paths stop the download.
* The first hook evaluation raises ``JobAborted``, which unwinds the download
  stack with an unwinding exception instead of an ordinary one, so a caller
  that means to cancel (spotDL) can recognise it instead of swallowing it.

Every hook fired while an abort is armed raises, so each download that shares
the flag stops itself. Callers use a fresh ``threading.Event`` per job, so a
hook from an earlier job is never affected.
"""

from app.jobs import JobAborted


class _ArmedAbort:
    """Truthy while the job's cancel event is set. Calling it raises."""

    __slots__ = ("_event", "_label")

    def __init__(self, event, label: str = "") -> None:
        self._event = event
        self._label = label

    def __bool__(self) -> bool:
        return self._event.is_set()

    def __call__(self, *args, **kwargs) -> bool:
        # Hook signature differs per library (yt-dlp passes a status dict,
        # spotDL passes a tracker and a message); both are ignored.
        if self._event.is_set():
            raise JobAborted(self._label or "download aborted")
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ArmedAbort {self._label!r} set={self._event.is_set()}>"


def armed_abort(event, label: str = "") -> _ArmedAbort:
    """Build the abort signal for one job."""
    return _ArmedAbort(event, label)
