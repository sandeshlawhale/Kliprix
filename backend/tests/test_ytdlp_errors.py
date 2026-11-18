"""yt-dlp exceptions -> documented codes, using the real yt-dlp exception classes.

The reason texts are YouTube playability reasons as yt-dlp 2026.10.26 passes them on.
"""

import sys

import pytest
from yt_dlp.utils import (
    DownloadError,
    ExtractorError,
    GeoRestrictedError,
    PostProcessingError,
)

from app.core.errors import ErrorCode
from app.sources.ytdlp_errors import to_app_error


def _wrapped(cause: Exception) -> DownloadError:
    """What `YoutubeDL.extract_info` raises: a DownloadError carrying the original."""
    try:
        raise cause
    except (ExtractorError, PostProcessingError):
        return DownloadError(f"ERROR: {cause}", sys.exc_info())


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        (
            "[youtube] abc: Private video. Sign in if you've been granted access to this video",
            ErrorCode.VIDEO_PRIVATE,
        ),
        (
            "[youtube] abc: Sign in to confirm your age. This video may be inappropriate for "
            "some users.",
            ErrorCode.VIDEO_AGE_RESTRICTED,
        ),
        ("[youtube] abc: Video unavailable", ErrorCode.VIDEO_UNAVAILABLE),
        # Exact text seen for a nonexistent id in the M2 run (2026-11-18).
        ("[youtube] abc: This video is unavailable", ErrorCode.VIDEO_UNAVAILABLE),
        (
            "[youtube] abc: Video unavailable. This video has been removed by the uploader",
            ErrorCode.VIDEO_UNAVAILABLE,
        ),
        (
            "[youtube] abc: Join this channel to get access to members-only content",
            ErrorCode.VIDEO_UNAVAILABLE,
        ),
        ("[youtube] abc: This live event will begin in 3 hours.", ErrorCode.VIDEO_UNAVAILABLE),
        (
            "[youtube] abc: Sign in to confirm you're not a bot. Use --cookies",
            ErrorCode.DOWNLOAD_FAILED,
        ),
        (
            "[youtube] abc: This content isn't available, try again later. The current session "
            "has been rate-limited by YouTube",
            ErrorCode.DOWNLOAD_FAILED,
        ),
        (
            "[youtube] abc: Requested format is not available. Use --list-formats",
            ErrorCode.DOWNLOADER_OUTDATED,
        ),
    ],
)
def test_youtube_reasons_map_to_codes(reason: str, code: ErrorCode) -> None:
    error = to_app_error(_wrapped(ExtractorError(reason, expected=True)), video="abc")
    assert error.code == code
    assert "abc" not in error.message  # our own message, not yt-dlp's text


def test_geo_restriction_by_class() -> None:
    cause = GeoRestrictedError("The uploader has not made this video available in your country")
    assert to_app_error(_wrapped(cause), video="abc").code == ErrorCode.GEO_BLOCKED


def test_unexpected_extractor_error_means_outdated_downloader() -> None:
    cause = ExtractorError("Unable to extract initial player response", expected=False)
    assert to_app_error(_wrapped(cause), video="abc").code == ErrorCode.DOWNLOADER_OUTDATED


def test_merge_failure() -> None:
    cause = PostProcessingError("Conversion failed! C:\\Users\\x\\data\\source.mp4")
    error = to_app_error(_wrapped(cause), video="abc")
    assert error.code == ErrorCode.DOWNLOAD_FAILED
    assert "Users" not in error.message


def test_unknown_error_is_download_failed() -> None:
    error = to_app_error(DownloadError("ERROR: Connection reset by peer"), video="abc")
    assert error.code == ErrorCode.DOWNLOAD_FAILED
