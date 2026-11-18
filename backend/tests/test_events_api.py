"""SSE endpoint headers (regression: the Next.js dev proxy gzipped and buffered the stream)."""

import asyncio
from types import SimpleNamespace

from app.api.batches import batch_events
from app.core.events import EventBus


def test_sse_response_is_not_transformed_by_proxies() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(event_bus=EventBus())))
    response = asyncio.run(batch_events("b1", request))  # type: ignore[arg-type]
    assert response.media_type == "text/event-stream"
    assert "no-transform" in response.headers["cache-control"]
    assert response.headers["x-accel-buffering"] == "no"
