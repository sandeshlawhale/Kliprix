"""Bundled fonts (SIL OFL, docs/09): where they are, text width measurement, and copying
them next to a render so ffmpeg can use a relative `fontsdir` (Windows path escaping)."""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont

from app.render import layout_constants as lc

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_FILES = (lc.TITLE_FONT_FILE, lc.CAPTION_FONT_FILE, lc.DEVANAGARI_FONT_FILE)
WORK_FONTS_DIR_NAME = "fonts"
# Advance used for a character the font has no glyph for, as a fraction of the em size.
MISSING_GLYPH_EM = 0.6


@lru_cache(maxsize=len(FONT_FILES))
def _metrics(file_name: str) -> tuple[dict[int, str], dict[str, tuple[int, int]], int]:
    """Character -> glyph name, glyph name -> (advance, side bearing), units per em."""
    font = TTFont(FONTS_DIR / file_name, lazy=True)
    cmap: dict[int, str] = dict(font.getBestCmap() or {})
    hmtx: dict[str, tuple[int, int]] = dict(font["hmtx"].metrics)
    units = int(font["head"].unitsPerEm)
    font.close()
    return cmap, hmtx, units


def text_width(text: str, file_name: str, size: float) -> float:
    """Width in pixels of `text` set in the font at `size` px (advance widths, no kerning)."""
    cmap, hmtx, units = _metrics(file_name)
    total = 0.0
    for char in text:
        glyph = cmap.get(ord(char))
        if glyph is None:
            total += MISSING_GLYPH_EM * units
        else:
            total += hmtx[glyph][0]
    return total * size / units


def install_fonts(work_dir: Path) -> Path:
    """Copy the bundled fonts into `work_dir/fonts` (skipped when already there)."""
    target = work_dir / WORK_FONTS_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    for name in FONT_FILES:
        destination = target / name
        source = FONTS_DIR / name
        if not destination.is_file() or destination.stat().st_size != source.stat().st_size:
            shutil.copyfile(source, destination)
    return target
