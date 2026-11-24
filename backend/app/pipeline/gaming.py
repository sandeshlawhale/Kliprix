"""Is a video a gaming video? (D-052) Decides whether Auto uses the gaming layouts.

Two signals the pipeline already has: the game strategy found from the title and channel
(D-050: Valorant, GTA, Minecraft) and the screen type of the whole video (D-040). The screen
type alone is not enough: a bike review scored 0.70 game-like and a vlog 0.69, while the
owner's game streams score 0.90-0.99, so an unknown game needs GAMING_SCREEN_MIN_SHARE.
"""

from __future__ import annotations

from pathlib import Path

from app.pipeline.context.strategies import GENERIC, detect_strategy
from app.pipeline.screen import ScreenFile, load_screen, window_shares

# Share of game screens (gameplay + game menus) over the whole video for an unknown game.
GAMING_SCREEN_MIN_SHARE = 0.85
GAME_GROUPS = ("gameplay", "menu")


def screen_says_gaming(screen: ScreenFile | None) -> bool:
    if screen is None or not screen.shares:
        return False
    shares = window_shares(screen, 0.0, len(screen.shares) * screen.step_s)
    return sum(shares.get(group, 0.0) for group in GAME_GROUPS) >= GAMING_SCREEN_MIN_SHARE


def is_gaming_video(title: str | None, channel: str | None, screen: ScreenFile | None) -> bool:
    if detect_strategy(title or "", channel).name != GENERIC.name:
        return True
    return screen_says_gaming(screen)


def video_is_gaming(
    video_dir: Path, title: str | None, channel: str | None, duration_s: float | None
) -> bool:
    """`is_gaming_video` with the video's saved screen file (when it has one)."""
    screen = load_screen(video_dir, duration_s) if duration_s else None
    return is_gaming_video(title, channel, screen)
