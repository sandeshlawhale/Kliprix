"""YouTube adapter on the yt-dlp Python API (docs/04 stages 1-3, docs/09 "Download").

yt-dlp options used here were checked against the installed version's
`YoutubeDL.py` docstring (2026.10.26). YouTube's player challenges need a JavaScript
runtime plus the `yt-dlp-ejs` scripts; we enable Node (already required for the
frontend) and never allow yt-dlp to fetch remote components.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, parse_qs

from yt_dlp import YoutubeDL
from yt_dlp.utils import YoutubeDLError

from app.config import (
    BYTES_PER_GB,
    DOWNLOAD_FORMAT_SORT,
    DOWNLOAD_RESERVE_GB,
    DOWNLOAD_SIZE_FACTOR,
    DOWNLOAD_UNKNOWN_SIZE_MIN_FREE_GB,
    YTDLP_JS_RUNTIME,
    YTDLP_SOCKET_TIMEOUT_S,
    AppConfig,
)
from app.core.errors import AppError, ErrorCode
from app.core.hardware import find_ffmpeg
from app.sources.base import (
    SOURCE_FILE_NAME,
    ChatMessage,
    ProgressCallback,
    SourceCancelledError,
    VideoMeta,
)
from app.sources.youtube_chat import parse_live_chat_lines
from app.sources.ytdlp_errors import to_app_error

logger = logging.getLogger(__name__)

PLATFORM = "youtube"
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
# youtube.com/<prefix>/<id> forms that point at one video.
_ID_PATH_PREFIXES = ("shorts", "live", "embed", "v")
_CHAT_SUBTITLE_KEY = "live_chat"
_CHAT_FILE_GLOB = "*.live_chat.json"
_CACHE_SUBDIR = Path("work") / "yt-dlp-cache"
# A part still downloading is shown as at most this far done (it is complete only on "finished").
PART_UNFINISHED_MAX = 0.99


class _YtdlpLog:
    """Routes yt-dlp's console output into our log file instead of stdout."""

    def debug(self, message: str) -> None:
        logger.debug("yt-dlp: %s", message)

    def info(self, message: str) -> None:
        logger.debug("yt-dlp: %s", message)

    def warning(self, message: str) -> None:
        logger.warning("yt-dlp: %s", message)

    def error(self, message: str) -> None:
        # Errors are raised as exceptions and mapped to codes; keep the text for debugging.
        logger.info("yt-dlp: %s", message)


def video_id_from_url(parts: SplitResult) -> str | None:
    """The 11-character video id in a YouTube link, or None for channels/playlists."""
    host = (parts.hostname or "").lower()
    segments = [segment for segment in parts.path.split("/") if segment]
    candidate: str | None = None
    if host == "youtu.be" or host.endswith(".youtu.be"):
        candidate = segments[0] if segments else None
    elif segments[:1] == ["watch"]:
        candidate = parse_qs(parts.query).get("v", [None])[0]
    elif len(segments) >= 2 and segments[0] in _ID_PATH_PREFIXES:
        candidate = segments[1]
    if candidate is not None and _VIDEO_ID_RE.match(candidate):
        return candidate
    return None


