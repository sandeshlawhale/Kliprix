"use client";

import { Check, ChevronDown, Circle, Loader2, Minus, Pause, X } from "lucide-react";
import { useState } from "react";

import type { Checkpoint } from "@/lib/api";
import { currentStepIndex } from "@/lib/checkpoints";
import { cn } from "@/lib/utils";

// Transcription is the longest step on most PCs (several minutes for a 15-minute video).
const SLOW_STEP_NOTE = "slowest step, please wait patiently";

/** The 7 steps under each video (docs/03, D-024). Only the step being worked on shows, with
 * its percentage and hint (D-055); hovering it shows an arrow that opens all 7. States come
 * from the backend; nothing ticks on a timer. */
export function CheckpointList({ checkpoints }: { checkpoints: Checkpoint[] }) {
  const [open, setOpen] = useState(false);
  const current = currentStepIndex(checkpoints);
  if (current < 0) return null;
  const shown = open ? checkpoints : [checkpoints[current]!];

  return (
    <ol className="text-muted-foreground flex flex-col gap-1 text-sm" aria-label="Progress steps">
      {shown.map((step) => {
        const isCurrent = step.key === checkpoints[current]?.key;
        return (
          <li key={step.key} className="group flex flex-col">
            <span className="flex items-center gap-2">
              <StepLine step={step} />
              {isCurrent && (
                <>
                  <span className="text-xs tabular-nums">
                    step {current + 1} of {checkpoints.length}
                  </span>
                  <button
                    type="button"
                    onClick={() => setOpen((value) => !value)}
                    aria-expanded={open}
                    aria-label={open ? "Show only the current step" : "Show all steps"}
                    className={cn(
                      "hover:bg-accent hover:text-foreground focus-visible:ring-ring rounded p-0.5 transition-opacity focus-visible:opacity-100 focus-visible:ring-2 focus-visible:outline-none",
                      open ? "opacity-100" : "opacity-0 group-hover:opacity-100",
                    )}
                  >
                    <ChevronDown
                      className={cn("size-4 transition-transform", open && "rotate-180")}
                      aria-hidden
                    />
                  </button>
                </>
              )}
            </span>
            {step.state === "failed" && step.detail && step.key !== "edit" && (
              <span className="text-destructive ml-6 text-xs">{step.detail}</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function StepLine({ step }: { step: Checkpoint }) {
  const active = step.state === "running" || step.state === "paused";
  return (
    <span
      className={cn(
        "flex items-center gap-2",
        active && "text-foreground",
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
      {active && step.key !== "edit" && step.detail && (
        <span className="font-mono text-xs tabular-nums">{step.detail}</span>
      )}
      {/* A paused batch (D-049): the stopped step waits, no spinner. */}
      {step.state === "paused" && <span className="text-xs">(paused)</span>}
      {step.key === "transcribe" && (step.state === "pending" || step.state === "running") && (
        <span className="text-xs">({SLOW_STEP_NOTE})</span>
      )}
      {step.state === "skipped" && step.detail && <span className="text-xs">({step.detail})</span>}
      <span className="sr-only">: {step.state}</span>
    </span>
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
