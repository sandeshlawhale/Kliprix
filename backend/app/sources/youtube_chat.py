"""Parse the YouTube chat replay file that yt-dlp writes (`<name>.live_chat.json`).

Format (yt-dlp `downloader/youtube_live_chat.py`): one JSON object per line. Replay
lines are `{"replayChatItemAction": {"actions": [...], "videoOffsetTimeMsec": "123"}}`;
the inner actions are YouTube's `addChatItemAction` objects whose renderer carries
`message.runs` (text runs and emoji runs). Lines are read one at a time so a long
stream's chat never sits in memory as raw JSON.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from typing import Any

from app.sources.base import ChatMessage

logger = logging.getLogger(__name__)

MS_PER_S = 1000.0
# Renderers that carry a viewer's typed message (normal and Super Chat).
_MESSAGE_RENDERERS = ("liveChatTextMessageRenderer", "liveChatPaidMessageRenderer")


def _run_text(run: dict[str, Any]) -> str:
    text = run.get("text")
    if isinstance(text, str):
        return text
    emoji = run.get("emoji")
    if not isinstance(emoji, dict):
        return ""
    # Standard emoji use the character itself as the id; channel emoji use a shortcut.
    emoji_id = emoji.get("emojiId")
    if not emoji.get("isCustomEmoji") and isinstance(emoji_id, str):
        return emoji_id
    shortcuts = emoji.get("shortcuts")
    if isinstance(shortcuts, list) and shortcuts and isinstance(shortcuts[0], str):
        return shortcuts[0]
    return ""


def _message_text(action: dict[str, Any]) -> str | None:
    item = action.get("addChatItemAction", {}).get("item", {})
    if not isinstance(item, dict):
        return None
    for name in _MESSAGE_RENDERERS:
        renderer = item.get(name)
        if isinstance(renderer, dict):
            runs = renderer.get("message", {}).get("runs", [])
            text = "".join(_run_text(run) for run in runs if isinstance(run, dict)).strip()
            return text or None
    return None


def parse_live_chat_lines(lines: Iterable[str]) -> Iterator[ChatMessage]:
    """Yield chat messages (seconds from video start, text) from replay JSON lines.

    Skips non-message actions (membership banners, stickers, polls), messages sent
    before the video starts (negative offset), and lines that are not valid JSON.
    """
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("chat replay line %s is not valid JSON; skipped", number)
            continue
        replay = entry.get("replayChatItemAction") if isinstance(entry, dict) else None
        if not isinstance(replay, dict):
            continue
        try:
            offset_s = int(replay.get("videoOffsetTimeMsec", "")) / MS_PER_S
        except ValueError:
            continue
        if offset_s < 0:
            continue
        for action in replay.get("actions", []):
            if not isinstance(action, dict):
                continue
            text = _message_text(action)
            if text is not None:
                yield ChatMessage(t=offset_s, text=text)
