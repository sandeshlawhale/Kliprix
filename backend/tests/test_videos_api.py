"""`POST /api/videos/inspect` and `POST /api/system/update-downloader`.

The yt-dlp network call is replaced by a fake adapter here (tests only); the real
adapter is exercised by the M2 "Done when" run on real links.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import system
from app.config import AppConfig
from app.core import hardware
from app.core.errors import AppError, ErrorCode
from app.core.proc import CommandResult, CommandTimeoutError
from app.sources.base import VideoMeta
from app.sources.youtube import YoutubeAdapter

GOOD = "https://youtu.be/aaaaaaaaaaa"
PRIVATE = "https://youtu.be/bbbbbbbbbbb"


class FakeYoutube(YoutubeAdapter):
    """Real URL handling; `inspect` answers from a table instead of the network."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__(config)
        self.inspected: list[str] = []

    def inspect(self, url: str) -> VideoMeta:
        self.inspected.append(url)
        if url.endswith("bbbbbbbbbbb"):
            raise AppError(ErrorCode.VIDEO_PRIVATE, "This video is private.")
        return VideoMeta(
            platform="youtube",
            source_id=url[-11:],
            title="A title",
            channel="A channel",
            duration_s=600.0,
            thumbnail_url="https://i.ytimg.com/vi/x/hqdefault.jpg",
            has_chat=True,
            is_live=False,
        )


@pytest.fixture
def fake(client: TestClient, config: AppConfig) -> FakeYoutube:
    adapter = FakeYoutube(config)
    client.app.state.source_adapters = [adapter]  # type: ignore[attr-defined]
    return adapter


def test_inspect_returns_per_link_results(client: TestClient, fake: FakeYoutube) -> None:
    urls = [GOOD, PRIVATE, "https://vimeo.com/1"]
    response = client.post("/api/videos/inspect", json={"urls": urls})
    assert response.status_code == 200
    results = response.json()["results"]
    assert [r["ok"] for r in results] == [True, False, False]
    assert [r["url"] for r in results] == urls
    assert results[0]["video"]["source_id"] == "aaaaaaaaaaa"
    assert results[0]["error"] is None
    assert results[1]["error"]["code"] == "VIDEO_PRIVATE"
    assert results[2]["error"]["code"] == "UNSUPPORTED_HOST"
    # The unsupported link never reached the adapter; the others were canonicalized.
    assert fake.inspected == [
        "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "https://www.youtube.com/watch?v=bbbbbbbbbbb",
    ]


def test_inspect_rejects_more_than_three_links(client: TestClient, fake: FakeYoutube) -> None:
    response = client.post("/api/videos/inspect", json={"urls": [GOOD] * 4})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TOO_MANY_VIDEOS"
    assert fake.inspected == []


@pytest.mark.parametrize("body", [{"urls": []}, {}, {"urls": [GOOD], "extra": 1}])
def test_inspect_validates_body(client: TestClient, fake: FakeYoutube, body: Any) -> None:
    response = client.post("/api/videos/inspect", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("live_status", "message"),
    [
        ("is_live", "Live streams are not supported yet."),
        ("is_upcoming", "This stream or premiere has not started yet."),
        ("post_live", "still processing"),
    ],
)
def test_live_and_upcoming_streams_are_rejected(
    config: AppConfig, live_status: str, message: str
) -> None:
    info = {"id": "aaaaaaaaaaa", "title": "t", "duration": 100, "live_status": live_status}
    with pytest.raises(AppError) as error:
        YoutubeAdapter(config)._meta(info)
    assert error.value.code == ErrorCode.VIDEO_UNAVAILABLE
    assert message in error.value.message


def test_meta_reads_chat_and_channel(config: AppConfig) -> None:
    info = {
        "id": "aaaaaaaaaaa",
        "title": "Stream",
        "uploader": "Uploader",
        "duration": 3600,
        "live_status": "was_live",
        "subtitles": {"live_chat": [{"ext": "json"}]},
    }
    meta = YoutubeAdapter(config)._meta(info)
    assert (meta.channel, meta.has_chat, meta.is_live, meta.duration_s) == (
        "Uploader",
        True,
        False,
        3600.0,
    )
    no_chat = YoutubeAdapter(config)._meta({**info, "subtitles": {}, "live_status": "not_live"})
    assert no_chat.has_chat is False


def test_update_downloader_reports_versions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    versions = iter(["2026.10.26", "2026.11.01"])
    calls: list[list[str]] = []

    def fake_run(args: list[str], timeout_s: float) -> CommandResult:
        calls.append(list(args))
        return CommandResult(0, "Successfully installed", "")

    monkeypatch.setattr(hardware, "ytdlp_version", lambda: next(versions))
    monkeypatch.setattr(system, "run_command", fake_run)
    response = client.post("/api/system/update-downloader")
    assert response.status_code == 200
    assert response.json() == {"before": "2026.10.26", "after": "2026.11.01"}
    assert calls[0][1:5] == ["-m", "pip", "install", "--upgrade"]
    assert calls[0][-2:] == ["yt-dlp", "yt-dlp-ejs"]


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_update_downloader_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    def fake_run(args: list[str], timeout_s: float) -> CommandResult:
        if failure == "timeout":
            raise CommandTimeoutError("pip timed out")
        return CommandResult(1, "", "ERROR: network unreachable")

    monkeypatch.setattr(system, "run_command", fake_run)
    response = client.post("/api/system/update-downloader")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "DOWNLOAD_FAILED"
