"use client";

import { Timer } from "lucide-react";
import { useEffect, useState } from "react";

import { clock } from "@/lib/format";

/** Time since the backend started this video's current run (`active_since`). The caller
 * hides it when the run ends. Counts real wall-clock time, nothing is estimated. */
export function ElapsedTimer({ since }: { since: string }) {
  const start = new Date(since).getTime();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const seconds = Math.max(0, (now - start) / 1000);
  return (
    <span
      className="text-muted-foreground inline-flex items-center gap-1.5 text-sm"
      aria-label={`Working for ${clock(seconds)}`}
    >
      <span className="bg-brand size-1.5 animate-pulse rounded-full" aria-hidden />
      <Timer className="size-3.5" aria-hidden />
      <span className="font-mono tabular-nums">{clock(seconds)}</span>
    </span>
  );
}
