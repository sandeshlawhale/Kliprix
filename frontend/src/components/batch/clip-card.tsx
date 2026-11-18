"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { invalidateBatch } from "@/hooks/queries";
import { clipMediaUrl, errorMessage, retryClip, type ClipSummary } from "@/lib/api";
import { clock } from "@/lib/format";

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

export function ClipCard({ clip, batchId }: { clip: ClipSummary; batchId: string }) {
  const client = useQueryClient();
  const retry = useMutation({
    mutationFn: () => retryClip(clip.id),
    onSuccess: () => invalidateBatch(client, batchId),
    onError: (error) => toast.error(errorMessage(error)),
  });
  const status = clipStatusText(clip);
  const openable = clip.status === "done" || clip.has_export || clip.has_preview;

  const body = (
    <Card className="flex h-full flex-col gap-2 overflow-hidden p-2">
      {clip.thumbnail_url ? (
        // Local frame served by the backend; next/image adds nothing for it.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={clipMediaUrl(clip.id, "thumbnail", clip.updated_at)}
          alt=""
          className="bg-muted aspect-[9/16] w-full rounded-md object-cover"
        />
      ) : (
        <Skeleton className="aspect-[9/16] w-full" />
      )}
      <p className="line-clamp-2 text-sm font-medium">{clip.title || "Untitled clip"}</p>
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground font-mono text-xs">{clock(clip.duration_s)}</span>
        <Badge
          variant={
            clip.status === "failed"
              ? "destructive"
              : clip.status === "done"
                ? "secondary"
                : "outline"
          }
        >
          {status}
        </Badge>
      </div>
      {clip.status === "running" && (
        <Progress value={clip.progress * 100} aria-label="Clip progress" />
      )}
      {clip.status === "failed" && (
        <div className="flex flex-col gap-2">
          {clip.error && <p className="text-destructive text-xs">{clip.error.message}</p>}
          <Button
            size="sm"
            variant="outline"
            disabled={retry.isPending}
            onClick={(event) => {
              event.preventDefault();
              retry.mutate();
            }}
          >
            Retry
          </Button>
        </div>
      )}
    </Card>
  );

  if (!openable) return body;
  return (
    <Link
      href={`/history/${batchId}/clips/${clip.id}`}
      className="focus-visible:ring-ring/50 rounded-xl outline-none focus-visible:ring-[3px]"
    >
      {body}
    </Link>
  );
}
