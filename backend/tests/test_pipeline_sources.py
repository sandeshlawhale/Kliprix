"""Download and chat stages on a synthetic ffmpeg video (no network, no copyrighted media)."""

import shutil
import threading
from collections import namedtuple
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from yt_dlp import YoutubeDL

from app.config import DOWNLOAD_FORMAT_SORT, AppConfig
from app.core.errors import AppError, ErrorCode
from app.core.hardware import MemoryInfo
from app.core.proc import run_command
from app.pipeline import download as download_stage
from app.pipeline.chat import CHAT_FILE_NAME, run_chat
from app.pipeline.download import INFO_FILE_NAME, max_download_height, run_download
from app.sources import youtube
from app.sources.base import ChatMessage, ProgressCallback, SourceCancelledError, VideoMeta
from app.sources.youtube import YoutubeAdapter

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
URL = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
CLIP_S = 3


@pytest.fixture(scope="module")
def synthetic_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    assert FFMPEG is not None
    path = tmp_path_factory.mktemp("media") / "synthetic.mp4"
    args = [
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x180:rate=25:duration={CLIP_S}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={CLIP_S}",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    assert run_command(args, 60).returncode == 0
    return path


class FakeSource(YoutubeAdapter):
    """Copies a local file instead of downloading; returns canned chat."""

    def __init__(self, config: AppConfig, media: Path, chat: list[ChatMessage] | None) -> None:
        super().__init__(config)
        self.media = media
        self.chat = chat
        self.downloads = 0
        self.chat_calls = 0

    def download(
        self,
        url: str,
        dest_dir: Path,
        *,
        max_height: int,
        cancel: threading.Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[Path, VideoMeta]:
        self.downloads += 1
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / "source.mp4"
        shutil.copyfile(self.media, target)
        meta = VideoMeta(
            platform="youtube",
            source_id="aaaaaaaaaaa",
            title="Synthetic",
            channel=None,
            duration_s=CLIP_S,
            thumbnail_url=None,
            has_chat=self.chat is not None,
            is_live=False,
        )
        return target, meta

    def get_chat(
        self, url: str, work_dir: Path, *, cancel: threading.Event | None = None
    ) -> list[ChatMessage] | None:
        self.chat_calls += 1
        return self.chat


@needs_ffmpeg
def test_download_writes_info_and_reuses_valid_cache(
    config: AppConfig, synthetic_mp4: Path, tmp_path: Path
) -> None:
    source = FakeSource(config, synthetic_mp4, None)
    dest = tmp_path / "video one"
    info = run_download(source, URL, dest, ffmpeg_path=None, max_height=1080)
    assert (info.width, info.height) == (320, 180)
    assert info.probed_duration_s == pytest.approx(CLIP_S, abs=0.2)
    assert (dest / INFO_FILE_NAME).is_file()
    again = run_download(source, URL, dest, ffmpeg_path=None, max_height=1080)
    assert again == info
    assert source.downloads == 1


@needs_ffmpeg
def test_broken_cache_is_downloaded_again(
    config: AppConfig, synthetic_mp4: Path, tmp_path: Path
) -> None:
    source = FakeSource(config, synthetic_mp4, None)
    dest = tmp_path / "v"
    run_download(source, URL, dest, ffmpeg_path=None, max_height=1080)
    (dest / "source.mp4").write_bytes(b"truncated")
    run_download(source, URL, dest, ffmpeg_path=None, max_height=1080)
    assert source.downloads == 2


@needs_ffmpeg
def test_unplayable_download_is_an_error(config: AppConfig, tmp_path: Path) -> None:
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video")
    with pytest.raises(AppError) as error:
        run_download(
            FakeSource(config, junk, None), URL, tmp_path / "v", ffmpeg_path=None, max_height=720
        )
    assert error.value.code == ErrorCode.DOWNLOAD_FAILED


def test_missing_ffprobe_is_reported(config: AppConfig, tmp_path: Path) -> None:
    missing = str(tmp_path / "no ffmpeg" / "ffmpeg.exe")
    with pytest.raises(AppError) as error:
        download_stage.probe_video(tmp_path / "x.mp4", missing)
    assert error.value.code == ErrorCode.FFMPEG_MISSING


def test_download_height_follows_ram() -> None:
    assert max_download_height(MemoryInfo(3.8, 2.0)) == 720
    assert max_download_height(MemoryInfo(5.9, 2.0)) == 720
    assert max_download_height(MemoryInfo(7.6, 2.0)) == 1080


def test_chat_written_once_and_absent_chat_writes_nothing(
    config: AppConfig, tmp_path: Path
) -> None:
    messages = [ChatMessage(t=1.0, text="hi"), ChatMessage(t=2.5, text="lol")]
    with_chat = FakeSource(config, tmp_path, messages)
    chat = run_chat(with_chat, URL, tmp_path / "a")
    assert chat is not None
    assert chat.items == messages
    assert (
        (tmp_path / "a" / CHAT_FILE_NAME)
        .read_text(encoding="utf-8")
        .startswith('{"version":1,"items":[{"t":1.0,"text":"hi"}')
    )
    assert run_chat(with_chat, URL, tmp_path / "a") == chat
    assert with_chat.chat_calls == 1

    without = FakeSource(config, tmp_path, None)
    assert run_chat(without, URL, tmp_path / "b") is None
    assert not (tmp_path / "b" / CHAT_FILE_NAME).exists()


def test_progress_covers_video_and_audio_parts() -> None:
    seen: list[float] = []
    tracker = youtube._ProgressTracker(None, seen.append)
    tracker.parts = 2
    tracker.hook(
        {"status": "downloading", "filename": "v", "downloaded_bytes": 50, "total_bytes": 100}
    )
    tracker.hook({"status": "finished", "filename": "v"})
    tracker.hook(
        {
            "status": "downloading",
            "filename": "a",
            "downloaded_bytes": 10,
            "total_bytes_estimate": 20,
        }
    )
    tracker.hook({"status": "finished", "filename": "a"})
    assert seen == [0.25, 0.5, 0.75, 1.0]


def test_progress_on_fragmented_download_is_monotonic() -> None:
    """Shape of the real hooks for an HLS video part + https audio part (M2 run)."""
    seen: list[float] = []
    tracker = youtube._ProgressTracker(None, seen.append)
    tracker.parts = 2
    v, a = "source.f616.mp4", "source.f251.webm"
    events: list[dict[str, Any]] = [
        # First event: estimate equals bytes so far; must not count as a finished part.
        {"status": "downloading", "filename": v, "downloaded_bytes": 712,
         "total_bytes_estimate": 712, "fragment_index": 0, "fragment_count": 55},
        {"status": "downloading", "filename": v, "downloaded_bytes": 712,
         "total_bytes_estimate": 78320.0, "fragment_index": 1, "fragment_count": 55},
        {"status": "downloading", "filename": v, "downloaded_bytes": 1736,
         "total_bytes_estimate": 19580.0, "fragment_index": 1, "fragment_count": 55},
        {"status": "downloading", "filename": v, "downloaded_bytes": 9_000_000,
         "total_bytes_estimate": 72_108_410.0, "fragment_index": 30, "fragment_count": 55},
        {"status": "finished", "filename": v},
        {"status": "downloading", "filename": a, "downloaded_bytes": 2_000_000,
         "total_bytes": 4_469_727},
        {"status": "downloading", "filename": a, "downloaded_bytes": 4_469_727,
         "total_bytes": 4_469_727},
        {"status": "finished", "filename": a},
    ]  # fmt: skip
    for event in events:
        tracker.hook(event)
    assert seen == sorted(seen)
    assert seen[0] == pytest.approx(1 / 55 / 2)
    assert seen[1] == pytest.approx(30 / 55 / 2)
    assert seen[2] == 0.5
    assert seen[-2] == pytest.approx(0.5 + 0.99 / 2)
    assert seen[-1] == 1.0


def test_cancel_stops_the_download() -> None:
    cancel = threading.Event()
    tracker = youtube._ProgressTracker(cancel, None)
    tracker.hook({"status": "downloading"})
    cancel.set()
    with pytest.raises(SourceCancelledError):
        tracker.hook({"status": "downloading"})


DiskUsage = namedtuple("DiskUsage", "total used free")


@pytest.mark.parametrize(
    ("free_gb", "info", "fails"),
    [
        (1.0, {"requested_formats": [{"filesize": 2 * 1024**3}, {"filesize_approx": 1}]}, True),
        (3.0, {"requested_formats": [{"filesize": 2 * 1024**3}, {"filesize_approx": 1}]}, False),
        (1.5, {"requested_formats": [{"filesize": None}]}, True),  # unknown size needs 2 GB
        (2.5, {}, False),
    ],
)
def test_disk_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    free_gb: float,
    info: dict[str, Any],
    fails: bool,
) -> None:
    usage = DiskUsage(0, 0, int(free_gb * 1024**3))
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage)
    if fails:
        with pytest.raises(AppError) as error:
            youtube._check_disk(tmp_path, info)
        assert error.value.code == ErrorCode.DISK_FULL
    else:
        youtube._check_disk(tmp_path, info)


