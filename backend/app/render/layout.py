"""Layout math (docs/05): foreground box, title and caption positions. Pure functions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.render import layout_constants as lc

# D-052 adds the gaming layouts: "facecam" (cam on top, game below) and "center" (3:4).
CropMode = Literal["face", "full", "facecam", "center"]


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
    if mode == "facecam":
        # The whole canvas is video; the title sits on the seam between cam and game.
        return Layout(lc.CANVAS_W, lc.CANVAS_H, 0, lc.FACECAM_TOP_H, lc.CAPTION_MAX_CENTER_Y)
    if mode == "face":
        fg_w, fg_h, fg_top = lc.FACE_FG_W, lc.FACE_FG_H, lc.FACE_FG_TOP
    elif mode == "center":
        fg_w, fg_h, fg_top = lc.CENTER_FG_W, lc.CENTER_FG_H, lc.CENTER_FG_TOP
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


@dataclass(frozen=True)
class Rect:
    """A rectangle in source pixels."""

    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


@dataclass(frozen=True)
class Panel:
    """A source rectangle scaled to `dest_w`x`dest_h` and placed centered at `dest_y`."""

    src: Rect
    dest_w: int
    dest_h: int
    dest_y: int


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def aspect_crop(box: Rect, aspect: float, focus_x: float, focus_y: float) -> Rect:
    """The largest `aspect` (w/h) window inside `box`, centered on the focus point (source
    pixels) as far as the box allows."""
    if box.w / box.h > aspect:
        w, h = min(box.w, even(box.h * aspect)), box.h - box.h % 2
    else:
        w, h = box.w - box.w % 2, min(box.h, even(box.w / aspect))
    x = _clamp(round(focus_x - w / 2), box.x, box.right - w)
    y = _clamp(round(focus_y - h / 2), box.y, box.bottom - h)
    return Rect(x, y, w, h)


def game_window(src_w: int, src_h: int, aspect: float, avoid: Rect | None = None) -> Rect:
    """A full-height `aspect` window of the game, centered; moved sideways off `avoid` (the
    facecam) when it overlaps and the shorter move fits in the frame."""
    w = min(src_w - src_w % 2, even(src_h * aspect))
    x = (src_w - w) // 2
    if avoid is not None and x < avoid.right and avoid.x < x + w:
        options = [left for left in (avoid.x - w, avoid.right) if 0 <= left <= src_w - w]
        if options:
            x = min(options, key=lambda left: abs(left - x))
    return Rect(x, 0, w, src_h - src_h % 2)


def center_panels(src_w: int, src_h: int) -> tuple[Panel, ...]:
    """The center layout: the middle 3:4 of the game."""
    window = game_window(src_w, src_h, lc.CENTER_FG_W / lc.CENTER_FG_H)
    return (Panel(window, lc.CENTER_FG_W, lc.CENTER_FG_H, lc.CENTER_FG_TOP),)


def facecam_panels(
    src_w: int, src_h: int, cam: Rect, face_x: float, face_y: float
) -> tuple[Panel, ...]:
    """The facecam split: the cam (trimmed by FACECAM_INSET, cropped to the top band's shape
    around the face) on top, the game below."""
    dx, dy = round(cam.w * lc.FACECAM_INSET), round(cam.h * lc.FACECAM_INSET)
    inner = Rect(cam.x + dx, cam.y + dy, cam.w - 2 * dx, cam.h - 2 * dy)
    top = aspect_crop(inner, lc.CANVAS_W / lc.FACECAM_TOP_H, face_x, face_y)
    game = game_window(src_w, src_h, lc.CANVAS_W / lc.FACECAM_GAME_H, avoid=cam)
    return (
        Panel(top, lc.CANVAS_W, lc.FACECAM_TOP_H, 0),
        Panel(game, lc.CANVAS_W, lc.FACECAM_GAME_H, lc.FACECAM_TOP_H),
    )
