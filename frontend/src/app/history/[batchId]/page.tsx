"use client";

import { AlertCircle } from "lucide-react";
import { use } from "react";

import { CheckpointList } from "@/components/batch/checkpoint-list";
import { ClipCard } from "@/components/batch/clip-card";
import { ElapsedTimer } from "@/components/batch/elapsed-timer";
import { MoreClips } from "@/components/batch/more-clips";
import { SourcePolicyPrompt } from "@/components/batch/source-policy-prompt";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { useBatch } from "@/hooks/queries";
import { useBatchEvents } from "@/hooks/use-batch-events";
import { errorMessage, type Video } from "@/lib/api";

/** The video and all its clips are finished without a failure (D-024, A-008). */
function videoFinished(video: Video): boolean {
  return (
    video.status === "done" &&
    video.clips.length > 0 &&
    video.clips.every((clip) => clip.status === "done") &&
    video.checkpoints.every((step) => step.state === "done" || step.state === "skipped")
  );
}

/** The 7 steps show only while this video is being worked on, or when something failed
 * (D-038); a video still waiting in the queue shows none. A paused video (D-049) shows
 * where it stopped. */
function showSteps(video: Video): boolean {
  if (video.checkpoints.some((step) => step.state === "paused")) return true;
  if (video.status === "queued") return false;
  if (video.status === "failed") return true;
  return !videoFinished(video) && (video.status === "running" || Boolean(video.active_since));
}

export default function BatchPage({ params }: PageProps<"/history/[batchId]">) {
  const { batchId } = use(params);
  const batch = useBatch(batchId);
  useBatchEvents(batchId);

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
  const defaultCount = Number(data.style.clips_per_video ?? 5);
  return (
    <div className="flex flex-col gap-12">
      <h1 className="sr-only">Batch</h1>
      <SourcePolicyPrompt batch={data} />
      {data.videos.map((video) => (
        <VideoSection
          key={video.id}
          video={video}
          batchId={batchId}
          defaultCount={defaultCount}
          paused={data.status === "paused"}
        />
      ))}
    </div>
  );
}

function VideoSection({
  video,
  batchId,
  defaultCount,
  paused,
}: {
  video: Video;
  batchId: string;
  defaultCount: number;
  paused: boolean;
}) {
  const name = video.title ?? video.url;
  return (
    <section className="flex flex-col gap-4" aria-label={name}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="min-w-0 truncate text-xl font-semibold tracking-tight">{name}</h2>
            {video.platform && <Badge variant="secondary">{video.platform}</Badge>}
            {video.status === "queued" && (
              <Badge variant="outline">{paused ? "Paused" : "Waiting"}</Badge>
            )}
            {video.status === "done" && (
              <Badge variant="outline">
                {video.has_chat ? "Chat replay used" : "No chat replay"}
              </Badge>
            )}
            {video.active_since && <ElapsedTimer since={video.active_since} />}
          </div>
          {video.channel && <p className="text-muted-foreground text-sm">{video.channel}</p>}
          {video.note && <p className="text-muted-foreground text-sm">{video.note}</p>}
        </div>
        {/* Only for videos that got past the download (D-034). */}
        {video.checkpoints[0]?.state === "done" && (
          <MoreClips video={video} batchId={batchId} defaultCount={defaultCount} />
        )}
      </div>
      {video.status === "running" && (
        <Progress value={video.progress * 100} aria-label="Video progress" className="max-w-md" />
      )}
      {video.error && video.status === "failed" && (
        <Alert variant="destructive">
          <AlertCircle aria-hidden />
          <AlertTitle>This video failed</AlertTitle>
          <AlertDescription>{video.error.message}</AlertDescription>
        </Alert>
      )}
      {showSteps(video) && <CheckpointList checkpoints={video.checkpoints} />}
      {video.clips.length > 0 && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
          {video.clips.map((clip) => (
            <ClipCard key={clip.id} clip={clip} batchId={batchId} batchPaused={paused} />
          ))}
        </div>
      )}
    </section>
  );
}