def test_download_without_ffmpeg_fails_fast(config: AppConfig, tmp_path: Path) -> None:
    adapter = YoutubeAdapter(replace(config, ffmpeg_path=str(tmp_path / "none" / "ffmpeg")))
    with pytest.raises(AppError) as error:
        adapter.download(URL, tmp_path / "v", max_height=720)
    assert error.value.code == ErrorCode.FFMPEG_MISSING


def _video_format(format_id: str, vcodec: str, height: int) -> dict[str, Any]:
    return {
        "format_id": format_id,
        "url": f"https://example.invalid/{format_id}",
        "ext": "mp4",
        "protocol": "https",
        "vcodec": vcodec,
        "acodec": "none",
        "height": height,
        "width": height * 16 // 9,
        "fps": 30,
    }


@pytest.mark.parametrize(
    ("formats", "expected"),
    [
        # Same height: H.264 wins over AV1 and VP9 (D-026).
        (
            [("399", "av01.0.08M.08", 1080), ("248", "vp9", 1080), ("137", "avc1.640028", 1080)],
            "137",
        ),
        # Resolution still comes first: 1080p VP9 beats 720p H.264.
        ([("248", "vp9", 1080), ("136", "avc1.4d401f", 720)], "248"),
        # The height cap still applies.
        ([("401", "av01.0.12M.08", 2160), ("137", "avc1.640028", 1080)], "137"),
    ],
)
def test_format_choice_prefers_h264_at_same_resolution(
    formats: list[tuple[str, str, int]], expected: str
) -> None:
    """Runs yt-dlp's real format selection (no network) with our format and sort."""
    audio = {"format_id": "140", "url": "https://example.invalid/a", "ext": "m4a",
             "protocol": "https", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128}  # fmt: skip
    info = {
        "id": "aaaaaaaaaaa",
        "title": "t",
        "extractor": "youtube",
        "extractor_key": "Youtube",
        "webpage_url": URL,
        "formats": [_video_format(*f) for f in formats] + [audio],
    }
    params = {
        "quiet": True,
        "simulate": True,
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
        "format_sort": list(DOWNLOAD_FORMAT_SORT),
    }
    with YoutubeDL(params) as ydl:
        result = ydl.process_video_result(info, download=False)
    assert result["requested_formats"][0]["format_id"] == expected
