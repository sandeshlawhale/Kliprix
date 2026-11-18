"""Stage 3, chat replay: `chat.json` = `{version: 1, items: [{t, text}]}` (docs/04, 08).

Only past live streams and premieres have a replay. Without one the stage returns
None and writes nothing; the caller records `has_chat=false` and continues (A-004).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.files import write_json_atomic
from app.sources.base import ChatMessage, SourceAdapter

logger = logging.getLogger(__name__)

CHAT_FILE_NAME = "chat.json"


class ChatFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    items: list[ChatMessage]


def load_cached(dest_dir: Path) -> ChatFile | None:
    path = dest_dir / CHAT_FILE_NAME
    if not path.is_file():
        return None
    try:
        return ChatFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def run_chat(
    adapter: SourceAdapter,
    url: str,
    dest_dir: Path,
    *,
    cancel: threading.Event | None = None,
) -> ChatFile | None:
    """Fetch the chat replay into `dest_dir/chat.json`; None when the video has none.

    Raises:
        AppError: DOWNLOAD_FAILED or the adapter's video errors.
        SourceCancelledError: `cancel` was set.
    """
    cached = load_cached(dest_dir)
    if cached is not None:
        return cached
    messages = adapter.get_chat(url, dest_dir, cancel=cancel)
    if messages is None:
        logger.info("chat absent url=%s", url)
        return None
    chat = ChatFile(items=messages)
    write_json_atomic(dest_dir / CHAT_FILE_NAME, chat)
    logger.info("chat saved url=%s messages=%s", url, len(messages))
    return chat
