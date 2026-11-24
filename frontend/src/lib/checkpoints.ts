import type { Checkpoint } from "@/lib/api";

/** The step to show while a video is worked on (D-055): the running, paused or failed one;
 * between steps the next one still to do; when all are finished the last one. -1 when
 * there are no steps. */
export function currentStepIndex(steps: Pick<Checkpoint, "state">[]): number {
  const active = steps.findIndex(
    (step) => step.state === "running" || step.state === "paused" || step.state === "failed",
  );
  if (active >= 0) return active;
  const pending = steps.findIndex((step) => step.state === "pending");
  if (pending >= 0) return pending;
  return steps.length - 1;
}
