import { beforeEach, describe, expect, it } from "vitest";

import { loadPins, splitPinned, togglePin } from "@/lib/model-pins";

const models = [
  { id: "a", label: "a" },
  { id: "b", label: "b" },
  { id: "c", label: "c" },
  { id: "d", label: "d" },
];

// Vitest runs in Node: give the module a minimal in-memory localStorage.
function memoryStorage(): Pick<Storage, "getItem" | "setItem" | "clear"> {
  const data = new Map<string, string>();
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => void data.set(key, value),
    clear: () => data.clear(),
  };
}

describe("model pins", () => {
  beforeEach(() => {
    Object.assign(globalThis, { window: { localStorage: memoryStorage() } });
  });

  it("toggles pins per provider and keeps them across reads", () => {
    expect(loadPins("gemini")).toEqual([]);
    expect(togglePin("gemini", "c")).toEqual(["c"]);
    expect(togglePin("gemini", "a")).toEqual(["c", "a"]);
    expect(togglePin("openai", "x")).toEqual(["x"]);
    expect(loadPins("gemini")).toEqual(["c", "a"]);
    expect(togglePin("gemini", "c")).toEqual(["a"]);
    expect(loadPins("openai")).toEqual(["x"]);
  });

  it("ignores broken storage", () => {
    window.localStorage.setItem("kliprix.modelPins", "{not json");
    expect(loadPins("gemini")).toEqual([]);
  });

  it("puts pinned models first in pin order and skips pins the provider no longer lists", () => {
    const { pinned, others } = splitPinned(models, ["d", "gone", "b"]);
    expect(pinned.map((m) => m.id)).toEqual(["d", "b"]);
    expect(others.map((m) => m.id)).toEqual(["a", "c"]);
  });
});
