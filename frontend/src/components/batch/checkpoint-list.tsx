import { Check, Circle, Loader2, Minus, Pause, X } from "lucide-react";

import type { Checkpoint } from "@/lib/api";
import { cn } from "@/lib/utils";

// Transcription is the longest step on most PCs (several minutes for a 15-minute video).
const SLOW_STEP_NOTE = "slowest step, please wait patiently";

/** The muted 7-step list under each video (docs/03, D-024). States come from the backend;
 * nothing ticks on a timer. */
export function CheckpointList({ checkpoints }: { checkpoints: Checkpoint[] }) {
  return (
    <ol className="text-muted-foreground flex flex-col gap-1 text-sm" aria-label="Progress steps">
      {checkpoints.map((step) => (
        <li key={step.key} className="flex flex-col">
          <span
            className={cn(
              "flex items-center gap-2",
              (step.state === "running" || step.state === "paused") && "text-foreground",
              step.state === "failed" && "text-destructive",
            )}
          >
            <StateIcon state={step.state} />
            <span>
              {step.label}
              {step.key === "edit" && step.detail ? ` ${step.detail}` : ""}
            </span>
            {step.engine && (
              // Temporary debug display (D-030): the engine the backend recorded for this
              // video when the step started. Remove before production.
              <span className="border-border rounded border px-1.5 text-xs">{step.engine}</span>
            )}
            {(step.state === "running" || step.state === "paused") &&
              step.key !== "edit" &&
              step.detail && <span className="font-mono text-xs tabular-nums">{step.detail}</span>}
            {/* A paused batch (D-049): the stopped step waits, no spinner. */}
            {step.state === "paused" && <span className="text-xs">(paused)</span>}
            {step.key === "transcribe" &&
              (step.state === "pending" || step.state === "running") && (
                <span className="text-xs">({SLOW_STEP_NOTE})</span>
              )}
            {step.state === "skipped" && step.detail && (
              <span className="text-xs">({step.detail})</span>
            )}
            <span className="sr-only">: {step.state}</span>
          </span>
          {step.state === "failed" && step.detail && step.key !== "edit" && (
            <span className="text-destructive ml-6 text-xs">{step.detail}</span>
          )}
        </li>
      ))}
    </ol>
  );
}

function StateIcon({ state }: { state: Checkpoint["state"] }) {
  const common = "size-4 shrink-0";
  if (state === "done") return <Check className={common} aria-hidden />;
  if (state === "running") return <Loader2 className={cn(common, "animate-spin")} aria-hidden />;
  if (state === "paused") return <Pause className={common} aria-hidden />;
  if (state === "skipped") return <Minus className={common} aria-hidden />;
  if (state === "failed") return <X className={common} aria-hidden />;
  return <Circle className={cn(common, "opacity-50")} aria-hidden />;
}
