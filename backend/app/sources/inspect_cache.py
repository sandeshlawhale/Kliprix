"""Recent `inspect` results, kept in memory so a new batch can show each video's title and
channel before its pipeline starts (D-038). Nothing is written to disk; the pipeline
inspects the link again and overwrites these values when it runs."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock

from app.sources.base import VideoMeta

# Three links per batch; a few batches' worth is plenty.
MAX_ENTRIES = 32


class InspectCache:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._items: OrderedDict[str, VideoMeta] = OrderedDict()
        self._max = max_entries
        self._lock = Lock()

    def put(self, url: str, meta: VideoMeta) -> None:
        with self._lock:
            self._items[url] = meta
            self._items.move_to_end(url)
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def get(self, url: str) -> VideoMeta | None:
        with self._lock:
            return self._items.get(url)
