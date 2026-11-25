// Clip editor logic without React: the editable draft, its validation, and the PATCH body
// with only what changed (docs/03 "Clip detail", docs/07 PATCH /api/clips/{id}).
import type { BlurBox, Clip, ClipPatch, Word } from "@/lib/api";

export const TITLE_MAX = 40;
export const EDIT_MIN_S = 5;
export const EDIT_MAX_S = 90;
export const GOOD_MIN_S = 15;
/** Blur boxes (D-048): at most this many, and no side under this share of the video. */
export const MAX_BLUR_BOXES = 20;
export const MIN_BOX_SIDE = 0.02;
const NEW_BOX_S = 3;
/** Shortest blur box the timeline lets you drag to (D-056). */
export const MIN_BOX_S = 0.2;
/** Shortest part of the clip after a split or a drag (D-059, same as the backend). */
export const MIN_PART_S = 0.5;
export const MAX_PARTS = 20;

export interface EditDraft {
  /** On-video title (D-056: edited in the editor, saved with Apply). */
  title: string;
  /** The source ranges the clip plays, in order (D-059): one for a plain trim, several
   * after a cut or for a montage. Split pieces that touch merge again when saved. */
  parts: Part[];
  caption_style: Clip["edit"]["caption_style"];
  crop_mode: Clip["edit"]["crop_mode"];
  blur: number;
  /** Caption words in SOURCE seconds (D-062), so a cut, delete or extend moves them with the
   * video at once; `shownWords` places them on the clip's timeline. */
  words: Word[];
  blur_boxes: BlurBox[];
}

export type Part = [number, number];

export function draftFrom(clip: Clip): EditDraft {
  return {
    title: clip.metadata.title,
    parts: savedParts(clip),
    caption_style: clip.edit.caption_style,
    crop_mode: clip.edit.crop_mode,
    blur: clip.edit.blur,
    words: toSourceWords(clip.words, savedParts(clip)),
    blur_boxes: clip.edit.blur_boxes.map((box) => ({ ...box })),
  };
}

/** The source ranges a trim plays with planned parts (mirrors the backend's `effective`):
 * the parts inside [start, end], the first and last moved to those edges, or the single
 * range when fewer than two parts remain. */
export function effective(start: number, end: number, planned: Part[]): Part[] {
  const kept: Part[] = normalized(
    planned
      .filter(([a, b]) => b > start && a < end)
      .map(([a, b]) => [Math.max(a, start), Math.min(b, end)]),
  );
  if (kept.length < 2) return [[start, end]];
  kept[0] = [start, kept[0]![1]];
  kept[kept.length - 1] = [kept[kept.length - 1]![0], end];
  return kept;
}

/** Sorted parts, merged where they touch (within 0.05 s), without empty ones. */
export function normalized(parts: Part[]): Part[] {
  const merged: Part[] = [];
  for (const [a, b] of [...parts].sort((x, y) => x[0] - y[0])) {
    if (b - a < 0.05) continue;
    const last = merged[merged.length - 1];
    if (last && a <= last[1] + 0.05) last[1] = Math.max(last[1], b);
    else merged.push([a, b]);
  }
  return merged;
}

/** What the saved clip plays. */
export function savedParts(clip: Clip): Part[] {
  const { start_s, end_s, montage, parts } = clip.edit;
  return montage ? effective(start_s, end_s, parts as Part[]) : [[start_s, end_s]];
}

export const draftStart = (draft: EditDraft) => draft.parts[0]?.[0] ?? 0;
export const draftEnd = (draft: EditDraft) => draft.parts[draft.parts.length - 1]?.[1] ?? 0;

/** The source ranges the draft plays. */
export function playedParts(draft: EditDraft): Part[] {
  return draft.parts;
}

/** Seconds the draft plays. */
export function playedLength(draft: EditDraft): number {
  return draft.parts.reduce((sum, [start, end]) => sum + end - start, 0);
}

/** The draft trimmed to [start, end] (source seconds): parts outside are dropped, the first
 * and last are cut or extended to the new edges. */
