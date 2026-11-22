"""Queued videos are named from the home page's link check (D-038)."""

from pathlib import Path

from app.db.engine import make_engine, make_session_factory
from app.db.migrate import upgrade_to_head
from app.db.repositories import batches as repo
from app.sources.base import VideoMeta
from app.sources.inspect_cache import InspectCache


def _meta(source_id: str) -> VideoMeta:
    return VideoMeta(
        platform="youtube", source_id=source_id, title=f"Title {source_id}", channel="Chan",
        duration_s=60.0, thumbnail_url="https://i.ytimg.com/x.jpg", has_chat=False,
        is_live=False,
    )  # fmt: skip


def test_cache_keeps_the_newest_entries() -> None:
    cache = InspectCache(max_entries=2)
    cache.put("a", _meta("a"))
    cache.put("b", _meta("b"))
    cache.put("c", _meta("c"))
    assert cache.get("a") is None
    assert cache.get("c") is not None and cache.get("c").title == "Title c"  # type: ignore[union-attr]


def test_create_batch_fills_known_metadata(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    upgrade_to_head(url)
    factory = make_session_factory(make_engine(url))
    with factory() as session:
        known = {"https://youtu.be/a": _meta("a")}
        batch = repo.create_batch(
            session, ["https://youtu.be/a", "https://youtu.be/b"], {}, None, known=known
        )
        first, second = repo.videos_of(session, batch.id)
        assert (first.title, first.channel, first.thumbnail_path) == (
            "Title a", "Chan", "https://i.ytimg.com/x.jpg",
        )  # fmt: skip
        assert first.status == "queued"
        assert second.title is None
