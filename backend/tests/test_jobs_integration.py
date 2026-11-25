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


def settled(batch: dict[str, Any]) -> bool:
    """Finished and no clip still rendering (edits are refused while a clip renders)."""
    return finished(batch) and all(
        clip["status"] not in ("queued", "running")
        for video in batch["videos"]
        for clip in video["clips"]
    )


def test_edit_preview_export_download(client: TestClient) -> None:
    batch_id = client.post("/api/batches", json={"urls": [url("aaaaaaaaaaa")]}).json()["id"]
    batch = wait_for(client, batch_id, settled)
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
    wait_for(client, batch_id, lambda b: b["videos"][0]["clips"][0]["has_preview"] and settled(b))
    media = client.get(f"/api/clips/{clip_id}/media/preview", headers={"Range": "bytes=0-99"})
    assert media.status_code == 206
    assert len(media.content) == 100
    # D-057: the preview render also made the live-preview base, which survives edits the
    # editor draws itself (title, words, blur boxes) and goes away with a layout change.
    live = client.get(f"/api/clips/{clip_id}").json()["live"]
    assert live is not None and live["caption_y"] > live["title_y"]
    assert client.get(f"/api/clips/{clip_id}/media/base").status_code == 200
    drawn = client.patch(f"/api/clips/{clip_id}", json={"title": "Live", "blur_boxes": []})
    assert drawn.json()["live"] == live
    layout = client.patch(f"/api/clips/{clip_id}", json={"blur": 12}).json()
    assert layout["live"] is None
    assert client.get(f"/api/clips/{clip_id}/media/base").json()["error"]["code"] == "NOT_FOUND"
    client.patch(f"/api/clips/{clip_id}", json={"title": "Fake LLM pick", "blur_boxes": [box]})
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


def test_editor_parts_split_delete_and_extend(client: TestClient) -> None:
    """D-059: the editor sends the played parts; a cut makes a montage, one part a trim."""
    batch_id = client.post("/api/batches", json={"urls": [url("aaaaaaaaaaa")]}).json()["id"]
    batch = wait_for(client, batch_id, settled)
    clip_id = batch["videos"][0]["clips"][0]["id"]
    clip = client.get(f"/api/clips/{clip_id}").json()
    start, end = clip["start_s"], clip["end_s"]
    # Cut out 2 s in the middle (split twice, delete the piece).
    middle = round((start + end) / 2, 2)
    cut = client.patch(
        f"/api/clips/{clip_id}", json={"parts": [[start, middle - 1], [middle + 1, end]]}
    ).json()
    assert cut["edit"]["montage"] is True
    assert cut["edit"]["parts"] == [[start, middle - 1], [middle + 1, end]]
    assert abs(cut["duration_s"] - (end - start - 2)) < 0.02
    assert cut["edited"] is True
    assert all(w["end"] <= cut["duration_s"] + 0.01 for w in cut["words"])
    # D-062: words sent with a cut are in the new clip's time and are kept as sent.
    sent = client.patch(
        f"/api/clips/{clip_id}",
        json={
            "parts": [[start, middle - 2], [middle + 1, end]],
            "words": [{"text": "synced", "start": 1.0, "end": 1.5}],
        },
    ).json()
    assert {"text": "synced", "start": 1.0, "end": 1.5} in sent["words"]
    # Touching parts merge into one; extending the start by 3 s is a plain trim.
    whole = client.patch(
        f"/api/clips/{clip_id}", json={"parts": [[start - 3, middle], [middle, end]]}
    ).json()
    assert whole["edit"]["montage"] is False
    assert (whole["start_s"], whole["end_s"]) == (start - 3, end)
    too_short = client.patch(
        f"/api/clips/{clip_id}", json={"parts": [[start, start + 0.2], [middle, end]]}
    )
    assert too_short.json()["error"]["code"] == "VALIDATION_ERROR"
    outside = client.patch(f"/api/clips/{clip_id}", json={"parts": [[start, LENGTH + 5]]})
    assert outside.json()["error"]["code"] == "VALIDATION_ERROR"


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


