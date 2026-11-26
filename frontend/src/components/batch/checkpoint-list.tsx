"use client";

import { Check, ChevronDown, Circle, Loader2, Minus, Pause, X } from "lucide-react";
import { useEffect, useState } from "react";

import type { Checkpoint } from "@/lib/api";
import { currentStepIndex } from "@/lib/checkpoints";
import { cn } from "@/lib/utils";

// Transcription is the longest step on most PCs (several minutes for a 15-minute video).
const SLOW_STEP_NOTE = "slowest step, please wait patiently";

/** The 7 steps under each video (docs/03, D-024). Only the step being worked on shows, with
 * its percentage and hints (D-055); hovering it shows an arrow that opens all 7 below it, so
 * the current step stays where it is (D-066). States come from the backend; nothing ticks on
 * a timer. */
export function CheckpointList({ checkpoints }: { checkpoints: Checkpoint[] }) {
  const [open, setOpen] = useState(false);
  const current = currentStepIndex(checkpoints);
  if (current < 0) return null;
  const step = checkpoints[current]!;

  return (
    <div className="text-muted-foreground flex flex-col gap-1 text-sm">
      <div className="group flex flex-col">
        <span className="flex min-w-0 items-center gap-2">
          <StepLine step={step} />
          <StepHints step={step} />
          <span className="shrink-0 text-xs tabular-nums">
            step {current + 1} of {checkpoints.length}
          </span>
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-label={open ? "Hide all steps" : "Show all steps"}
            className={cn(
              "hover:bg-accent hover:text-foreground focus-visible:ring-ring shrink-0 rounded p-0.5 transition-opacity focus-visible:opacity-100 focus-visible:ring-2 focus-visible:outline-none",
              open ? "opacity-100" : "opacity-0 group-hover:opacity-100",
            )}
          >
            <ChevronDown
              className={cn("size-4 transition-transform", open && "rotate-180")}
              aria-hidden
            />
          </button>
        </span>
        <StepError step={step} />
      </div>
      {open && (
        <ol
          className="border-border ml-2 flex flex-col gap-1 border-l pl-3"
          aria-label="Progress steps"
        >
          {checkpoints.map((item) => (
            <li key={item.key} className="flex flex-col">
              <StepLine step={item} />
              {item.key !== step.key && <StepError step={item} />}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function StepError({ step }: { step: Checkpoint }) {
  if (step.state !== "failed" || !step.detail || step.key === "edit") return null;
  return <span className="text-destructive ml-6 text-xs">{step.detail}</span>;
}

function StepLine({ step }: { step: Checkpoint }) {
  const active = step.state === "running" || step.state === "paused";
  return (
    <span
      className={cn(
        "flex shrink-0 items-center gap-2",
        active && "text-foreground",
        step.state === "failed" && "text-destructive",
      )}
    >
      <StateIcon state={step.state} />
      <span>
        {step.label}
        {step.key === "edit" && step.detail ? ` ${step.detail}` : ""}
      </span>
      {active && step.key !== "edit" && step.detail && (
        <span className="font-mono text-xs tabular-nums">{step.detail}</span>
      )}
      {/* A paused batch (D-049): the stopped step waits, no spinner. */}
      {step.state === "paused" && <span className="text-xs">(paused)</span>}
      {step.state === "skipped" && step.detail && <span className="text-xs">({step.detail})</span>}
      <span className="sr-only">: {step.state}</span>
    </span>
  );
}

/** How long each hint stays before the next one rolls in (D-066). */
const HINT_MS = 2000;

/** The current step's hints (engine in use, "slowest step"), one at a time: every 2 s the
 * shown one moves up and out while the next comes in from below (D-066). */
function StepHints({ step }: { step: Checkpoint }) {
  const hints: string[] = [];
  if (step.engine) hints.push(step.engine);
  if (step.key === "transcribe" && (step.state === "pending" || step.state === "running")) {
    hints.push(SLOW_STEP_NOTE);
  }
  const count = hints.length;
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (count < 2) return;
    const id = window.setInterval(() => setTick((value) => value + 1), HINT_MS);
    return () => window.clearInterval(id);
  }, [count]);
  if (count === 0) return null;

  const shown = tick % count;
  const leaving = tick > 0 && count > 1 ? (tick - 1) % count : -1;
  return (
    // Every hint sits in the same grid cell, so the box is as wide as the longest one and
    // nothing next to it moves when they swap.
    <span className="grid min-w-0 overflow-hidden text-xs" aria-live="off">
      {hints.map((hint, index) => (
        <span
          // A new key restarts the animation each turn.
          key={index === shown || index === leaving ? `${index}-${tick}` : index}
          aria-hidden={index !== shown}
          className={cn(
            "col-start-1 row-start-1 truncate",
            index !== shown && index !== leaving && "invisible",
          )}
          style={
            index === shown && tick > 0
              ? { animation: "kliprix-hint-in 350ms ease-out both" }
              : index === leaving
                ? { animation: "kliprix-hint-out 350ms ease-in both" }
                : undefined
          }
        >
          {hint}
        </span>
      ))}
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