export function withTrim(draft: EditDraft, start: number, end: number): EditDraft {
  if (!(end > start)) return draft;
  const kept: Part[] = draft.parts
    .filter(([a, b]) => b > start && a < end)
    .map(([a, b]) => [Math.max(a, start), Math.min(b, end)]);
  if (kept.length === 0) return { ...draft, parts: [[start, end]] };
  kept[0] = [start, kept[0]![1]];
  kept[kept.length - 1] = [kept[kept.length - 1]![0], end];
  return { ...draft, parts: kept };
}

/** Montage switch (D-051): the planned parts inside the trim, or the whole range. */
export function withMontage(draft: EditDraft, on: boolean, planned: Part[]): EditDraft {
  const start = draftStart(draft);
  const end = draftEnd(draft);
  return { ...draft, parts: on ? effective(start, end, planned) : [[start, end]] };
}

/** The draft with the part under clip time `at` split in two there (D-059), and the index
 * of the second piece; null when a piece would be shorter than MIN_PART_S. */
export function splitAt(draft: EditDraft, at: number): { draft: EditDraft; index: number } | null {
  if (draft.parts.length >= MAX_PARTS) return null;
  let offset = 0;
  for (let i = 0; i < draft.parts.length; i++) {
    const [a, b] = draft.parts[i]!;
    if (at < offset + (b - a)) {
      const cut = round(a + Math.max(0, at - offset));
      if (cut - a < MIN_PART_S || b - cut < MIN_PART_S) return null;
      const parts = [...draft.parts];
      parts.splice(i, 1, [a, cut], [cut, b]);
      return { draft: { ...draft, parts }, index: i + 1 };
    }
    offset += b - a;
  }
  return null;
}

/** The draft without part `index` (the last part is never removed). */
export function withoutPart(draft: EditDraft, index: number): EditDraft {
  if (draft.parts.length < 2) return draft;
  return { ...draft, parts: draft.parts.filter((_, i) => i !== index) };
}

/** Part `index` with one edge moved by `delta` source seconds (D-059): a part stays at least
 * MIN_PART_S long, never runs into its neighbours, and stays inside the source bounds. */
export function movePartEdge(
  parts: Part[],
  index: number,
  edge: "start" | "end",
  delta: number,
  bounds: { min_start_s: number; max_end_s: number },
): Part[] {
  const part = parts[index];
  if (!part) return parts;
  const [a, b] = part;
  const next: Part =
    edge === "start"
      ? [
          round(
            Math.min(
              Math.max(a + delta, index > 0 ? parts[index - 1]![1] : bounds.min_start_s),
              b - MIN_PART_S,
            ),
          ),
          b,
        ]
      : [
          a,
          round(
            Math.max(
              Math.min(
                b + delta,
                index < parts.length - 1 ? parts[index + 1]![0] : bounds.max_end_s,
              ),
              a + MIN_PART_S,
            ),
          ),
        ];
  return parts.map((old, i) => (i === index ? next : old));
}

const round = (value: number) => Math.round(value * 100) / 100;
const share = (value: number) => Math.round(value * 10000) / 10000;

/** Problems that block saving (empty list = valid). */
export function draftErrors(draft: EditDraft, clip: Clip): string[] {
  const errors: string[] = [];
  if (draft.title.length > TITLE_MAX) {
    errors.push(`The on-video title must be at most ${TITLE_MAX} characters.`);
  }
  const { min_start_s: min, max_end_s: max } = clip.source_bounds;
  if (draft.parts.some(([a, b]) => !Number.isFinite(a) || !Number.isFinite(b))) {
    errors.push("Start and end must be numbers.");
    return errors;
  }
  if (draftStart(draft) < min - 0.01 || draftEnd(draft) > max + 0.01) {
    errors.push(`Trim must stay between ${round(min)} s and ${round(max)} s.`);
  }
  if (draft.parts.some(([a, b]) => b - a < MIN_PART_S - 0.01)) {
    errors.push(`Each part must be at least ${MIN_PART_S} s long.`);
  }
  const length = playedLength(draft);
  if (length < EDIT_MIN_S || length > EDIT_MAX_S) {
    errors.push(`A clip must be ${EDIT_MIN_S}–${EDIT_MAX_S} s long.`);
  }
  if (draft.blur_boxes.some((box) => !(box.start >= 0 && box.end > box.start))) {
    errors.push("Each blur box must end after it starts.");
  }
  if (draft.blur_boxes.some((box) => box.start >= length)) {
    errors.push("Each blur box must start inside the clip.");
  }
  if (draft.blur_boxes.length > MAX_BLUR_BOXES) {
    errors.push(`At most ${MAX_BLUR_BOXES} blur boxes.`);
  }
  return errors;
}