def test_pause_holds_the_batch_and_resume_finishes_it(client: TestClient, app: Any) -> None:
    """D-049: pause stops the running download, other batches still run, resume finishes."""
    source: FakeSource = app.state.job_context.adapters[0]
    source.release.clear()
    batch_id = client.post("/api/batches", json={"urls": [url(SLOW_ID)]}).json()["id"]
    wait_for(client, batch_id, lambda b: b["videos"][0]["stage"] == "download")
    paused = client.post(f"/api/batches/{batch_id}/pause").json()
    assert paused["status"] == "paused"
    batch = wait_for(client, batch_id, lambda b: b["videos"][0]["status"] == "queued")
    assert batch["status"] == "paused"
    # Another batch runs while this one is paused.
    other = client.post("/api/batches", json={"urls": [url("aaaaaaaaaaa")]}).json()["id"]
    assert wait_for(client, other, finished)["status"] == "done"
    assert client.get(f"/api/batches/{batch_id}").json()["status"] == "paused"
    source.release.set()
    resumed = client.post(f"/api/batches/{batch_id}/resume").json()
    assert resumed["status"] in ("queued", "running")
    batch = wait_for(client, batch_id, finished)
    assert batch["status"] == "done"
    assert all(clip["status"] == "done" for clip in batch["videos"][0]["clips"])


def test_paused_batch_can_be_cancelled(client: TestClient, app: Any) -> None:
    source: FakeSource = app.state.job_context.adapters[0]
    source.release.clear()
    batch_id = client.post("/api/batches", json={"urls": [url(SLOW_ID)]}).json()["id"]
    wait_for(client, batch_id, lambda b: b["videos"][0]["stage"] == "download")
    client.post(f"/api/batches/{batch_id}/pause")
    wait_for(client, batch_id, lambda b: b["videos"][0]["status"] == "queued")
    assert client.post(f"/api/batches/{batch_id}/cancel").json()["status"] == "cancelled"
    batch = client.get(f"/api/batches/{batch_id}").json()
    assert batch["videos"][0]["status"] == "cancelled"
    source.release.set()
    # Resume does nothing on a cancelled batch.
    assert client.post(f"/api/batches/{batch_id}/resume").json()["status"] == "cancelled"


def _all_rendered(clips: int) -> Callable[[dict[str, Any]], bool]:
    def check(batch: dict[str, Any]) -> bool:
        video = batch["videos"][0]
        return (
            finished(batch)
            and len(video["clips"]) == clips
            and all(clip["status"] == "done" for clip in video["clips"])
        )

    return check


def test_custom_clip_from_a_picked_range(config: AppConfig, media: Path) -> None:
    """D-061: a range picked by hand becomes one more clip, rendered like the others."""
    app = build(config, media)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.put("/api/llm/session", json={"provider": "gemini", "model": "m", "api_key": KEY})
        client.put("/api/settings", json={"style_defaults": {"clips_per_video": 1}})
        batch_id = client.post("/api/batches", json={"urls": [url("aaaaaaaaaaa")]}).json()["id"]
        video = wait_for(client, batch_id, _all_rendered(1))["videos"][0]
        source = client.get(f"/api/videos/{video['id']}/source", headers={"Range": "bytes=0-99"})
        assert source.status_code == 206 and len(source.content) == 100
        short = client.post(f"/api/videos/{video['id']}/custom-clip",
                            json={"start_s": 10, "end_s": 12})  # fmt: skip
        assert short.json()["error"]["code"] == "VALIDATION_ERROR"
        past = client.post(f"/api/videos/{video['id']}/custom-clip",
                           json={"start_s": LENGTH - 10, "end_s": LENGTH + 20})  # fmt: skip
        assert past.json()["error"]["code"] == "VALIDATION_ERROR"
        queued = client.post(f"/api/videos/{video['id']}/custom-clip",
                             json={"start_s": 20, "end_s": 45.5})  # fmt: skip
        assert queued.status_code == 202
        done = wait_for(client, batch_id, _all_rendered(2))["videos"][0]
        custom = done["clips"][1]
        assert (custom["start_s"], custom["end_s"]) == (20, 45.5)
        assert custom["index"] == 1 and custom["title"]
        assert "a clip you picked" in (done["note"] or "")
        detail = client.get(f"/api/clips/{custom['id']}").json()
        assert all(0 <= w["start"] <= 25.5 for w in detail["words"])


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
        # A newer batch is on top of the history until More clips moves the old one up (D-055).
        newer = client.post("/api/batches", json={"urls": [url("ddddddddddd")]}).json()["id"]
        wait_for(client, newer, finished)
        history = client.get("/api/batches").json()["items"]
        assert [item["id"] for item in history[:2]] == [newer, batch_id]
        assert not any(item["working"] for item in history)
        downloads = source.downloads
        queued = client.post(f"/api/videos/{video['id']}/more-clips", json={"count": 1})
        assert queued.status_code == 202
        top = client.get("/api/batches").json()["items"][0]
        assert top["id"] == batch_id
        assert top["working"] is True
        assert top["last_activity_at"] > top["created_at"]
        batch = wait_for(client, batch_id, _all_rendered(2))
        assert client.get("/api/batches").json()["items"][0]["working"] is False
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


