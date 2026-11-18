// LLM settings for this browser tab (docs/02 flow D): kept in sessionStorage so the key
// survives a backend restart while the tab is open, and is forgotten when the tab closes.
import type { LlmConfig } from "@/lib/api";

const KEY = "kliprix.llm";

export function loadLlmConfig(): LlmConfig | null {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<LlmConfig>;
    if (!value.provider || !value.model || !value.api_key) return null;
    return value as LlmConfig;
  } catch {
    return null;
  }
}

export function saveLlmConfig(config: LlmConfig): void {
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(config));
  } catch {
    // Storage blocked: the backend still holds the session until it restarts.
  }
}

export function clearLlmConfig(): void {
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    // Nothing stored.
  }
}
