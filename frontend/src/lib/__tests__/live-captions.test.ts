import { describe, expect, it } from "vitest";

import type { Word } from "@/lib/api";
import { captionAt, chunkWords, cleanText, estimateWidth, fitTitle } from "@/lib/live-captions";

const w = (text: string, start: number, end: number): Word => ({ text, start, end });

describe("live captions (D-057, same rules as backend/app/render/ass.py)", () => {
  it("cleans text like the renderer", () => {
    expect(cleanText("  a {b}\\c\nd ")).toBe("a (b)/c d");
  });

  it("groups words up to the limit and breaks at punctuation and pauses", () => {
    const words = [
      w("one", 0, 0.3),
      w("two", 0.3, 0.6),
      w("three.", 0.6, 0.9),
      w("four", 1.0, 1.2),
      w("five", 2.0, 2.2), // pause of 0.8 s
    ];
    const chunks = chunkWords(words, 4, estimateWidth);
    expect(chunks.map((chunk) => chunk.words.map((word) => word.text))).toEqual([
      ["one", "two", "three."],
      ["four"],
      ["five"],
    ]);
    expect(chunkWords(words.slice(0, 2), 1, estimateWidth)).toHaveLength(2);
  });

  it("keeps a group up to 0.3 s after its last word, never into the next group", () => {
    const chunks = chunkWords([w("hi.", 0, 0.5), w("there", 0.6, 1.0)], 4, estimateWidth);
    expect(chunks[0]!.end).toBe(0.6);
    expect(chunks[1]!.end).toBeCloseTo(1.3);
  });

  it("wraps at 18 characters per line", () => {
    const chunks = chunkWords(
      [w("absolutely", 0, 0.5), w("wonderful", 0.5, 1), w("day", 1, 1.2)],
      4,
      estimateWidth,
    );
    expect(chunks[0]!.lines).toEqual([[0], [1, 2]]);
  });

  it("shows the spoken word as active and nothing between groups", () => {
    const chunks = chunkWords(
      [w("one", 0, 0.4), w("two", 0.5, 0.9), w("later", 5, 5.5)],
      4,
      estimateWidth,
    );
    expect(captionAt(chunks, 0.2, 10)).toMatchObject({ chunk: 0, active: 0 });
    expect(captionAt(chunks, 0.6, 10)).toMatchObject({ chunk: 0, active: 1 });
    expect(captionAt(chunks, 3, 10)).toBeNull();
    expect(captionAt(chunks, 5.1, 10)?.lines).toEqual([[{ text: "later", index: 0 }]]);
  });

  it("fits the title in two uppercase lines, shrinking the font when needed", () => {
    const short = fitTitle("Clutch on B", estimateWidth);
    expect(short).toEqual({ size: 88, lines: ["CLUTCH ON B"] });
    const long = fitTitle(
      "an extremely long title that would need three lines at full size",
      estimateWidth,
    );
    expect(long.lines.length).toBeLessThanOrEqual(2);
    expect(long.size).toBeLessThan(88);
    expect(fitTitle("", estimateWidth).lines).toEqual([]);
  });
});
