"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCcw } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { invalidateBatch } from "@/hooks/queries";
import { clipMediaUrl, errorMessage, retryClip, type ClipSummary } from "@/lib/api";
import { clock } from "@/lib/format";
import { cn } from "@/lib/utils";

const STAGE_LABELS: Record<string, string> = {
  cut: "Cutting",
  face_crop: "Framing",
  captions: "Captions",
  render: "Rendering",
  preview: "Preview",
};

export function clipStatusText(clip: ClipSummary): string {
  if (clip.status === "queued")
    return clip.queue_position ? `Queued · #${clip.queue_position}` : "Queued";
  if (clip.status === "running") {
    const stage = STAGE_LABELS[clip.stage ?? ""] ?? "Processing";
    return `Processing · ${stage} ${Math.round(clip.progress * 100)}%`;
  }
  if (clip.status === "failed") return "Failed";
  if (clip.status === "cancelled") return "Cancelled";
  return clip.edited && !clip.has_export ? "Edited" : "Done";
}

/** What the working badge says: "42% · Rendering" or "Queued #2". */
function workingText(clip: ClipSummary): string {
  if (clip.status === "queued")
    return clip.queue_position ? `Queued #${clip.queue_position}` : "Queued";
  const stage = STAGE_LABELS[clip.stage ?? ""] ?? "Processing";
  return `${Math.round(clip.progress * 100)}% · ${stage}`;
}

const CHIP =
  "absolute right-2 bottom-2 inline-flex items-center gap-1 rounded-md bg-black/70 px-1.5 py-0.5 font-mono text-xs text-white tabular-nums backdrop-blur-sm";

/** A bare 9:16 tile: the frame, and in the corner its length or what is happening to it.
 * Title and the rest live on the clip page. */
export function ClipCard({ clip, batchId }: { clip: ClipSummary; batchId: string }) {
  const client = useQueryClient();
  const retry = useMutation({
    mutationFn: () => retryClip(clip.id),
    onSuccess: () => invalidateBatch(client, batchId),
    onError: (error) => toast.error(errorMessage(error)),
  });
  const working = clip.status === "queued" || clip.status === "running";
  const failed = clip.status === "failed";
  const openable = clip.status === "done" || clip.has_export || clip.has_preview;
  const label = `${clip.title || "Untitled clip"} — ${clipStatusText(clip)}`;

  const body = (
    <div
      className="bg-muted ring-border/60 relative aspect-[9/16] w-full overflow-hidden rounded-xl ring-1 transition-shadow duration-200 group-hover:shadow-lg group-hover:shadow-black/20"
      aria-label={openable ? undefined : label}
      title={clip.title || undefined}
    >
      {clip.thumbnail_url ? (
        // Local frame served by the backend; next/image adds nothing for it.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={clipMediaUrl(clip.id, "thumbnail", clip.updated_at)}
          alt=""
          className={cn(
            "size-full object-cover transition-[filter,transform] duration-500",
            (working || failed) && "scale-110 blur-md brightness-75",
          )}
        />
      ) : (
        working && <div className="bg-muted-foreground/10 absolute inset-0 animate-pulse" />
      )}

      {working && (
        <span className={CHIP} role="status">
          <Loader2 className="size-3 animate-spin" aria-hidden />
          {workingText(clip)}
        </span>
      )}

      {failed && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-3 text-center">
          <p className="line-clamp-4 text-xs text-white">
            {clip.error?.message ?? "This clip failed."}
          </p>
          <button
            type="button"
            disabled={retry.isPending}
            onClick={(event) => {
              event.preventDefault();
              retry.mutate();
            }}
            className="inline-flex items-center gap-1 rounded-md bg-white/90 px-2 py-1 text-xs font-medium text-black hover:bg-white disabled:opacity-60"
          >
            <RotateCcw className="size-3" aria-hidden /> Retry
          </button>
        </div>
      )}

      {!working && !failed && <span className={CHIP}>{clock(clip.duration_s)}</span>}
    </div>
  );

  if (!openable) return body;
  return (
    <Link
      href={`/history/${batchId}/clips/${clip.id}`}
      aria-label={label}
      className="focus-visible:ring-brand/60 group block rounded-xl transition-transform duration-200 ease-out outline-none hover:-translate-y-0.5 focus-visible:ring-[3px] active:translate-y-0"
    >
      {body}
    </Link>
  );
}
