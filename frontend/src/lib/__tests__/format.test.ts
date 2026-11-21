import { describe, expect, it } from "vitest";

import { bytes, clock, historyGroup, plural, styleSummary } from "@/lib/format";
import { isSupportedLink } from "@/lib/links";

describe("format helpers", () => {
  it("formats clocks and sizes", () => {
    expect(clock(754.2)).toBe("12:34");
    expect(clock(3725)).toBe("1:02:05");
    expect(bytes(512)).toBe("512 B");
    expect(bytes(1536)).toBe("1.5 KB");
    expect(plural(1, "clip")).toBe("1 clip");
    expect(plural(3, "video")).toBe("3 videos");
  });

  it("groups history by local day", () => {
    const now = new Date(2026, 10, 18, 12, 0);
    expect(historyGroup(new Date(2026, 10, 18, 1, 0).toISOString(), now)).toBe("Today");
    expect(historyGroup(new Date(2026, 10, 17, 23, 0).toISOString(), now)).toBe("Yesterday");
    expect(historyGroup(new Date(2026, 10, 1).toISOString(), now)).toBe("Earlier");
  });

  it("summarizes the style", () => {
    expect(
      styleSummary({
        caption_style: "pop",
        clips_per_video: 5,
      }),
    ).toBe("Pop captions · 5 clips");
  });
});

describe("link check", () => {
  const hosts = ["youtube.com", "youtu.be"];
  it("accepts supported hosts and subdomains", () => {
    expect(isSupportedLink("https://www.youtube.com/watch?v=abc", hosts)).toBe(true);
    expect(isSupportedLink("https://youtu.be/abc", hosts)).toBe(true);
  });
  it("rejects other hosts, look-alikes and non-URLs", () => {
    expect(isSupportedLink("https://vimeo.com/1", hosts)).toBe(false);
    expect(isSupportedLink("https://notyoutube.com/x", hosts)).toBe(false);
    expect(isSupportedLink("ftp://youtube.com/x", hosts)).toBe(false);
    expect(isSupportedLink("not a url", hosts)).toBe(false);
  });
});
