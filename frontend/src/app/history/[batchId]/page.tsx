"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle } from "lucide-react";
import { use } from "react";
import { toast } from "sonner";

import { CheckpointList } from "@/components/batch/checkpoint-list";
import { ClipCard } from "@/components/batch/clip-card";
import { SourcePolicyPrompt } from "@/components/batch/source-policy-prompt";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { invalidateBatch, useBatch } from "@/hooks/queries";
import { useBatchEvents } from "@/hooks/use-batch-events";
import { cancelBatch, errorMessage, type Video } from "@/lib/api";
import { localDateTime } from "@/lib/format";

const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Processing",
  done: "Done",
  partial: "Partly done",
  failed: "Failed",
  cancelled: "Cancelled",
};
const ACTIVE = ["queued", "running"];

/** The checkpoint list hides once the video and all its clips are finished without a
 * failure (D-024, A-008); it stays when anything failed. */
function videoFinished(video: Video): boolean {
  return (
    video.status === "done" &&
    video.clips.length > 0 &&
    video.clips.every((clip) => clip.status === "done") &&
    video.checkpoints.every((step) => step.state === "done" || step.state === "skipped")
  );
}

export default function BatchPage({ params }: PageProps<"/history/[batchId]">) {
  const { batchId } = use(params);
  const batch = useBatch(batchId);
  const client = useQueryClient();
  useBatchEvents(batchId);
  const cancel = useMutation({
    mutationFn: () => cancelBatch(batchId),
    onSuccess: () => invalidateBatch(client, batchId),
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (batch.isPending) return <Skeleton className="h-96 w-full" />;
  if (batch.isError)
    return (
      <Alert variant="destructive">
        <AlertCircle aria-hidden />
        <AlertTitle>Cannot show this batch</AlertTitle>
        <AlertDescription>{errorMessage(batch.error)}</AlertDescription>
      </Alert>
    );

  const data = batch.data;
  const active = ACTIVE.includes(data.status);
  return (
    <div className="flex flex-col gap-10">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{localDateTime(data.created_at)}</h1>
        <Badge variant="outline">{STATUS_LABELS[data.status] ?? data.status}</Badge>
        {active && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => cancel.mutate()}
            disabled={cancel.isPending}
          >
            Cancel
          </Button>
        )}
      </header>
      <SourcePolicyPrompt batch={data} />
      {data.videos.map((video) => (
        <section
          key={video.id}
          className="flex flex-col gap-4"
          aria-label={video.title ?? video.url}
        >
          <div className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-medium">{video.title ?? video.url}</h2>
              {video.platform && <Badge variant="secondary">{video.platform}</Badge>}
              {video.status === "done" && (
                <Badge variant="outline">
                  {video.has_chat ? "Chat replay used" : "No chat replay"}
                </Badge>
              )}
            </div>
            {video.channel && <p className="text-muted-foreground text-sm">{video.channel}</p>}
            {video.note && <p className="text-muted-foreground text-sm">{video.note}</p>}
          </div>
          {video.status === "running" && (
            <Progress
              value={video.progress * 100}
              aria-label="Video progress"
              className="max-w-md"
            />
          )}
          {video.error && video.status === "failed" && (
            <Alert variant="destructive">
              <AlertCircle aria-hidden />
              <AlertTitle>This video failed</AlertTitle>
              <AlertDescription>{video.error.message}</AlertDescription>
            </Alert>
          )}
          {!videoFinished(video) && <CheckpointList checkpoints={video.checkpoints} />}
          {video.clips.length > 0 && (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
              {video.clips.map((clip) => (
                <ClipCard key={clip.id} clip={clip} batchId={batchId} />
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
