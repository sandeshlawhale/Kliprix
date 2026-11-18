import pytest

from app.config import AppConfig
from app.core.errors import AppError, ErrorCode
from app.sources.registry import build_adapters, resolve
from app.sources.urls import parse_allowed_url

VIDEO_ID = "abcDEF123_-"
CANONICAL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.mark.parametrize(
    "raw",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"http://youtube.com/watch?v={VIDEO_ID}&t=42s",
        f"https://m.youtube.com/watch?feature=share&v={VIDEO_ID}",
        f"https://music.youtube.com/watch?v={VIDEO_ID}&list=RDabc",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL123&index=4",
        f"https://youtu.be/{VIDEO_ID}?si=tracking",
        f"youtu.be/{VIDEO_ID}",
        f"  https://www.youtube.com/shorts/{VIDEO_ID}  ",
        f"https://www.youtube.com/live/{VIDEO_ID}?feature=shared",
        f"https://www.youtube.com/embed/{VIDEO_ID}",
        f"HTTPS://WWW.YOUTUBE.COM/watch?v={VIDEO_ID}",
    ],
)
def test_video_links_become_one_canonical_url(config: AppConfig, raw: str) -> None:
    _, url = resolve(raw, build_adapters(config))
    assert url == CANONICAL


@pytest.mark.parametrize(
    "raw",
    [
        "https://evil-youtube.com/watch?v=abcDEF123_-",
        "https://youtube.com.evil.example/watch?v=abcDEF123_-",
        "https://vimeo.com/123",
        "https://www.twitch.tv/videos/1",
        "https://youtube-nocookie.com/embed/abcDEF123_-",
    ],
)
def test_other_hosts_are_unsupported(raw: str) -> None:
    with pytest.raises(AppError) as info:
        parse_allowed_url(raw)
    assert info.value.code == ErrorCode.UNSUPPORTED_HOST


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not a url at all",
        "ftp://youtube.com/watch?v=abcDEF123_-",
        "file:///C:/Windows/win.ini",
        "javascript:alert(1)",
        "https://user:pass@youtube.com/watch?v=abcDEF123_-",
        "https://youtube.com:8080/watch?v=abcDEF123_-",
        "https://youtube.com:notaport/watch",
        "https://" + "a" * 3000,
    ],
)
def test_malformed_links_are_invalid(raw: str) -> None:
    with pytest.raises(AppError) as info:
        parse_allowed_url(raw)
    assert info.value.code == ErrorCode.INVALID_URL


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.youtube.com/",
        "https://www.youtube.com/@somechannel",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/watch?v=short",
        "https://youtu.be/",
        "https://www.youtube.com/watch?v=abcDEF123_-extra",
    ],
)
def test_youtube_links_without_a_single_video_are_invalid(config: AppConfig, raw: str) -> None:
    with pytest.raises(AppError) as info:
        resolve(raw, build_adapters(config))
    assert info.value.code == ErrorCode.INVALID_URL


def test_registry_rejects_allowed_host_without_adapter(config: AppConfig) -> None:
    with pytest.raises(AppError) as info:
        resolve("https://youtu.be/abcDEF123_-", [])
    assert info.value.code == ErrorCode.UNSUPPORTED_HOST
