"""Layout math (docs/05): foreground box, title and caption positions. Pure functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.render import layout_constants as lc

CropMode = Literal["face", "full"]


@dataclass(frozen=True)
class Layout:
    fg_w: int
    fg_h: int
    fg_top: int
    title_y: int
    caption_y: int

    @property
    def fg_bottom(self) -> int:
        return self.fg_top + self.fg_h


def even(value: float) -> int:
    """Round to the nearest even integer (yuv420p needs even sizes)."""
    return max(2, round(value / 2) * 2)


def full_frame_height(src_w: int, src_h: int) -> int:
    """Foreground height for the whole frame scaled to 1080 wide (16:9 gives 608)."""
    return min(lc.CANVAS_H, even(lc.FULL_FG_W * src_h / src_w))


def compute_layout(mode: CropMode, src_w: int, src_h: int) -> Layout:
    """Foreground size/top and the title and caption centers for a crop mode."""
    if mode == "face":
        fg_w, fg_h, fg_top = lc.FACE_FG_W, lc.FACE_FG_H, lc.FACE_FG_TOP
    else:
        fg_w, fg_h = lc.FULL_FG_W, full_frame_height(src_w, src_h)
        fg_top = max(0, lc.FULL_FG_CENTER_Y - fg_h // 2)
    title_y = max(lc.TITLE_MIN_CENTER_Y, fg_top - lc.TITLE_GAP)
    caption_y = min(lc.CAPTION_MAX_CENTER_Y, fg_top + fg_h + lc.CAPTION_GAP)
    return Layout(fg_w, fg_h, fg_top, title_y, caption_y)


@dataclass(frozen=True)
class CropBox:
    """The face crop window inside the source frame (before scaling to 1080x1350)."""

    w: int
    h: int


def face_crop_box(src_w: int, src_h: int) -> CropBox:
    """A 4:5 window of the full source height; for narrow sources, the full width and a
    4:5 height (scaled up later)."""
    width = even(src_h * lc.FACE_CROP_ASPECT)
    if width <= src_w:
        return CropBox(width, src_h - src_h % 2)
    height = min(src_h, even(src_w / lc.FACE_CROP_ASPECT))
    return CropBox(src_w - src_w % 2, height)


def crop_left(center_x: float, src_w: int, box: CropBox) -> int:
    """Left edge for a crop centered at `center_x` (0-1 of the width), clamped to the frame."""
    left = round(center_x * src_w - box.w / 2)
    return max(0, min(src_w - box.w, left))


def blur_radius(blur: int) -> int:
    """User blur (0-60) to the box-blur radius on the downscaled background."""
    clamped = max(0, min(lc.BLUR_MAX, blur))
    return round(clamped / lc.BG_BLUR_DOWNSCALE)
