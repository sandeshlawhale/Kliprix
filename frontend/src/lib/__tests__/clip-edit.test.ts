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
  moveBoxTime,
  movePartEdge,
  normalized,
  splitAt,
  withMontage,
  withoutPart,
  withTrim,
  type Part,
  resizeBoxTime,
  timelineParts,
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

  it("sends only changed fields, trim as parts without words", () => {
    const c = clip();
    const draft = { ...withTrim(draftFrom(c), 95.123, 150), blur: 10 };
    expect(patchFrom(draft, c)).toEqual({ parts: [[95.12, 150]], blur: 10 });
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
    expect(draftErrors(withTrim(draftFrom(c), 100, 700), c)).toContain(
      "Trim must stay between 0 s and 600 s.",
    );
    expect(draftErrors(withTrim(draftFrom(c), 100, 102), c)).toContain(
      "A clip must be 5–90 s long.",
    );
    expect(draftErrors(withTrim(draftFrom(c), 100, 200), c)).toContain(
      "A clip must be 5–90 s long.",
    );
    const empty = draftFrom(c);
    empty.words[0] = { ...empty.words[0]!, text: "  " };
    expect(draftErrors(empty, c)).toEqual([]);
    expect(draftErrors(draftFrom(c), c)).toEqual([]);
  });

  it("warns for short clips", () => {
    const c = clip();
    expect(lengthWarning(withTrim(draftFrom(c), 100, 110))).toMatch(/short/);
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
    expect(playedParts(draft)).toEqual([
      [100, 110],
      [120, 130],
      [140, 150],
    ]);
    expect(playedLength(draft)).toBe(30);
    expect(isDirty(draft, c)).toBe(false);
  });

  it("moves the outer parts with the trim and keeps the inner cuts", () => {
    const c = montageClip();
    const draft = withTrim(draftFrom(c), 104, 145);
    expect(playedParts(draft)).toEqual([
      [104, 110],
      [120, 130],
      [140, 145],
    ]);
    expect(playedLength(draft)).toBe(21);
  });

  it("switches montage off and on again", () => {
    const c = montageClip();
    const off = withMontage(draftFrom(c), false, c.edit.parts as Part[]);
    expect(playedLength(off)).toBe(50);
    expect(patchFrom(off, c)).toEqual({ parts: [[100, 150]] });
    expect(playedLength(withMontage(off, true, c.edit.parts as Part[]))).toBe(30);
  });

  it("checks the played length, not the range", () => {
    const c = montageClip();
    const draft = withTrim(draftFrom(c), 107, 142);
    // Plays 3 + 10 + 2 = 15 s although the range is 35 s.
    expect(playedLength(draft)).toBe(15);
    expect(draftErrors(draft, c)).toEqual([]);
  });
});

describe("timeline editor (D-056)", () => {
  const box = { start: 2, end: 5, x: 0.1, y: 0.1, w: 0.5, h: 0.5 };

  it("saves the on-video title with the edits and checks its length", () => {
    const c = clip();
    const draft = { ...draftFrom(c), title: "New title" };
    expect(patchFrom(draft, c)).toEqual({ title: "New title" });
    expect(draftErrors({ ...draft, title: "x".repeat(41) }, c)).toContain(
      "The on-video title must be at most 40 characters.",
    );
  });

  it("lays the whole range from 0 when there is no montage", () => {
    const c = clip();
    expect(timelineParts(draftFrom(c))).toEqual([{ start: 0, end: 50, source: [100, 150] }]);
  });

  it("lays montage parts end to end in clip time", () => {
    const c = clip();
    const draft = {
      ...draftFrom(c),
      parts: [
        [100, 110],
        [120, 150],
      ] as Part[],
    };
    const parts = timelineParts(draft);
    expect(parts).toEqual([
      { start: 0, end: 10, source: [100, 110] },
      { start: 10, end: 40, source: [120, 150] },
    ]);
  });

  it("moves a blur box in time, keeping its length inside the clip", () => {
    expect(moveBoxTime(box, 1.5, 20)).toMatchObject({ start: 3.5, end: 6.5 });
    expect(moveBoxTime(box, -10, 20)).toMatchObject({ start: 0, end: 3 });
    expect(moveBoxTime(box, 30, 20)).toMatchObject({ start: 17, end: 20 });
  });

  it("drags one end of a blur box, never past the other or the clip", () => {
    expect(resizeBoxTime(box, "start", -1, 20)).toMatchObject({ start: 1, end: 5 });
    expect(resizeBoxTime(box, "start", 10, 20)).toMatchObject({ start: 4.8, end: 5 });
    expect(resizeBoxTime(box, "end", 30, 20)).toMatchObject({ start: 2, end: 20 });
    expect(resizeBoxTime(box, "end", -10, 20)).toMatchObject({ start: 2, end: 2.2 });
  });
});

describe("cut, delete and extend (D-059)", () => {
  const bounds = { min_start_s: 0, max_end_s: 600 };

  it("splits the part under the playhead; touching pieces save as one", () => {
    const c = clip();
    const result = splitAt(draftFrom(c), 20);
    expect(result?.index).toBe(1);
    expect(result?.draft.parts).toEqual([
      [100, 120],
      [120, 150],
    ]);
    expect(isDirty(result!.draft, c)).toBe(false);
    expect(splitAt(draftFrom(c), 0.2)).toBeNull();
  });

  it("deleting a split piece cuts it out of the clip", () => {
    const c = clip();
    const first = splitAt(draftFrom(c), 20)!.draft;
    const second = splitAt(first, 30)!.draft;
    const cut = withoutPart(second, 1);
    expect(cut.parts).toEqual([
      [100, 120],
      [130, 150],
    ]);
    expect(playedLength(cut)).toBe(40);
    expect(patchFrom(cut, c)).toEqual({
      parts: [
        [100, 120],
        [130, 150],
      ],
    });
    expect(withoutPart(draftFrom(c), 0).parts).toEqual([[100, 150]]);
  });

  it("drags a part's ends: shorter, longer into the source, never into a neighbour", () => {
    const parts: Part[] = [
      [100, 120],
      [130, 150],
    ];
    expect(movePartEdge(parts, 0, "start", -30, bounds)[0]).toEqual([70, 120]);
    expect(movePartEdge(parts, 0, "end", 50, bounds)[0]).toEqual([100, 130]);
    expect(movePartEdge(parts, 1, "start", -50, bounds)[1]).toEqual([120, 150]);
    expect(movePartEdge(parts, 1, "end", 900, bounds)[1]).toEqual([130, 600]);
    expect(movePartEdge(parts, 0, "start", -500, bounds)[0]).toEqual([0, 120]);
    expect(movePartEdge(parts, 0, "end", -100, bounds)[0]).toEqual([100, 100.5]);
  });

  it("trims across a cut and merges touching parts", () => {
    const draft = {
      ...draftFrom(clip()),
      parts: [
        [100, 120],
        [130, 150],
      ] as Part[],
    };
    expect(withTrim(draft, 125, 160).parts).toEqual([[125, 160]]);
    expect(
      normalized([
        [130, 150],
        [100, 120],
        [120, 125],
      ]),
    ).toEqual([
      [100, 125],
      [130, 150],
    ]);
  });
});