class YoutubeAdapter:
    """`SourceAdapter` for youtube.com and youtu.be."""

    platform = PLATFORM
    hosts: tuple[str, ...] = ("youtube.com", "youtu.be")

    def __init__(self, config: AppConfig) -> None:
        self._ffmpeg_path = config.ffmpeg_path
        self._cache_dir = config.data_dir / _CACHE_SUBDIR

    def canonical_url(self, parts: SplitResult) -> str:
        """`https://www.youtube.com/watch?v=<id>`; playlist and tracking parameters dropped.

        Raises:
            AppError INVALID_URL: the link does not point at a single video.
        """
        video_id = video_id_from_url(parts)
        if video_id is None:
            raise AppError(ErrorCode.INVALID_URL, "Paste a link to a single YouTube video.")
        return f"https://www.youtube.com/watch?v={video_id}"

    def inspect(self, url: str) -> VideoMeta:
        """Metadata without downloading.

        Raises:
            AppError: VIDEO_* / GEO_BLOCKED / DOWNLOAD_FAILED / DOWNLOADER_OUTDATED.
        """
        with YoutubeDL(self._params(skip_download=True)) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except YoutubeDLError as exc:
                raise to_app_error(exc, video=url) from exc
        return self._meta(info)

    def download(
        self,
        url: str,
        dest_dir: Path,
        *,
        max_height: int,
        cancel: threading.Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[Path, VideoMeta]:
        """Download best video up to `max_height` + best audio, merged into `source.mp4`.

        Raises:
            AppError: FFMPEG_MISSING, DISK_FULL, VIDEO_*, GEO_BLOCKED, DOWNLOAD_FAILED,
                DOWNLOADER_OUTDATED.
            SourceCancelledError: `cancel` was set.
        """
        ffmpeg = find_ffmpeg(self._ffmpeg_path)
        if ffmpeg is None:
            raise AppError(
                ErrorCode.FFMPEG_MISSING,
                "ffmpeg was not found. Install ffmpeg and run the system check again.",
            )
        dest_dir.mkdir(parents=True, exist_ok=True)
        tracker = _ProgressTracker(cancel, on_progress)
        params = self._params(
            format=f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]",
            format_sort=list(DOWNLOAD_FORMAT_SORT),
            paths={"home": str(dest_dir)},
            outtmpl={"default": "source.%(ext)s"},
            merge_output_format="mp4",
            # A single progressive format is not merged, so remux it to MP4 as well.
            postprocessors=[{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
            ffmpeg_location=ffmpeg,
            progress_hooks=[tracker.hook],
        )
        with YoutubeDL(params) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                meta = self._meta(info)
                _check_disk(dest_dir, info)
                tracker.parts = len(info.get("requested_formats") or [info])
                ydl.process_ie_result(info, download=True)
            except YoutubeDLError as exc:
                if cancel is not None and cancel.is_set():
                    raise SourceCancelledError("download cancelled") from exc
                raise to_app_error(exc, video=url) from exc
        source = dest_dir / SOURCE_FILE_NAME
        if not source.is_file():
            raise AppError(ErrorCode.DOWNLOAD_FAILED, "The download finished without a video file.")
        return source, meta

    def get_chat(
        self, url: str, work_dir: Path, *, cancel: threading.Event | None = None
    ) -> list[ChatMessage] | None:
        """Chat replay messages sorted by time, or None when the video has no replay.

        Raises:
            AppError: DOWNLOAD_FAILED when a replay exists but could not be fetched,
                plus the same video errors as `inspect`.
            SourceCancelledError: `cancel` was set.
        """
        work_dir.mkdir(parents=True, exist_ok=True)
        tracker = _ProgressTracker(cancel, None)
        with tempfile.TemporaryDirectory(dir=work_dir, prefix="chat-") as tmp:
            params = self._params(
                skip_download=True,
                writesubtitles=True,
                subtitleslangs=[_CHAT_SUBTITLE_KEY],
                paths={"home": tmp},
                outtmpl={"default": "chat"},
                progress_hooks=[tracker.hook],
            )
            with YoutubeDL(params) as ydl:
                try:
                    info = ydl.extract_info(url, download=True)
                except YoutubeDLError as exc:
                    if cancel is not None and cancel.is_set():
                        raise SourceCancelledError("chat download cancelled") from exc
                    raise to_app_error(exc, video=url) from exc
            if _CHAT_SUBTITLE_KEY not in (info.get("subtitles") or {}):
                return None
            files = sorted(Path(tmp).glob(_CHAT_FILE_GLOB))
            if not files:
                raise AppError(ErrorCode.DOWNLOAD_FAILED, "Downloading the chat replay failed.")
            with files[0].open(encoding="utf-8") as handle:
                messages = list(parse_live_chat_lines(handle))
        messages.sort(key=lambda message: message.t)
        return messages

    def _params(self, **extra: Any) -> dict[str, Any]:
        params: dict[str, Any] = {
            "logger": _YtdlpLog(),
            "quiet": True,
            "noprogress": True,
            "noplaylist": True,
            "socket_timeout": YTDLP_SOCKET_TIMEOUT_S,
            "js_runtimes": {YTDLP_JS_RUNTIME: {}},
            "cachedir": str(self._cache_dir),
        }
        params.update(extra)
        return params

    def _meta(self, info: dict[str, Any]) -> VideoMeta:
        live_status = info.get("live_status")
        if live_status == "is_live":
            raise AppError(ErrorCode.VIDEO_UNAVAILABLE, "Live streams are not supported yet.")
        if live_status == "is_upcoming":
            raise AppError(
                ErrorCode.VIDEO_UNAVAILABLE, "This stream or premiere has not started yet."
            )
        if live_status == "post_live":
            raise AppError(
                ErrorCode.VIDEO_UNAVAILABLE,
                "This stream just ended and YouTube is still processing it. Try again later.",
            )
        duration = info.get("duration")
        if not isinstance(duration, int | float) or duration <= 0:
            raise AppError(
                ErrorCode.VIDEO_UNAVAILABLE, "YouTube did not report a length for this video."
            )
        source_id = str(info.get("id") or "")
        return VideoMeta(
            platform=PLATFORM,
            source_id=source_id,
            title=str(info.get("title") or source_id),
            channel=_str_or_none(info.get("channel") or info.get("uploader")),
            duration_s=float(duration),
            thumbnail_url=_str_or_none(info.get("thumbnail")),
            has_chat=_CHAT_SUBTITLE_KEY in (info.get("subtitles") or {}),
            is_live=False,
        )


def _part_fraction(status: dict[str, Any]) -> float | None:
    """How much of the current part is done, from the most reliable field available.

    Fragmented (HLS/DASH) downloads report `total_bytes_estimate` that swings wildly
    (measured on a real video: 712 B to 72 MB within one part), so fragment counts win,
    then exact `total_bytes`, then the estimate as a last resort.
    """
    index, count = status.get("fragment_index"), status.get("fragment_count")
    if isinstance(index, int) and isinstance(count, int) and count > 0:
        return index / count
    done = status.get("downloaded_bytes") or 0
    total = status.get("total_bytes") or status.get("total_bytes_estimate")
    return done / total if total else None


class _ProgressTracker:
    """Turns yt-dlp progress hooks into one 0-1 fraction across the video and audio parts.

    Each part counts equally and the reported fraction never goes down.
    """

    def __init__(self, cancel: threading.Event | None, on_progress: ProgressCallback | None):
        self.parts = 1
        self._cancel = cancel
        self._on_progress = on_progress
        self._finished: set[str] = set()
        self._reported = 0.0

    def hook(self, status: dict[str, Any]) -> None:
        if self._cancel is not None and self._cancel.is_set():
            # yt-dlp stops the download when a hook raises.
            raise SourceCancelledError("cancelled")
        if self._on_progress is None:
            return
        filename = str(status.get("filename") or "")
        if status.get("status") == "finished":
            self._finished.add(filename)
            current = 0.0
        elif status.get("status") == "downloading" and filename not in self._finished:
            part = _part_fraction(status)
            if part is None:
                return
            # Never let one part look complete before yt-dlp says "finished".
            current = min(part, PART_UNFINISHED_MAX)
        else:
            return
        fraction = min((len(self._finished) + current) / max(self.parts, 1), 1.0)
        if fraction > self._reported:
            self._reported = fraction
            self._on_progress(fraction)


def _str_or_none(value: Any) -> str | None:
    return str(value) if value else None


def _estimate_bytes(info: dict[str, Any]) -> int | None:
    formats = info.get("requested_formats") or [info]
    sizes = [fmt.get("filesize") or fmt.get("filesize_approx") for fmt in formats]
    if any(not isinstance(size, int | float) for size in sizes):
        return None
    return int(sum(sizes))


def _check_disk(dest_dir: Path, info: dict[str, Any]) -> None:
    """Raise DISK_FULL when the download would not fit (AA-019)."""
    free_gb = shutil.disk_usage(dest_dir).free / BYTES_PER_GB
    estimate = _estimate_bytes(info)
    if estimate is None:
        need_gb = DOWNLOAD_UNKNOWN_SIZE_MIN_FREE_GB
    else:
        need_gb = estimate * DOWNLOAD_SIZE_FACTOR / BYTES_PER_GB + DOWNLOAD_RESERVE_GB
    if free_gb < need_gb:
        raise AppError(
            ErrorCode.DISK_FULL,
            f"Not enough free disk space for this video: needs about {need_gb:.1f} GB, "
            f"{free_gb:.1f} GB free.",
            {"needed_gb": round(need_gb, 1), "free_gb": round(free_gb, 1)},
        )