def test_montage_toggle_retimes_words_and_renders_only_the_parts(
    client: TestClient, app: Any, config: AppConfig
) -> None:
    """D-051: a clip with planned parts plays only them when montage is on: the API length,
    the caption words and the rendered preview follow; switching it off restores the range."""
    from app.db.repositories import batches as repo
    from app.render.compose import probe_streams

    batch_id = client.post("/api/batches", json={"urls": [url("ddddddddddd")]}).json()["id"]
    batch = wait_for(client, batch_id, finished)
    clip_id = batch["videos"][0]["clips"][0]["id"]
    plain = client.patch(f"/api/clips/{clip_id}", json={"start_s": 10.0, "end_s": 40.0}).json()
    assert plain["duration_s"] == 30.0
    assert plain["edit"]["parts"] == [] and plain["edit"]["montage"] is False
    refused = client.patch(f"/api/clips/{clip_id}", json={"montage": True})
    assert refused.json()["error"]["code"] == "VALIDATION_ERROR"
    with app.state.job_context.session_factory() as session:
        row = repo.get_clip(session, clip_id)
        assert row is not None
        row.parts_json = json.dumps([[10.0, 16.0], [24.0, 40.0]])
        session.commit()
    montage = client.patch(f"/api/clips/{clip_id}", json={"montage": True}).json()
    assert montage["edit"]["montage"] is True
    assert montage["edit"]["parts"] == [[10.0, 16.0], [24.0, 40.0]]
    assert montage["duration_s"] == 22.0
    assert all(word["end"] <= 22.0 + 0.01 for word in montage["words"])
    assert client.post(f"/api/clips/{clip_id}/preview").status_code == 202
    wait_for(client, batch_id, lambda b: b["videos"][0]["clips"][0]["has_preview"])
    preview = config.data_dir / "previews" / f"{clip_id}.mp4"
    assert probe_streams(preview, None).video_s == pytest.approx(22.0, abs=0.1)
    whole = client.patch(f"/api/clips/{clip_id}", json={"montage": False}).json()
    assert whole["duration_s"] == 30.0
    assert whole["edit"]["parts"] == [[10.0, 16.0], [24.0, 40.0]]


class AuthenticationError(Exception):
    """Named like the provider error, so the client fails at once (no retry wait)."""


def test_failed_video_resumes_from_the_failed_step(config: AppConfig, media: Path) -> None:
    app = build(config, media)
    ctx = app.state.job_context
    source: FakeSource = ctx.adapters[0]
    llm_down = threading.Event()
    llm_down.set()

    def flaky(**kwargs: Any) -> Any:
        if llm_down.is_set():
            raise AuthenticationError("provider said no")
        return fake_completion(**kwargs)

    ctx.completion = flaky
    with TestClient(app, raise_server_exceptions=False) as client:
        client.put("/api/llm/session", json={"provider": "gemini", "model": "m", "api_key": KEY})
        client.put("/api/settings", json={"style_defaults": {"clips_per_video": 1}})
        batch_id = client.post("/api/batches", json={"urls": [url("aaaaaaaaaaa")]}).json()["id"]
        failed = wait_for(client, batch_id, finished)
        video = failed["videos"][0]
        assert (video["status"], video["stage"]) == ("failed", "select")
        assert video["error"]["code"] == "LLM_AUTH_FAILED"
        states = {step["key"]: step["state"] for step in video["checkpoints"]}
        assert states["download"] == "done" and states["select"] == "failed"
        downloads = source.downloads

        # Not allowed while the AI model is not configured, nor for a video that did not fail.
        client.delete("/api/llm/session")
        no_llm = client.post(f"/api/videos/{video['id']}/retry")
        assert no_llm.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
        client.put("/api/llm/session", json={"provider": "gemini", "model": "m", "api_key": KEY})
        assert client.post("/api/videos/nope/retry").status_code == 404

        llm_down.clear()
        resumed = client.post(f"/api/videos/{video['id']}/retry")
        assert resumed.status_code == 202
        assert resumed.json()["videos"][0]["status"] in ("queued", "running", "done")
        batch = wait_for(client, batch_id, _all_rendered(1))
        again = client.post(f"/api/videos/{video['id']}/retry")
        assert again.json()["error"]["code"] == "CONFLICT"
    video = batch["videos"][0]
    assert video["status"] == "done" and video["error"] is None
    assert batch["status"] == "done"
    assert len(video["clips"]) == 1
    # The saved download was reused: no second download.
    assert source.downloads == downloads
