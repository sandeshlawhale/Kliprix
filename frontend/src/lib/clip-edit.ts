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

export interface EditDraft {
  start_s: number;
  end_s: number;
  caption_style: Clip["edit"]["caption_style"];
  crop_mode: Clip["edit"]["crop_mode"];
  blur: number;
  words: Word[];
  blur_boxes: BlurBox[];
  /** Montage (D-051): play only the clip's planned parts. */
  montage: boolean;
}

export type Part = [number, number];

export function draftFrom(clip: Clip): EditDraft {
  return {
    start_s: clip.edit.start_s,
    end_s: clip.edit.end_s,
    caption_style: clip.edit.caption_style,
    crop_mode: clip.edit.crop_mode,
    blur: clip.edit.blur,
    words: clip.words.map((word) => ({ ...word })),
    blur_boxes: clip.edit.blur_boxes.map((box) => ({ ...box })),
    montage: clip.edit.montage,
  };
}

/** The source ranges the draft plays (mirrors the backend's `effective`): the planned parts
 * inside the trim, the first and last moved to the trim edges, or the whole range when
 * montage is off or fewer than two parts remain. */
export function playedParts(draft: EditDraft, parts: Part[]): Part[] {
  if (!draft.montage) return [[draft.start_s, draft.end_s]];
  const kept: Part[] = parts
    .filter(([start, end]) => end > draft.start_s && start < draft.end_s)
    .map(([start, end]) => [Math.max(start, draft.start_s), Math.min(end, draft.end_s)]);
  if (kept.length < 2) return [[draft.start_s, draft.end_s]];
  kept[0] = [draft.start_s, kept[0]![1]];
  kept[kept.length - 1] = [kept[kept.length - 1]![0], draft.end_s];
  return kept;
}

/** Seconds the draft plays (the parts' total for a montage). */
export function playedLength(draft: EditDraft, parts: Part[]): number {
  return playedParts(draft, parts).reduce((sum, [start, end]) => sum + end - start, 0);
}

const round = (value: number) => Math.round(value * 100) / 100;
const share = (value: number) => Math.round(value * 10000) / 10000;

/** Problems that block saving (empty list = valid). */
export function draftErrors(draft: EditDraft, clip: Clip): string[] {
  const errors: string[] = [];
  const { min_start_s: min, max_end_s: max } = clip.source_bounds;
  if (!Number.isFinite(draft.start_s) || !Number.isFinite(draft.end_s)) {
    errors.push("Start and end must be numbers.");
    return errors;
  }
  if (draft.start_s < min - 0.01 || draft.end_s > max + 0.01) {
    errors.push(`Trim must stay between ${round(min)} s and ${round(max)} s.`);
  }
  const length = playedLength(draft, clip.edit.parts);
  if (length < EDIT_MIN_S || length > EDIT_MAX_S) {
    errors.push(`A clip must be ${EDIT_MIN_S}–${EDIT_MAX_S} s long.`);
  }
  if (draft.words.some((word) => !word.text.trim())) errors.push("Caption words cannot be empty.");
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
export function lengthWarning(draft: EditDraft, parts: Part[] = []): string | null {
  const length = playedLength(draft, parts);
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

/** Only the changed fields. Trim without word edits lets the backend re-sync the words. */
export function patchFrom(draft: EditDraft, clip: Clip): ClipPatch {
  const patch: ClipPatch = {};
  if (round(draft.start_s) !== round(clip.edit.start_s)) patch.start_s = round(draft.start_s);
  if (round(draft.end_s) !== round(clip.edit.end_s)) patch.end_s = round(draft.end_s);
  if (draft.caption_style !== clip.edit.caption_style) patch.caption_style = draft.caption_style;
  if (draft.crop_mode !== clip.edit.crop_mode) patch.crop_mode = draft.crop_mode;
  if (draft.blur !== clip.edit.blur) patch.blur = draft.blur;
  if (draft.montage !== clip.edit.montage) patch.montage = draft.montage;
  const wordsChanged =
    draft.words.length !== clip.words.length ||
    draft.words.some((word, index) => word.text.trim() !== clip.words[index]?.text);
  if (wordsChanged) {
    patch.words = draft.words.map((word) => ({
      text: word.text.trim(),
      start: word.start,
      end: word.end,
    }));
  }
  const boxes = draft.blur_boxes.map(roundBox);
  if (JSON.stringify(boxes) !== JSON.stringify(clip.edit.blur_boxes.map(roundBox))) {
    patch.blur_boxes = boxes;
  }
  return patch;
}

export function isDirty(draft: EditDraft, clip: Clip): boolean {
  return Object.keys(patchFrom(draft, clip)).length > 0;
}

/** Group words into short lines for the caption text editor. */
export function wordLines(words: Word[], perLine = 6): number[][] {
  const lines: number[][] = [];
  words.forEach((_, index) => {
    if (index % perLine === 0) lines.push([]);
    lines[lines.length - 1]?.push(index);
  });
  return lines;
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
