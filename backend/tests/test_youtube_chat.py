"""Chat replay parsing on hand-written lines in yt-dlp's live_chat.json shape."""

import json
from typing import Any

from app.sources.youtube_chat import parse_live_chat_lines


def _line(offset_ms: str, *actions: dict[str, Any]) -> str:
    return json.dumps(
        {"replayChatItemAction": {"actions": list(actions), "videoOffsetTimeMsec": offset_ms}}
    )


def _text(*runs: dict[str, Any], renderer: str = "liveChatTextMessageRenderer") -> dict[str, Any]:
    return {"addChatItemAction": {"item": {renderer: {"message": {"runs": list(runs)}}}}}


def test_parses_text_emoji_and_paid_messages() -> None:
    lines = [
        _line("1500", _text({"text": "hello "}, {"text": "chat"})),
        _line(
            "2250",
            _text(
                {"text": "lol "},
                {"emoji": {"emojiId": "\U0001f602", "shortcuts": [":joy:"]}},
            ),
        ),
        _line("3000", _text({"text": "gg"}, renderer="liveChatPaidMessageRenderer")),
        _line(
            "4000",
            _text(
                {"emoji": {"emojiId": "UCx/abc", "shortcuts": [":hype:"], "isCustomEmoji": True}}
            ),
        ),
    ]
    messages = list(parse_live_chat_lines(lines))
    assert [(m.t, m.text) for m in messages] == [
        (1.5, "hello chat"),
        (2.25, "lol \U0001f602"),
        (3.0, "gg"),
        (4.0, ":hype:"),
    ]


def test_skips_non_messages_bad_lines_and_pre_start_chat() -> None:
    membership: dict[str, Any] = {
        "addChatItemAction": {"item": {"liveChatMembershipItemRenderer": {"headerSubtext": {}}}}
    }
    lines = [
        "",
        "{not json",
        json.dumps({"clickTrackingParams": "x"}),
        _line("-5000", _text({"text": "before start"})),
        _line("abc", _text({"text": "bad offset"})),
        _line("1000", membership, _text({"text": "   "})),
        _line("2000", {"addLiveChatTickerItemAction": {}}, _text({"text": "kept"})),
    ]
    messages = list(parse_live_chat_lines(lines))
    assert [(m.t, m.text) for m in messages] == [(2.0, "kept")]
