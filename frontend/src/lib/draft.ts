// The Home list of inspected links, kept in sessionStorage so it survives a trip to /style
// and back (docs/02 flow A step 3). Exposed as an external store for useSyncExternalStore.
import type { VideoMeta } from "@/lib/api";

export interface Draft {
  url: string;
  video: VideoMeta;
}

const KEY = "kliprix.home";
const EMPTY: Draft[] = [];
const listeners = new Set<() => void>();
let cachedRaw: string | null = null;
let cachedValue: Draft[] = EMPTY;

function read(): string | null {
  try {
    return window.sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function getDraft(): Draft[] {
  const raw = read();
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    try {
      cachedValue = raw ? (JSON.parse(raw) as Draft[]) : EMPTY;
    } catch {
      cachedValue = EMPTY;
    }
  }
  return cachedValue;
}

export const getServerDraft = (): Draft[] => EMPTY;

export function setDraft(items: Draft[]): void {
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(items));
  } catch {
    // Storage blocked: keep the list in memory for this page only.
    cachedRaw = null;
    cachedValue = items;
  }
  for (const listener of listeners) listener();
}

export function subscribeDraft(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
