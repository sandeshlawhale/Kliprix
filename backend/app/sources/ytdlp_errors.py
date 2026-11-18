"""Map yt-dlp exceptions to the documented error codes (docs/07, docs/04 "Download").

Checked against the installed yt-dlp (2026.10.26): `extract_info` wraps extractor
failures in `DownloadError` with the original exception in `exc_info`; YouTube's own
playability reason ("Private video", "Sign in to confirm your age", "Video unavailable")
is passed through as text, so the reason is matched case-insensitively. An
`ExtractorError` with `expected=False` is yt-dlp's "this is a bug, update yt-dlp" case.
Messages are our own fixed texts: yt-dlp text can contain file paths, so it is only logged.
"""

from __future__ import annotations

import logging

from yt_dlp.utils import (
    DownloadError,
    ExtractorError,
    GeoRestrictedError,
    PostProcessingError,
)

from app.core.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

# Order matters: the first matching rule wins (bot checks also say "sign in").
_TEXT_RULES: tuple[tuple[tuple[str, ...], ErrorCode, str], ...] = (
    (
        ("not a bot",),
        ErrorCode.DOWNLOAD_FAILED,
        "YouTube asked to confirm this is not a bot. Try again later.",
    ),
    (
        ("try again later", "rate-limited"),
        ErrorCode.DOWNLOAD_FAILED,
        "YouTube is limiting requests right now. Try again in a while.",
    ),
    (
        ("not made this video available in your country", "from your location"),
        ErrorCode.GEO_BLOCKED,
        "This video is not available in your country.",
    ),
    (("private video",), ErrorCode.VIDEO_PRIVATE, "This video is private."),
    (
        ("confirm your age", "age-restricted", "inappropriate for some users", "age_verification"),
        ErrorCode.VIDEO_AGE_RESTRICTED,
        "This video is age-restricted and cannot be downloaded without signing in.",
    ),
    (
        ("members-only", "join this channel"),
        ErrorCode.VIDEO_UNAVAILABLE,
        "This video is for channel members only.",
    ),
    (
        ("live event will begin", "premieres in", "premiere will begin"),
        ErrorCode.VIDEO_UNAVAILABLE,
        "This stream or premiere has not started yet.",
    ),
    (
        # Format selection found nothing: usually YouTube changed and yt-dlp lags behind.
        ("requested format is not available",),
        ErrorCode.DOWNLOADER_OUTDATED,
        "No downloadable format was found. Update the downloader in Settings and try again.",
    ),
    (
        (
            "video unavailable",
            "video is unavailable",
            "has been removed",
            "no longer available",
            "is not available",
            "does not exist",
            "account associated with this video has been terminated",
        ),
        ErrorCode.VIDEO_UNAVAILABLE,
        "This video is unavailable (removed, deleted, or blocked).",
    ),
    (
        ("ffmpeg not found", "ffprobe not found", "ffmpeg is not installed"),
        ErrorCode.FFMPEG_MISSING,
        "ffmpeg was not found. Install ffmpeg and run the system check again.",
    ),
)


def _root_cause(exc: Exception) -> BaseException:
    if isinstance(exc, DownloadError) and exc.exc_info and exc.exc_info[1] is not None:
        cause: BaseException = exc.exc_info[1]
        return cause
    return exc


def to_app_error(exc: Exception, *, video: str) -> AppError:
    """Turn a yt-dlp error into an `AppError` and log yt-dlp's own text.

    `video` is a short label (source id or URL) for the log line only.
    """
    cause = _root_cause(exc)
    text = f"{exc} {cause}".lower()
    logger.warning("yt-dlp failed video=%s error=%s", video, str(exc)[:1000])
    if isinstance(cause, GeoRestrictedError):
        return AppError(ErrorCode.GEO_BLOCKED, "This video is not available in your country.")
    for needles, code, message in _TEXT_RULES:
        if any(needle in text for needle in needles):
            return AppError(code, message)
    if isinstance(cause, PostProcessingError):
        return AppError(ErrorCode.DOWNLOAD_FAILED, "Merging the downloaded video and audio failed.")
    if isinstance(cause, ExtractorError) and not cause.expected:
        return AppError(
            ErrorCode.DOWNLOADER_OUTDATED,
            "The downloader could not read this YouTube page. Update the downloader in "
            "Settings and try again.",
        )
    return AppError(
        ErrorCode.DOWNLOAD_FAILED,
        "Could not get this video from YouTube. Check your connection and try again.",
    )
