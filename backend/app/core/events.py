"""In-memory pub/sub for progress hints (docs/06 "Runtime model").

The job runner thread publishes; each SSE connection subscribes to one batch on the event
loop. Events are only hints to refetch: the database is the source of truth.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any

# Events kept per subscriber if a client reads slowly; older hints are dropped.
QUEUE_LIMIT = 500


@dataclass(frozen=True)
class Event:
    kind: str  # "video" | "clip" | "batch"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Subscriber:
    batch_id: str
    queue: asyncio.Queue[Event]
    loop: asyncio.AbstractEventLoop


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[_Subscriber] = []

    def subscribe(self, batch_id: str) -> asyncio.Queue[Event]:
        """A queue that receives this batch's events (call from the event loop)."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        subscriber = _Subscriber(batch_id, queue, asyncio.get_running_loop())
        with self._lock:
            self._subscribers.append(subscriber)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s.queue is not queue]

    def publish(self, batch_id: str, event: Event) -> None:
        """Send from any thread; never blocks the runner."""
        with self._lock:
            targets = [s for s in self._subscribers if s.batch_id == batch_id]
        for subscriber in targets:
            subscriber.loop.call_soon_threadsafe(_offer, subscriber.queue, event)


def _offer(queue: asyncio.Queue[Event], event: Event) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(event)