/** A soft warning outside 15–90 s (still allowed, D-035). */
export function lengthWarning(draft: EditDraft): string | null {
  const length = playedLength(draft);
  if (length >= EDIT_MIN_S && length < GOOD_MIN_S) {
    return `${length.toFixed(1)} s is very short; 15–90 s works best.`;
  }
  return null;
}

function roundBox(box: BlurBox): BlurBox {
  return {
    start: round(box.start),
    end: round(box.end),
    x: share(box.x),
    y: share(box.y),
    w: share(box.w),
    h: share(box.h),
  };
}

/** Source time of clip time `t` over `parts` (clamped to the clip). */
export function toSource(t: number, parts: Part[]): number {
  let offset = 0;
  for (const [a, b] of parts) {
    if (t <= offset + (b - a) + 1e-6) return a + Math.max(0, t - offset);
    offset += b - a;
  }
  return parts[parts.length - 1]?.[1] ?? t;
}

/** Clip-time words of the saved clip in source seconds (D-062). */
export function toSourceWords(words: Word[], parts: Part[]): Word[] {
  return words.map((word) => {
    const start = toSource(word.start, parts);
    return { text: word.text, start, end: start + (word.end - word.start) };
  });
}

/** A caption word on the clip's timeline, with its index in the draft's words. */
export interface ShownWord extends Word {
  source: number;
}

/** Words (source seconds) placed on the timeline of `parts`: a word plays when its middle is
 * inside a part; it is moved to clip seconds and clamped to that part. Words in a cut-out
 * range are hidden, not deleted, so dragging the cut back brings them back. */
export function wordsOn(words: Word[], parts: Part[]): ShownWord[] {
  const shown: ShownWord[] = [];
  words.forEach((word, index) => {
    const middle = (word.start + word.end) / 2;
    let offset = 0;
    for (const [a, b] of parts) {
      if (middle >= a && middle < b) {
        shown.push({
          text: word.text,
          start: offset + Math.max(word.start, a) - a,
          end: offset + Math.min(word.end, b) - a,
          source: index,
        });
        return;
      }
      offset += b - a;
    }
  });
  return shown;
}

/** The draft's caption words on its own (edited) timeline. */
export const shownWords = (draft: EditDraft): ShownWord[] => wordsOn(draft.words, draft.parts);

const roundParts = (parts: Part[]): Part[] =>
  normalized(parts).map(([a, b]) => [round(a), round(b)]);

/** Only the changed fields. A cut or trim (D-059: `parts`) without word edits lets the
 * backend re-sync the words; edited words are sent in the saved clip's time. */
export function patchFrom(draft: EditDraft, clip: Clip): ClipPatch {
  const patch: ClipPatch = {};
  if (draft.title !== clip.metadata.title) patch.title = draft.title;
  const parts = roundParts(draft.parts);
  if (JSON.stringify(parts) !== JSON.stringify(roundParts(savedParts(clip)))) patch.parts = parts;
  if (draft.caption_style !== clip.edit.caption_style) patch.caption_style = draft.caption_style;
  if (draft.crop_mode !== clip.edit.crop_mode) patch.crop_mode = draft.crop_mode;
  if (draft.blur !== clip.edit.blur) patch.blur = draft.blur;
  // A word cleared to nothing is removed from the captions (D-055). After a cut the words
  // are always sent, in the new clip's time (D-062).
  const words = keptWords(shownWords(draft)).map((word) => ({
    text: word.text,
    start: round(word.start),
    end: round(word.end),
  }));
  const wordsChanged =
    "parts" in patch ||
    words.length !== clip.words.length ||
    words.some((word, index) => word.text !== clip.words[index]?.text);
  if (wordsChanged) patch.words = words;
  const boxes = draft.blur_boxes.map(roundBox);
  if (JSON.stringify(boxes) !== JSON.stringify(clip.edit.blur_boxes.map(roundBox))) {
    patch.blur_boxes = boxes;
  }
  return patch;
}

