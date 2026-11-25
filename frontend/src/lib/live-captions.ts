// The editor's live preview (D-057): the title and caption layout of the backend renderer
// (backend/app/render/ass.py and layout_constants.py) redone in the browser, so edits show
// over the base video at once. Numbers are pixels on the 1080x1920 canvas. Pure functions;
// text width is measured by the caller (a canvas in the browser, an estimate in tests).
import type { Word } from "@/lib/api";

export const CANVAS_W = 1080;
export const CANVAS_H = 1920;
/** libass sizes a font by its whole height (OS/2 winAscent + winDescent), not by its em,
 * and spaces lines by that size. These are unitsPerEm / (winAscent + winDescent) of the
 * bundled fonts: CSS font-size = ASS size x this, line height = the ASS size. */
export const EM_PER_ASS_SIZE = { title: 0.5769, caption: 0.6402 } as const;

export const CAPTION = {
  size: 72,
  outline: 5,
  maxW: 900,
  maxChars: 18,
  maxLines: 2,
  maxWords: 4,
  popMaxWords: 3,
  pauseS: 0.35,
  lingerS: 0.3,
} as const;

export const TITLE = {
  size: 88,
  minSize: 60,
  step: 2,
  maxW: 960,
  maxLines: 2,
  outline: 6,
  shadow: 3,
  fadeS: 0.25,
} as const;

export const COLORS = {
  text: "#FFFFFF",
  outline: "#000000",
  active: "#FFD60A",
  box: "#2563EB",
  boxBorder: 14,
  upcomingOpacity: 0.7,
  shadowOpacity: 0.6,
} as const;

export type CaptionStyle = "pop" | "karaoke" | "boxed";
export type FontKind = "caption" | "title";
/** Width in canvas pixels of `text` in the given font at `size`. */
export type Measure = (text: string, font: FontKind, size: number) => number;

/** A rough width when no font metrics are at hand (tests, fonts still loading). */
export const estimateWidth: Measure = (text, font, size) =>
  text.length * size * (font === "title" ? 0.45 : 0.62);

const PUNCTUATION = [".", ",", "!", "?", ";", ":", "…"];

/** Same clean-up as the renderer: no braces, backslashes or line breaks, single spaces. */
export function cleanText(text: string): string {
  return text
    .replaceAll("\\", "/")
    .replaceAll("{", "(")
    .replaceAll("}", ")")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

export interface Chunk {
  words: Word[];
  /** Word indexes (into `words`) per line. */
  lines: number[][];
  start: number;
  end: number;
}

function wrap(texts: string[], measure: Measure): number[][] {
  const lines: number[][] = [[]];
  texts.forEach((text, index) => {
    const current = lines[lines.length - 1]!;
    const candidate = [...current.map((i) => texts[i]), text].join(" ");
    const width = measure(candidate, "caption", CAPTION.size);
    if (current.length > 0 && (candidate.length > CAPTION.maxChars || width > CAPTION.maxW)) {
      lines.push([index]);
    } else {
      current.push(index);
    }
  });
  return lines;
}

/** On-screen groups, as the renderer makes them: at most `maxWords`, broken at punctuation,
 * at pauses over 0.35 s, and never more than 2 lines of 18 characters / 900 px. */
export function chunkWords(words: Word[], maxWords: number, measure: Measure): Chunk[] {
  const groups: Word[][] = [];
  for (const word of words) {
    if (!word.text.trim()) continue;
    const current = groups[groups.length - 1];
    if (current) {
      const previous = current[current.length - 1]!;
      const next = [...current, word].map((w) => cleanText(w.text));
      const breaks =
        current.length >= maxWords ||
        PUNCTUATION.some((mark) => previous.text.endsWith(mark)) ||
        word.start - previous.end > CAPTION.pauseS ||
        wrap(next, measure).length > CAPTION.maxLines;
      if (!breaks) {
        current.push(word);
        continue;
      }
    }
    groups.push([word]);
  }
  return groups.map((group, number) => {
    const nextStart = groups[number + 1]?.[0]?.start;
    const last = group[group.length - 1]!;
    let end = last.end + CAPTION.lingerS;
    if (nextStart !== undefined) end = Math.min(end, nextStart);
    end = Math.max(end, last.end);
    const lines = wrap(
      group.map((w) => cleanText(w.text)),
      measure,
    );
    return { words: group, lines, start: group[0]!.start, end };
  });
}

export interface CaptionFrame {
  /** Index of the chunk on screen (changes when a new group pops in). */
  chunk: number;
  lines: { text: string; index: number }[][];
  /** Index (inside the chunk) of the word being spoken. */
  active: number;
}

/** What the captions show at `time` (clip seconds), or null between groups. */
export function captionAt(chunks: Chunk[], time: number, duration: number): CaptionFrame | null {
  for (let c = 0; c < chunks.length; c++) {
    const chunk = chunks[c]!;
    if (time < chunk.start || time >= chunk.end) continue;
    for (let i = 0; i < chunk.words.length; i++) {
      const start = Math.max(0, i === 0 ? chunk.start : chunk.words[i]!.start);
      const end = Math.min(duration, chunk.words[i + 1]?.start ?? chunk.end);
      if (end - start < 0.01 || time < start || time >= end) continue;
      return {
        chunk: c,
        active: i,
        lines: chunk.lines.map((line) =>
          line.map((index) => ({ text: cleanText(chunk.words[index]!.text), index })),
        ),
      };
    }
  }
  return null;
}

/** Font size and lines of the uppercase title: the largest size from 88 down to 60 px that
 * fits in 2 lines of 960 px (as the renderer does). */
export function fitTitle(title: string, measure: Measure): { size: number; lines: string[] } {
  const words = cleanText(title).toUpperCase().split(" ").filter(Boolean);
  let size: number = TITLE.size;
  for (;;) {
    const lines: string[] = [];
    for (const word of words) {
      const last = lines[lines.length - 1];
      const candidate = last ? `${last} ${word}` : word;
      if (last && measure(candidate, "title", size) <= TITLE.maxW) {
        lines[lines.length - 1] = candidate;
      } else {
        lines.push(word);
      }
    }
    const tooWide = lines.some((line) => measure(line, "title", size) > TITLE.maxW);
    if ((lines.length <= TITLE.maxLines && !tooWide) || size <= TITLE.minSize) {
      return { size, lines: lines.slice(0, TITLE.maxLines) };
    }
    size -= TITLE.step;
  }
}
