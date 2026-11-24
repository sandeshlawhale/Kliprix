import { describe, expect, it } from "vitest";

import type { Clip } from "@/lib/api";
import {
  boxVisible,
  clampBox,
  draftErrors,
  draftFrom,
  isDirty,
  lengthWarning,
  newBlurBox,
  patchFrom,
  playedLength,
  playedParts,
  keptWords,
  wordAt,
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
      blur_boxes: [],
      parts: [],
      montage: false,
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

  it("validates bounds and length", () => {
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
    expect(draftErrors(empty, c)).toEqual([]);
    expect(draftErrors(draftFrom(c), c)).toEqual([]);
  });

  it("warns for short clips", () => {
    const c = clip();
    expect(lengthWarning({ ...draftFrom(c), end_s: 110 })).toMatch(/short/);
    expect(lengthWarning(draftFrom(c))).toBeNull();
  });

  it("sends blur boxes when they change, rounded", () => {
    const c = clip();
    const draft = draftFrom(c);
    draft.blur_boxes.push({ start: 1.234, end: 4, x: 0.123456, y: 0.2, w: 0.5, h: 0.3 });
    expect(patchFrom(draft, c)).toEqual({
      blur_boxes: [{ start: 1.23, end: 4, x: 0.1235, y: 0.2, w: 0.5, h: 0.3 }],
    });
    const saved = {
      ...c,
      edit: { ...c.edit, blur_boxes: [{ start: 1, end: 4, x: 0, y: 0, w: 1, h: 1 }] },
    };
    expect(isDirty(draftFrom(saved), saved)).toBe(false);
  });

  it("validates blur box times", () => {
    const c = clip();
    const backwards = {
      ...draftFrom(c),
      blur_boxes: [{ start: 5, end: 4, x: 0, y: 0, w: 1, h: 1 }],
    };
    expect(draftErrors(backwards, c)).toContain("Each blur box must end after it starts.");
    const late = { ...draftFrom(c), blur_boxes: [{ start: 60, end: 61, x: 0, y: 0, w: 1, h: 1 }] };
    expect(draftErrors(late, c)).toContain("Each blur box must start inside the clip.");
  });

  it("makes, clamps and shows boxes", () => {
    expect(newBlurBox(10, 50)).toMatchObject({ start: 10, end: 13 });
    expect(newBlurBox(49.9, 50)).toMatchObject({ start: 49.5, end: 50 });
    expect(clampBox({ start: 0, end: 1, x: 0.9, y: -0.2, w: 0.3, h: 0.001 })).toEqual({
      start: 0,
      end: 1,
      x: 0.7,
      y: 0,
      w: 0.3,
      h: 0.02,
    });
    const box = { start: 2, end: 4, x: 0, y: 0, w: 1, h: 1 };
    expect([boxVisible(box, 1.9), boxVisible(box, 2), boxVisible(box, 4)]).toEqual([
      false,
      true,
      false,
    ]);
  });

  it("removes cleared or deleted words when saving (D-055)", () => {
    const c = clip();
    const draft = draftFrom(c);
    const second = draft.words[1]!;
    draft.words[0] = { ...draft.words[0]!, text: " " };
    expect(patchFrom(draft, c).words).toEqual([
      { text: second.text, start: second.start, end: second.end },
      ...c.words.slice(2),
    ]);
    const deleted = { ...draftFrom(c), words: draftFrom(c).words.slice(1) };
    expect(patchFrom(deleted, c).words).toHaveLength(c.words.length - 1);
    expect(keptWords([{ text: " hi ", start: 0, end: 1 }])).toEqual([
      { text: "hi", start: 0, end: 1 },
    ]);
  });

  it("finds the word playing at a time", () => {
    const words = [
      { text: "a", start: 0, end: 0.5 },
      { text: "b", start: 1, end: 1.4 },
    ];
    expect(wordAt(words, 0.2)).toBe(0);
    expect(wordAt(words, 0.7)).toBe(-1);
    expect(wordAt(words, 1.39)).toBe(1);
  });
});

describe("montage clips (D-051)", () => {
  function montageClip(): Clip {
    const base = clip();
    return {
      ...base,
      duration_s: 30,
      edit: {
        ...base.edit,
        parts: [
          [100, 110],
          [120, 130],
          [140, 150],
        ],
        montage: true,
      },
    };
  }

  it("plays only the parts and counts their length", () => {
    const c = montageClip();
    const draft = draftFrom(c);
    expect(playedParts(draft, c.edit.parts)).toEqual([
      [100, 110],
      [120, 130],
      [140, 150],
    ]);
    expect(playedLength(draft, c.edit.parts)).toBe(30);
    expect(isDirty(draft, c)).toBe(false);
  });

  it("moves the outer parts with the trim and keeps the inner cuts", () => {
    const c = montageClip();
    const draft = { ...draftFrom(c), start_s: 104, end_s: 145 };
    expect(playedParts(draft, c.edit.parts)).toEqual([
      [104, 110],
      [120, 130],
      [140, 145],
    ]);
    expect(playedLength(draft, c.edit.parts)).toBe(21);
  });

  it("switches montage off as its own change", () => {
    const c = montageClip();
    const draft = { ...draftFrom(c), montage: false };
    expect(playedLength(draft, c.edit.parts)).toBe(50);
    expect(patchFrom(draft, c)).toEqual({ montage: false });
  });

  it("checks the played length, not the range", () => {
    const c = montageClip();
    const draft = { ...draftFrom(c), start_s: 107, end_s: 142 };
    // Plays 3 + 10 + 2 = 15 s although the range is 35 s.
    expect(playedLength(draft, c.edit.parts)).toBe(15);
    expect(draftErrors(draft, c)).toEqual([]);
  });
});