/** True when the draft changes what the live-preview base shows (trim or montage, D-057):
 * those show only after the next preview render. */
export function layoutChanged(draft: EditDraft, clip: Clip): boolean {
  const patch = patchFrom(draft, clip);
  return "parts" in patch;
}

export function isDirty(draft: EditDraft, clip: Clip): boolean {
  return Object.keys(patchFrom(draft, clip)).length > 0;
}

/** The caption words to save: trimmed, without the ones cleared to nothing (D-055). */
export function keptWords(words: Word[]): Word[] {
  return words
    .map((word) => ({ text: word.text.trim(), start: word.start, end: word.end }))
    .filter((word) => word.text.length > 0);
}

/** Index of the word playing at `time` (clip seconds), or -1 between words. */
export function wordAt(words: Word[], time: number): number {
  return words.findIndex(
    (word) => time >= word.start && time < Math.max(word.end, word.start + 0.05),
  );
}

/** A new box in the middle of the video from `at_s` for 3 s (cut at the clip end). */
export function newBlurBox(at_s: number, length_s: number): BlurBox {
  const start = round(Math.max(0, Math.min(at_s, length_s - 0.5)));
  return {
    start,
    end: round(Math.min(length_s, start + NEW_BOX_S)),
    x: 0.2,
    y: 0.3,
    w: 0.6,
    h: 0.25,
  };
}

/** Keep a moved or resized box inside the video and at least MIN_BOX_SIDE big. */
export function clampBox(box: BlurBox): BlurBox {
  const w = Math.min(1, Math.max(MIN_BOX_SIDE, box.w));
  const h = Math.min(1, Math.max(MIN_BOX_SIDE, box.h));
  return {
    ...box,
    w,
    h,
    x: Math.min(1 - w, Math.max(0, box.x)),
    y: Math.min(1 - h, Math.max(0, box.y)),
  };
}

/** True while the box is blurred at `time_s` (clip seconds). */
export function boxVisible(box: BlurBox, time_s: number): boolean {
  return time_s >= box.start && time_s < box.end;
}

/** A played part placed on the clip's own timeline (D-056): `start`/`end` are clip seconds,
 * `source` is the source range it comes from. */
export interface TimelinePart {
  start: number;
  end: number;
  source: Part;
}

/** The played parts laid end to end from 0, as the timeline's Video track shows them. */
export function timelineParts(draft: EditDraft): TimelinePart[] {
  let at = 0;
  return draft.parts.map((source) => {
    const part = { start: at, end: at + source[1] - source[0], source };
    at = part.end;
    return part;
  });
}

/** A blur box moved in time by `delta` seconds, kept inside 0..length with its duration. */
export function moveBoxTime(box: BlurBox, delta: number, length: number): BlurBox {
  const duration = box.end - box.start;
  const start = Math.min(Math.max(0, box.start + delta), Math.max(0, length - duration));
  return { ...box, start: round(start), end: round(start + duration) };
}

/** A blur box with one end dragged by `delta` seconds, at least MIN_BOX_S long, inside the clip. */
export function resizeBoxTime(
  box: BlurBox,
  edge: "start" | "end",
  delta: number,
  length: number,
): BlurBox {
  if (edge === "start") {
    const start = Math.min(Math.max(0, box.start + delta), box.end - MIN_BOX_S);
    return { ...box, start: round(start) };
  }
  const end = Math.max(Math.min(length, box.end + delta), box.start + MIN_BOX_S);
  return { ...box, end: round(end) };
}
