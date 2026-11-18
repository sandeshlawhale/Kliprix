"""Our own ASS generator (docs/05 "Title", "Captions"): one file holds the title and the
captions. One `Dialogue` event per word-time slice with inline override tags (no `\\k`).
PlayRes is 1080x1920, so every number is a canvas pixel.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.pipeline.transcribe import Word, contains_devanagari
from app.render import layout_constants as lc
from app.render.fonts import text_width
from app.render.layout import Layout

CaptionStyle = Literal["pop", "karaoke", "boxed"]
_PUNCTUATION = (".", ",", "!", "?", ";", ":", "…")


def ass_color(hex_rgb: str, opacity: float = 1.0) -> str:
    """'#RRGGBB' to ASS '&HAABBGGRR' (alpha 00 = opaque)."""
    red, green, blue = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    alpha = round((1.0 - opacity) * 255)
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def inline_color(hex_rgb: str) -> str:
    """'#RRGGBB' to the inline tag form '&HBBGGRR&'."""
    return f"&H{hex_rgb[5:7]}{hex_rgb[3:5]}{hex_rgb[1:3]}&".upper()


def inline_alpha(opacity: float) -> str:
    return f"&H{round((1.0 - opacity) * 255):02X}&"


def ass_time(seconds: float) -> str:
    """Seconds to ASS 'H:MM:SS.cc'."""
    centis = max(0, round(seconds * 100))
    hours, rest = divmod(centis, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def clean(text: str) -> str:
    """Make user/LLM text safe inside an ASS event: no override braces, no backslash codes,
    no line breaks."""
    for bad, good in (("\\", "/"), ("{", "("), ("}", ")"), ("\n", " "), ("\r", " ")):
        text = text.replace(bad, good)
    return " ".join(text.split())


# ---------- chunking ----------


@dataclass(frozen=True)
class Chunk:
    words: list[Word]
    lines: list[list[int]]  # word indexes per line
    start: float
    end: float


def _wrap(texts: Sequence[str], max_chars: int, max_width: float) -> list[list[int]]:
    """Greedy line breaking by character count and pixel width."""
    lines: list[list[int]] = [[]]
    for index, text in enumerate(texts):
        current = lines[-1]
        candidate = " ".join([texts[i] for i in current] + [text])
        width = text_width(candidate, lc.CAPTION_FONT_FILE, lc.CAPTION_SIZE)
        if current and (len(candidate) > max_chars or width > max_width):
            lines.append([index])
        else:
            current.append(index)
    return lines


def _fits(texts: Sequence[str]) -> bool:
    lines = _wrap(texts, lc.CAPTION_MAX_CHARS_PER_LINE, lc.CAPTION_MAX_W)
    return len(lines) <= lc.CAPTION_MAX_LINES


def chunk_words(words: Sequence[Word], max_words: int) -> list[Chunk]:
    """Group words into on-screen chunks: at most `max_words`, broken at punctuation and at
    pauses over 0.35 s, and never more than 2 lines of 18 characters / 900 px."""
    groups: list[list[Word]] = []
    for word in words:
        if not word.text.strip():
            continue
        current = groups[-1] if groups else None
        if current is not None:
            previous = current[-1]
            breaks = (
                len(current) >= max_words
                or previous.text.endswith(_PUNCTUATION)
                or word.start - previous.end > lc.CAPTION_PAUSE_S
                or not _fits([clean(w.text) for w in [*current, word]])
            )
            if not breaks:
                current.append(word)
                continue
        groups.append([word])
    chunks: list[Chunk] = []
    for number, group in enumerate(groups):
        next_start = groups[number + 1][0].start if number + 1 < len(groups) else None
        end = group[-1].end + lc.CAPTION_LINGER_S
        if next_start is not None:
            end = min(end, next_start)
        end = max(end, group[-1].end)
        texts = [clean(w.text) for w in group]
        lines = _wrap(texts, lc.CAPTION_MAX_CHARS_PER_LINE, lc.CAPTION_MAX_W)
        chunks.append(Chunk(group, lines, group[0].start, end))
    return chunks


# ---------- styles ----------


def _word_text(word: Word) -> str:
    text = clean(word.text)
    if contains_devanagari(text):
        return f"{{\\fn{lc.DEVANAGARI_FONT}}}{text}{{\\fn{lc.CAPTION_FONT}}}"
    return text


def _render_words(chunk: Chunk, active: int, style: CaptionStyle) -> str:
    white = inline_color(lc.TEXT_COLOR)
    yellow = inline_color(lc.ACTIVE_COLOR)
    black = inline_color(lc.OUTLINE_COLOR)
    dim = inline_alpha(lc.UPCOMING_OPACITY)
    opaque = inline_alpha(1.0)
    parts: list[str] = []
    for line_number, line in enumerate(chunk.lines):
        line_parts: list[str] = []
        for index in line:
            text = _word_text(chunk.words[index])
            if style == "pop":
                color = yellow if index == active else white
                line_parts.append(f"{{\\c{color}}}{text}")
            elif style == "karaoke":
                if index == active:
                    line_parts.append(f"{{\\c{yellow}\\alpha{opaque}}}{text}")
                elif index < active:
                    line_parts.append(f"{{\\c{white}\\alpha{opaque}}}{text}")
                else:
                    line_parts.append(f"{{\\c{white}\\alpha{dim}}}{text}")
            elif index == active:
                box = inline_color(lc.BOX_COLOR)
                line_parts.append(
                    f"{{\\c{white}\\3c{box}\\bord{lc.BOX_BORDER}}}{text}"
                    f"{{\\3c{black}\\bord{lc.CAPTION_OUTLINE}}}"
                )
            else:
                line_parts.append(f"{{\\c{white}}}{text}")
        parts.append(" ".join(line_parts))
        if line_number < len(chunk.lines) - 1:
            parts.append("\\N")
    return "".join(parts)


def _pop_in() -> str:
    return (
        f"\\fscx{lc.POP_START_SCALE}\\fscy{lc.POP_START_SCALE}"
        f"\\t(0,{lc.POP_OVERSHOOT_MS},\\fscx{lc.POP_OVERSHOOT_SCALE}\\fscy{lc.POP_OVERSHOOT_SCALE})"
        f"\\t({lc.POP_OVERSHOOT_MS},{lc.POP_TOTAL_MS},\\fscx100\\fscy100)"
    )


def caption_events(
    words: Sequence[Word], style: CaptionStyle, layout: Layout, duration_s: float
) -> list[str]:
    """Dialogue lines for the captions: one per word-time slice."""
    max_words = lc.POP_MAX_WORDS if style == "pop" else lc.CAPTION_MAX_WORDS
    position = f"\\an5\\pos({lc.CANVAS_W // 2},{layout.caption_y})"
    events: list[str] = []
    for chunk in chunk_words(words, max_words):
        for index, word in enumerate(chunk.words):
            start = chunk.start if index == 0 else word.start
            end = chunk.words[index + 1].start if index + 1 < len(chunk.words) else chunk.end
            start, end = max(0.0, start), min(duration_s, end)
            if end - start < 0.01:
                continue
            tags = position + (_pop_in() if style == "pop" and index == 0 else "")
            text = "{" + tags + "}" + _render_words(chunk, index, style)
            events.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{text}")
    return events


# ---------- title ----------


def fit_title(title: str) -> tuple[int, list[str]]:
    """Font size and lines for the uppercase title: largest size from 88 down to 60 px
    that fits in 2 lines of 960 px."""
    words = clean(title).upper().split()
    size = lc.TITLE_SIZE
    while True:
        lines: list[str] = []
        for word in words:
            candidate = f"{lines[-1]} {word}" if lines else word
            if lines and text_width(candidate, lc.TITLE_FONT_FILE, size) <= lc.TITLE_MAX_W:
                lines[-1] = candidate
            else:
                lines.append(word)
        too_wide = any(
            text_width(line, lc.TITLE_FONT_FILE, size) > lc.TITLE_MAX_W for line in lines
        )
        if (len(lines) <= lc.TITLE_MAX_LINES and not too_wide) or size <= lc.TITLE_MIN_SIZE:
            return size, lines[: lc.TITLE_MAX_LINES]
        size -= lc.TITLE_SIZE_STEP


def title_event(title: str, layout: Layout, duration_s: float) -> str | None:
    if not clean(title):
        return None
    size, lines = fit_title(title)
    tags = f"\\an5\\pos({lc.CANVAS_W // 2},{layout.title_y})\\fs{size}\\fad({lc.TITLE_FADE_MS},0)"
    text = "\\N".join(lines)
    return f"Dialogue: 2,{ass_time(0)},{ass_time(duration_s)},Title,,0,0,0,,{{{tags}}}{text}"


# ---------- file ----------


def _style_line(name: str, font: str, size: int, outline: int, shadow: int) -> str:
    return (
        f"Style: {name},{font},{size},{ass_color(lc.TEXT_COLOR)},{ass_color(lc.ACTIVE_COLOR)},"
        f"{ass_color(lc.OUTLINE_COLOR)},{ass_color(lc.SHADOW_COLOR, lc.SHADOW_OPACITY)},"
        f"0,0,0,0,100,100,0,0,1,{outline},{shadow},5,0,0,0,1"
    )


def build_ass(
    words: Sequence[Word],
    style: CaptionStyle,
    title: str | None,
    layout: Layout,
    duration_s: float,
) -> str:
    """The whole ASS file for one clip. `words` are relative to the clip start."""
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {lc.CANVAS_W}",
        f"PlayResY: {lc.CANVAS_H}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_line("Title", lc.TITLE_FONT, lc.TITLE_SIZE, lc.TITLE_OUTLINE, lc.TITLE_SHADOW),
        _style_line("Caption", lc.CAPTION_FONT, lc.CAPTION_SIZE, lc.CAPTION_OUTLINE, 0),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events: list[str] = []
    if title:
        event = title_event(title, layout, duration_s)
        if event is not None:
            events.append(event)
    events.extend(caption_events(words, style, layout, duration_s))
    return "\n".join(header + events) + "\n"
