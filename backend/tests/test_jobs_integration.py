"""Full pipeline through the API on synthetic videos: batch of 3 links, failure isolation,
resume after restart, cancel, and the clip edit → preview → export → download flow.

The source adapter copies ffmpeg-generated media, Whisper is a fake runner, and the LLM is
a fake completion (docs/12: the LLM is mocked in tests only)."""

import json
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.core.errors import AppError, ErrorCode
from app.core.proc import run_command
from app.main import create_app
from app.pipeline.transcribe import Segment, Word
from app.sources.base import ChatMessage, ProgressCallback, SourceCancelledError, VideoMeta
from app.sources.youtube import YoutubeAdapter

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
LENGTH = 240.0
KEY = "sk-integration-test-key-1234567"
FAILING_ID = "bbbbbbbbbbb"
SLOW_ID = "ccccccccccc"


@pytest.fixture(scope="module")
def media(tmp_path_factory: pytest.TempPathFactory) -> Path:
    assert FFMPEG is not None
    path = tmp_path_factory.mktemp("media") / "synthetic.mp4"
    args = [
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25:duration={LENGTH}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={LENGTH}",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path),
    ]  # fmt: skip
    assert run_command(args, 120).returncode == 0
    return path


class FakeSource(YoutubeAdapter):
    def __init__(self, config: AppConfig, media: Path) -> None:
        super().__init__(config)
        self.media = media
        self.release = threading.Event()
        self.release.set()
        self.downloads = 0

    def _fake_meta(self, url: str) -> VideoMeta:
        source_id = url.rsplit("=", 1)[-1]
        if source_id == FAILING_ID:
            raise AppError(ErrorCode.VIDEO_PRIVATE, "This video is private.")
        return VideoMeta(
            platform="youtube", source_id=source_id, title=f"Synthetic {source_id[:3]}",
            channel="Test", duration_s=LENGTH, thumbnail_url=None, has_chat=True, is_live=False,
        )  # fmt: skip

    def inspect(self, url: str) -> VideoMeta:
        return self._fake_meta(url)

    def download(
        self,
        url: str,
        dest_dir: Path,
        *,
        max_height: int,
        cancel: threading.Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[Path, VideoMeta]:
        meta = self._fake_meta(url)
        if meta.source_id == SLOW_ID:
            while not self.release.wait(0.05):
                if cancel is not None and cancel.is_set():
                    raise SourceCancelledError("cancelled")
        dest_dir.mkdir(parents=True, exist_ok=True)
        self.downloads += 1
        if on_progress is not None:
            on_progress(0.5)
        shutil.copyfile(self.media, dest_dir / "source.mp4")
        return dest_dir / "source.mp4", meta

    def get_chat(
        self, url: str, work_dir: Path, *, cancel: threading.Event | None = None
    ) -> list[ChatMessage] | None:
        # Two chat moments: the stronger first one is candidate 0 (the fake LLM's pick), the
        # second fills slot two (30 s clear of the first, not in the last 1.5%, D-033).
        return [ChatMessage(t=t, text="lol") for t in (40.0, 41.0, 42.0, 180.0, 181.0)]


class FakeWhisper:
    def transcribe(
        self,
        samples: Any,
        offset_s: float,
        language: str | None,
        initial_prompt: str | None,
        cancel: threading.Event | None,
        on_seconds: Callable[[float], None],
    ) -> tuple[list[Segment], str | None]:
        length = len(samples) / 16000
        words = [
            Word(text=f"word{int(offset_s + t)}", start=offset_s + t, end=offset_s + t + 0.4)
            for t in np.arange(0.0, length - 1, 1.0)
        ]
        on_seconds(length)
        return [Segment(start=offset_s, end=offset_s + length, text="words", words=words)], "en"


def fake_completion(**kwargs: Any) -> Any:
    # One clip with LLM metadata; the backend fills the rest from candidates. No candidate id,
    # so the pick does not depend on which window the signal weights rank first.
    text = (
        '{"clips":[{"candidate_id":null,"start":30,"end":70,"title":"Fake LLM pick",'
        '"youtube_title":"Fake LLM pick #shorts","youtube_description":"desc",'
        '"hashtags":["#test","#fake","#shorts"],"linkedin_post":"post","reason":"why",'
        '"score":0.9}]}'
    )
    user = str(kwargs["messages"][-1]["content"])
    if "\nClips:\n" in user:
        # The title step (D-043) for the clips the backend filled.
        ids = [int(line.split()[1].rstrip(":")) for line in user.splitlines()
               if line.startswith("Clip ")]  # fmt: skip
        text = json.dumps({"clips": [{"id": i, "title": f"Fake title {i}"} for i in ids]})
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def build(config: AppConfig, media: Path) -> Any:
    app = create_app(config)
    ctx = app.state.job_context
    ctx.adapters[:] = [FakeSource(config, media)]
    ctx.whisper_factory = lambda model, gpu: FakeWhisper
    # These runs test the pipeline with the fake Whisper; no real Parakeet model (D-028).
    ctx.parakeet_factory = None
    ctx.completion = fake_completion
    ctx.gpu_vram = lambda: None
    return app


@pytest.fixture
def app(config: AppConfig, media: Path) -> Any:
    return build(config, media)


@pytest.fixture
def client(app: Any) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.put(
            "/api/llm/session", json={"provider": "gemini", "model": "m", "api_key": KEY}
        )
        test_client.put("/api/settings", json={"style_defaults": {"clips_per_video": 2}})
        yield test_client


def url(source_id: str) -> str:
    return f"https://www.youtube.com/watch?v={source_id}"


def wait_for(client: TestClient, batch_id: str, done: Callable[[dict[str, Any]], bool],
             timeout_s: float = 240.0) -> dict[str, Any]:  # fmt: skip
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        batch: dict[str, Any] = client.get(f"/api/batches/{batch_id}").json()
        if done(batch):
            return batch
        time.sleep(0.3)
    raise AssertionError(f"timed out: {batch}")


def finished(batch: dict[str, Any]) -> bool:
    return batch["status"] in ("done", "partial", "failed", "cancelled")


def test_batch_needs_llm_session(config: AppConfig, media: Path) -> None:
    with TestClient(build(config, media), raise_server_exceptions=False) as plain:
        response = plain.post("/api/batches", json={"urls": [url("aaaaaaaaaaa")]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LLM_NOT_CONFIGURED"


def test_batch_rejects_bad_links(client: TestClient) -> None:
    too_many = client.post("/api/batches", json={"urls": [url("aaaaaaaaaaa")] * 4})
    assert too_many.json()["error"]["code"] == "TOO_MANY_VIDEOS"
    other = client.post("/api/batches", json={"urls": ["https://vimeo.com/1"]})
    assert other.json()["error"]["code"] == "UNSUPPORTED_HOST"


def test_three_links_run_unattended_and_failure_is_isolated(
    client: TestClient, config: AppConfig
) -> None:
    created = client.post(
        "/api/batches", json={"urls": [url("aaaaaaaaaaa"), url(FAILING_ID), url("ddddddddddd")]}
    )
    assert created.status_code == 201
    batch_id = created.json()["id"]
    batch = wait_for(client, batch_id, finished)
    assert batch["status"] == "partial"
    ok_a, failed, ok_b = batch["videos"]
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "VIDEO_PRIVATE"
    assert failed["checkpoints"][0]["state"] == "failed"
    for video in (ok_a, ok_b):
        assert video["status"] == "done"
        assert [c["key"] for c in video["checkpoints"]] == [
            "download", "chat", "signals", "candidates", "transcribe", "select", "edit"
        ]  # fmt: skip
        assert all(c["state"] == "done" for c in video["checkpoints"])
        assert video["checkpoints"][-1]["detail"] == "2/2"
        assert len(video["clips"]) == 2
        for clip in video["clips"]:
            assert clip["status"] == "done"
            assert clip["has_export"] is True
            assert clip["thumbnail_url"]
    assert ok_a["clips"][0]["title"] == "Fake LLM pick"
    history = client.get("/api/batches").json()
    assert history["items"][0]["id"] == batch_id
    assert history["items"][0]["clip_count"] == 4
    # Exports live in exports/<batch>/<video_slug>/clip_NN_<slug>.mp4
    exports = sorted((config.data_dir / "exports" / batch_id).rglob("*.mp4"))
    assert len(exports) == 4
    assert {p.parent.name for p in exports} == {"synthetic-aaa", "synthetic-ddd"}
    assert "clip_01_fake-llm-pick.mp4" in {p.name for p in exports}
    assert KEY.encode() not in (config.logs_dir / "app.log").read_bytes()


def test_edit_preview_export_download(client: TestClient) -> None:
    batch_id = client.post("/api/batches", json={"urls": [url("aaaaaaaaaaa")]}).json()["id"]
    batch = wait_for(client, batch_id, finished)
    clip_id = batch["videos"][0]["clips"][0]["id"]
    clip = client.get(f"/api/clips/{clip_id}").json()
    assert clip["source_bounds"] == {"min_start_s": 0.0, "max_end_s": LENGTH}
    too_long_title = client.patch(f"/api/clips/{clip_id}", json={"title": "x" * 41})
    assert too_long_title.json()["error"]["code"] == "VALIDATION_ERROR"
    outside = client.patch(f"/api/clips/{clip_id}", json={"end_s": LENGTH + 10})
    assert outside.json()["error"]["code"] == "VALIDATION_ERROR"
    text_only = client.patch(f"/api/clips/{clip_id}", json={"youtube_title": "New YT title"})
    assert text_only.json()["edited"] is False
    bad_box = {"start": 2, "end": 1, "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
    refused = client.patch(f"/api/clips/{clip_id}", json={"blur_boxes": [bad_box]})
    assert refused.json()["error"]["code"] == "VALIDATION_ERROR"
    box = {"start": 0.5, "end": 2.0, "x": 0.25, "y": 0.3, "w": 0.5, "h": 0.2}
    edited = client.patch(
        f"/api/clips/{clip_id}",
        json={
            "start_s": clip["start_s"] - 2, "caption_style": "boxed", "blur": 10,
            "blur_boxes": [box],
        },
    ).json()  # fmt: skip
    assert edited["edit"]["blur_boxes"] == [box]
    assert edited["edited"] is True
    assert edited["has_export"] is False
    assert edited["words"][0]["start"] >= 0
    assert client.get(f"/api/clips/{clip_id}/download").json()["error"]["code"] == "CONFLICT"
    assert client.post(f"/api/clips/{clip_id}/preview").status_code == 202
    wait_for(client, batch_id, lambda b: b["videos"][0]["clips"][0]["has_preview"])
    media = client.get(f"/api/clips/{clip_id}/media/preview", headers={"Range": "bytes=0-99"})
    assert media.status_code == 206
    assert len(media.content) == 100
    assert client.post(f"/api/clips/{clip_id}/render").status_code == 202
    wait_for(client, batch_id, lambda b: b["videos"][0]["clips"][0]["has_export"])
    download = client.get(f"/api/clips/{clip_id}/download")
    assert download.status_code == 200
    assert 'attachment; filename="Fake LLM pick.mp4"' in download.headers["content-disposition"]
    # Edits survive a reload.
    again = client.get(f"/api/clips/{clip_id}").json()
    assert again["edit"]["caption_style"] == "boxed"
    assert again["edit"]["blur_boxes"] == [box]
    assert again["metadata"]["youtube_title"] == "New YT title"


def test_cancel_stops_running_download(client: TestClient, app: Any) -> None:
    source: FakeSource = app.state.job_context.adapters[0]
    source.release.clear()
    batch_id = client.post("/api/batches", json={"urls": [url(SLOW_ID)]}).json()["id"]
    wait_for(client, batch_id, lambda b: b["videos"][0]["stage"] == "download")
    cancelled = client.post(f"/api/batches/{batch_id}/cancel").json()
    assert cancelled["status"] == "cancelled"
    batch = wait_for(client, batch_id, lambda b: b["videos"][0]["status"] == "cancelled")
    assert batch["status"] == "cancelled"
    source.release.set()
    assert client.delete(f"/api/batches/{batch_id}?delete_files=true").status_code == 204
    assert client.get(f"/api/batches/{batch_id}").status_code == 404


def _all_rendered(clips: int) -> Callable[[dict[str, Any]], bool]:
    def check(batch: dict[str, Any]) -> bool:
        video = batch["videos"][0]
        return (
            finished(batch)
            and len(video["clips"]) == clips
            and all(clip["status"] == "done" for clip in video["clips"])
        )

    return check


def test_more_clips_reuse_the_download(config: AppConfig, media: Path) -> None:
    app = build(config, media)
    ctx = app.state.job_context
    source: FakeSource = ctx.adapters[0]
    prompts: list[str] = []

    def recording(**kwargs: Any) -> Any:
        prompts.append(str(kwargs.get("messages")))
        return fake_completion(**kwargs)

    ctx.completion = recording
    with TestClient(app, raise_server_exceptions=False) as client:
        client.put("/api/llm/session", json={"provider": "gemini", "model": "m", "api_key": KEY})
        client.put("/api/settings", json={"style_defaults": {"clips_per_video": 1}})
        batch_id = client.post("/api/batches", json={"urls": [url("aaaaaaaaaaa")]}).json()["id"]
        video = wait_for(client, batch_id, _all_rendered(1))["videos"][0]
        assert video["can_add_clips"] is True
        first = video["clips"][0]
        downloads = source.downloads
        queued = client.post(f"/api/videos/{video['id']}/more-clips", json={"count": 1})
        assert queued.status_code == 202
        batch = wait_for(client, batch_id, _all_rendered(2))
    video = batch["videos"][0]
    assert [clip["index"] for clip in video["clips"]] == [0, 1]
    new = video["clips"][1]
    # Never on top of (or within 30 s of) the clip already made.
    assert new["start_s"] >= first["end_s"] + 30 or new["end_s"] <= first["start_s"] - 30
    assert source.downloads == downloads
    assert any("Clips already made (do not repeat these moments)" in p for p in prompts)
    assert video["note"].endswith("More clips: 1 more clip added.")
    assert all(step["state"] in ("done", "skipped") for step in video["checkpoints"])


def test_more_clips_refused_while_running_or_without_the_download(
    client: TestClient, app: Any
) -> None:
    source: FakeSource = app.state.job_context.adapters[0]
    source.release.clear()
    batch_id = client.post("/api/batches", json={"urls": [url(SLOW_ID)]}).json()["id"]
    video_id = client.get(f"/api/batches/{batch_id}").json()["videos"][0]["id"]
    busy = client.post(f"/api/videos/{video_id}/more-clips", json={"count": 2})
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "CONFLICT"
    source.release.set()
    batch = wait_for(client, batch_id, finished)
    assert batch["videos"][0]["can_add_clips"] is True
    cleaned = client.post(
        "/api/storage/cleanup", json={"scope": "source_videos", "batch_id": batch_id}
    )
    assert cleaned.status_code == 200
    video = client.get(f"/api/batches/{batch_id}").json()["videos"][0]
    assert video["can_add_clips"] is False
    assert "deleted" in video["add_clips_blocked"]
    gone = client.post(f"/api/videos/{video_id}/more-clips", json={"count": 2})
    assert gone.json()["error"]["code"] == "CONFLICT"
    assert client.post("/api/videos/nope/more-clips", json={"count": 1}).status_code == 404
    too_many = client.post(f"/api/videos/{video_id}/more-clips", json={"count": 6})
    assert too_many.json()["error"]["code"] == "VALIDATION_ERROR"


def test_resume_after_restart(config: AppConfig, media: Path) -> None:
    first = build(config, media)
    source: FakeSource = first.state.job_context.adapters[0]
    source.release.clear()
    with TestClient(first, raise_server_exceptions=False) as client:
        client.put("/api/llm/session", json={"provider": "gemini", "model": "m", "api_key": KEY})
        client.put("/api/settings", json={"style_defaults": {"clips_per_video": 1}})
        batch_id = client.post("/api/batches", json={"urls": [url(SLOW_ID)]}).json()["id"]
        wait_for(client, batch_id, lambda b: b["videos"][0]["stage"] == "download")
    # The app stopped mid-download (the runner's job was interrupted, not cancelled by a user).
    second = build(config, media)
    with TestClient(second, raise_server_exceptions=False) as client:
        client.put("/api/llm/session", json={"provider": "gemini", "model": "m", "api_key": KEY})
        batch = wait_for(client, batch_id, finished)
    assert batch["videos"][0]["status"] == "done"
    assert batch["status"] == "done"
