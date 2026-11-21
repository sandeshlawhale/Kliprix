// Clip editor logic without React: the editable draft, its validation, and the PATCH body
// with only what changed (docs/03 "Clip detail", docs/07 PATCH /api/clips/{id}).
import type { Clip, ClipPatch, Word } from "@/lib/api";

export const TITLE_MAX = 40;
export const EDIT_MIN_S = 5;
export const EDIT_MAX_S = 90;
export const GOOD_MIN_S = 15;

export interface EditDraft {
  start_s: number;
  end_s: number;
  caption_style: Clip["edit"]["caption_style"];
  crop_mode: Clip["edit"]["crop_mode"];
  blur: number;
  words: Word[];
}

export function draftFrom(clip: Clip): EditDraft {
  return {
    start_s: clip.edit.start_s,
    end_s: clip.edit.end_s,
    caption_style: clip.edit.caption_style,
    crop_mode: clip.edit.crop_mode,
    blur: clip.edit.blur,
    words: clip.words.map((word) => ({ ...word })),
  };
}

const round = (value: number) => Math.round(value * 100) / 100;

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
  const length = draft.end_s - draft.start_s;
  if (length < EDIT_MIN_S || length > EDIT_MAX_S) {
    errors.push(`A clip must be ${EDIT_MIN_S}–${EDIT_MAX_S} s long.`);
  }
  if (draft.words.some((word) => !word.text.trim())) errors.push("Caption words cannot be empty.");
  return errors;
}

/** A soft warning outside 15–90 s (still allowed, D-035). */
export function lengthWarning(draft: EditDraft): string | null {
  const length = draft.end_s - draft.start_s;
  if (length >= EDIT_MIN_S && length < GOOD_MIN_S) {
    return `${length.toFixed(1)} s is very short; 15–90 s works best.`;
  }
  return null;
}

/** Only the changed fields. Trim without word edits lets the backend re-sync the words. */
export function patchFrom(draft: EditDraft, clip: Clip): ClipPatch {
  const patch: ClipPatch = {};
  if (round(draft.start_s) !== round(clip.edit.start_s)) patch.start_s = round(draft.start_s);
  if (round(draft.end_s) !== round(clip.edit.end_s)) patch.end_s = round(draft.end_s);
  if (draft.caption_style !== clip.edit.caption_style) patch.caption_style = draft.caption_style;
  if (draft.crop_mode !== clip.edit.crop_mode) patch.crop_mode = draft.crop_mode;
  if (draft.blur !== clip.edit.blur) patch.blur = draft.blur;
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
