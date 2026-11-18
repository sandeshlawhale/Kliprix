import { describe, expect, it } from "vitest";

import type { Clip } from "@/lib/api";
import {
  draftErrors,
  draftFrom,
  isDirty,
  lengthWarning,
  patchFrom,
  wordLines,
} from "@/lib/clip-edit";

function clip(): Clip {
  return {
    id: "c1",
    index: 0,
    status: "done",
    stage: null,
    progress: 1,
    title: "Title",
    start_s: 100,
    end_s: 150,
    duration_s: 50,
    edited: false,
    has_preview: false,
    has_export: true,
    updated_at: "2026-11-18T10:00:00Z",
    video_id: "v1",
    batch_id: "b1",
    metadata: {
      title: "Title",
      youtube_title: "",
      youtube_description: "",
      hashtags: [],
      linkedin_post: "",
      reason: "",
    },
    edit: {
      caption_style: "pop",
      caption_language: "en",
      crop_mode: "auto",
      blur: 30,
      start_s: 100,
      end_s: 150,
    },
    words: [
      { text: "hello", start: 0.5, end: 0.9 },
      { text: "world", start: 1.0, end: 1.4 },
    ],
    source_bounds: { min_start_s: 0, max_end_s: 600 },
  } as Clip;
}

describe("clip editor logic", () => {
  it("starts clean", () => {
    const c = clip();
    expect(isDirty(draftFrom(c), c)).toBe(false);
    expect(patchFrom(draftFrom(c), c)).toEqual({});
  });

  it("sends only changed fields, trim without words", () => {
    const c = clip();
    const draft = { ...draftFrom(c), start_s: 95.123, blur: 10 };
    expect(patchFrom(draft, c)).toEqual({ start_s: 95.12, blur: 10 });
  });

  it("sends all words when one is fixed", () => {
    const c = clip();
    const draft = draftFrom(c);
    draft.words[1] = { ...draft.words[1]!, text: " World! " };
    expect(patchFrom(draft, c).words).toEqual([
      { text: "hello", start: 0.5, end: 0.9 },
      { text: "World!", start: 1.0, end: 1.4 },
    ]);
  });

  it("validates bounds, length and empty words", () => {
    const c = clip();
    expect(draftErrors({ ...draftFrom(c), end_s: 700 }, c)).toContain(
      "Trim must stay between 0 s and 600 s.",
    );
    expect(draftErrors({ ...draftFrom(c), end_s: 102 }, c)).toContain(
      "A clip must be 5–90 s long.",
    );
    expect(draftErrors({ ...draftFrom(c), end_s: 200 }, c)).toContain(
      "A clip must be 5–90 s long.",
    );
    const empty = draftFrom(c);
    empty.words[0] = { ...empty.words[0]!, text: "  " };
    expect(draftErrors(empty, c)).toContain("Caption words cannot be empty.");
    expect(draftErrors(draftFrom(c), c)).toEqual([]);
  });

  it("warns for short clips", () => {
    const c = clip();
    expect(lengthWarning({ ...draftFrom(c), end_s: 110 })).toMatch(/short/);
    expect(lengthWarning(draftFrom(c))).toBeNull();
  });

  it("groups words into lines", () => {
    expect(
      wordLines(
        Array.from({ length: 7 }, (_, i) => ({ text: `${i}`, start: i, end: i })),
        3,
      ),
    ).toEqual([[0, 1, 2], [3, 4, 5], [6]]);
  });
});
