// Pinned models per provider (D-053): shown first in the Configure API model list.
// A UI preference only, kept in this browser's localStorage (never the key).
import type { ModelEntry, ProviderId } from "@/lib/api";

const KEY = "kliprix.modelPins";

type PinMap = Partial<Record<ProviderId, string[]>>;

function readAll(): PinMap {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return {};
    const value: unknown = JSON.parse(raw);
    return value && typeof value === "object" ? (value as PinMap) : {};
  } catch {
    return {};
  }
}

export function loadPins(provider: ProviderId): string[] {
  const list = readAll()[provider];
  return Array.isArray(list) ? list.filter((id) => typeof id === "string") : [];
}

/** Pins `modelId` if it is not pinned, else unpins it; returns the provider's new pins. */
export function togglePin(provider: ProviderId, modelId: string): string[] {
  const current = loadPins(provider);
  const next = current.includes(modelId)
    ? current.filter((id) => id !== modelId)
    : [...current, modelId];
  try {
    window.localStorage.setItem(KEY, JSON.stringify({ ...readAll(), [provider]: next }));
  } catch {
    // Storage blocked: the pin lasts until the page reloads.
  }
  return next;
}

/** Pinned models first (in pin order), then the rest in the provider's order. */
export function splitPinned(
  models: ModelEntry[],
  pins: string[],
): { pinned: ModelEntry[]; others: ModelEntry[] } {
  const byId = new Map(models.map((model) => [model.id, model]));
  const pinned = pins.flatMap((id) => {
    const model = byId.get(id);
    return model ? [model] : [];
  });
  const pinnedIds = new Set(pinned.map((model) => model.id));
  return { pinned, others: models.filter((model) => !pinnedIds.has(model.id)) };
}
