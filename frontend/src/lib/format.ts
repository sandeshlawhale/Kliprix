// Small display helpers (dates in the user's local time, durations, counts).

export function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/** "Today" / "Yesterday" / "Earlier" for the sidebar history (local time). */
export function historyGroup(
  iso: string,
  now: Date = new Date(),
): "Today" | "Yesterday" | "Earlier" {
  const date = new Date(iso);
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = 24 * 60 * 60 * 1000;
  if (date.getTime() >= startOfToday) return "Today";
  if (date.getTime() >= startOfToday - day) return "Yesterday";
  return "Earlier";
}

/** 754.2 -> "12:34"; 3725 -> "1:02:05". */
export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  return `${h > 0 ? `${h}:` : ""}${mm}:${String(s).padStart(2, "0")}`;
}

/** 754.2 -> "00:12:34" (always hh:mm:ss, for source positions in the trim). */
export function hms(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds + 1e-6));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(whole / 3600))}:${pad(Math.floor((whole % 3600) / 60))}:${pad(whole % 60)}`;
}

/** "1:02:05", "12:34.5", "75" -> seconds; null when it is not a time. */
export function parseTime(text: string): number | null {
  const parts = text.trim().split(":");
  if (parts.length > 3 || parts.some((part) => !/^\d+(\.\d+)?$/.test(part))) return null;
  const numbers = parts.map(Number);
  if (numbers.slice(1).some((n) => n >= 60)) return null;
  if (numbers.slice(0, -1).some((n) => !Number.isInteger(n))) return null;
  return numbers.reduce((total, n) => total * 60 + n, 0);
}

export function localDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export const STYLE_LABELS = { pop: "Pop", karaoke: "Karaoke", boxed: "Boxed" } as const;

/** Clip length, language, crop and blur are decided by Kliprix, so only these are shown. */
export function styleSummary(style: {
  caption_style: keyof typeof STYLE_LABELS;
  clips_per_video: number;
}): string {
  return [
    `${STYLE_LABELS[style.caption_style]} captions`,
    plural(style.clips_per_video, "clip"),
  ].join(" · ");
}

/** 1536 -> "1.5 KB"; binary units, one decimal. */
export function bytes(value: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? size : size.toFixed(1)} ${units[unit]}`;
}
