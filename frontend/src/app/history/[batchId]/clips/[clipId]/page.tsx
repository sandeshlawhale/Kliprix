"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, Download, FileText, Loader2, Pencil } from "lucide-react";
import Link from "next/link";
import { use, useRef, useState, type ReactNode, type RefObject } from "react";
import { toast } from "sonner";

import { clipStatusText } from "@/components/batch/clip-card";
import { BlurBoxOverlay } from "@/components/clip/blur-box-overlay";
import { EditPanel } from "@/components/clip/edit-panel";
import { MetadataPanel } from "@/components/clip/metadata-panel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { invalidateBatch, keys, useClip } from "@/hooks/queries";
import { useBatchEvents } from "@/hooks/use-batch-events";
import {
  clipDownloadUrl,
  clipMediaUrl,
  errorMessage,
  exportClip,
  previewClip,
  type Clip,
} from "@/lib/api";
import { draftFrom, type EditDraft } from "@/lib/clip-edit";

type View = "metadata" | "edit";

export default function ClipPage({ params }: PageProps<"/history/[batchId]/clips/[clipId]">) {
  const { batchId, clipId } = use(params);
  const clip = useClip(clipId);
  const [view, setView] = useState<View>("metadata");
  useBatchEvents(batchId, [clipId]);

  if (clip.isPending) return <Skeleton className="h-[640px] w-full" />;
  if (clip.isError)
    return (
      <Alert variant="destructive">
        <AlertCircle aria-hidden />
        <AlertTitle>Cannot show this clip</AlertTitle>
        <AlertDescription>{errorMessage(clip.error)}</AlertDescription>
      </Alert>
    );

  const data = clip.data;
  return (
    // Fits the window (main has 2.5rem padding top and bottom); only the side panel scrolls.
    <div className="flex h-[calc(100svh-8.5rem)] min-h-[520px] flex-col gap-5">
      <Toolbar
        clip={data}
        batchId={batchId}
        view={view}
        onToggleView={() => setView(view === "edit" ? "metadata" : "edit")}
      />
      {/* Remount the draft when the saved clip changes (after apply or a render). */}
      <Workspace key={data.updated_at} clip={data} view={view} />
    </div>
  );
}

/** Player and side panel share the edit draft, so blur boxes can be drawn on the video. */
function Workspace({ clip, view }: { clip: Clip; view: View }) {
  const [draft, setDraft] = useState<EditDraft>(() => draftFrom(clip));
  const [selectedBox, setSelectedBox] = useState<number | null>(null);
  const [time, setTime] = useState(0);
  const video = useRef<HTMLVideoElement>(null);
  const editing = view === "edit";

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[auto_minmax(0,1fr)] gap-8">
      <Player
        clip={clip}
        video={video}
        onTime={setTime}
        overlay={
          editing ? (
            <BlurBoxOverlay
              video={video}
              boxes={draft.blur_boxes}
              time={time}
              selected={selectedBox}
              onSelect={setSelectedBox}
              onChange={(index, box) =>
                setDraft((current) => ({
                  ...current,
                  blur_boxes: current.blur_boxes.map((old, i) => (i === index ? box : old)),
                }))
              }
            />
          ) : null
        }
      />
      <section aria-label={editing ? "Edit" : "Metadata"} className="min-h-0 overflow-y-auto pr-2">
        {editing ? (
          <EditPanel
            clip={clip}
            draft={draft}
            setDraft={setDraft}
            videoTime={() => video.current?.currentTime ?? time}
            selectedBox={selectedBox}
            onSelectBox={(index) => {
              setSelectedBox(index);
              const box = index === null ? undefined : draft.blur_boxes[index];
              if (box && video.current) video.current.currentTime = box.start;
            }}
          />
        ) : (
          <MetadataPanel clip={clip} />
        )}
      </section>
    </div>
  );
}

function Toolbar({
  clip,
  batchId,
  view,
  onToggleView,
}: {
  clip: Clip;
  batchId: string;
  view: View;
  onToggleView: () => void;
}) {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: keys.clip(clip.id) });
    invalidateBatch(client, clip.batch_id);
  };
  const preview = useMutation({
    mutationFn: () => previewClip(clip.id),
    onSuccess: refresh,
    onError: (error) => toast.error(errorMessage(error)),
  });
  const render = useMutation({
    mutationFn: () => exportClip(clip.id),
    onSuccess: refresh,
    onError: (error) => toast.error(errorMessage(error)),
  });
  const busy = clip.status === "queued" || clip.status === "running";

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <Button variant="ghost" size="sm" asChild className="-ml-2.5">
        <Link href={`/history/${batchId}`}>
          <ArrowLeft aria-hidden /> Back to batch
        </Link>
      </Button>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" onClick={onToggleView} aria-pressed={view === "edit"}>
          {view === "edit" ? (
            <>
              <FileText aria-hidden /> Metadata
            </>
          ) : (
            <>
              <Pencil aria-hidden /> Edit
            </>
          )}
        </Button>
        <Button
          variant="outline"
          disabled={busy || preview.isPending}
          onClick={() => preview.mutate()}
        >
          {preview.isPending && <Loader2 className="animate-spin" aria-hidden />}
          Preview
        </Button>
        <Button
          variant="outline"
          disabled={busy || render.isPending}
          onClick={() => render.mutate()}
        >
          {render.isPending && <Loader2 className="animate-spin" aria-hidden />}
          Export
        </Button>
        {clip.has_export ? (
          <Button variant="brand" asChild>
            <a href={clipDownloadUrl(clip.id)} download>
              <Download aria-hidden /> Download
            </a>
          </Button>
        ) : (
          <Button variant="brand" disabled>
            <Download aria-hidden /> Download
          </Button>
        )}
      </div>
    </div>
  );
}

function Player({
  clip,
  video,
  onTime,
  overlay,
}: {
  clip: Clip;
  video: RefObject<HTMLVideoElement | null>;
  onTime: (time: number) => void;
  overlay: ReactNode;
}) {
  const busy = clip.status === "queued" || clip.status === "running";
  const kind = clip.has_preview ? "preview" : clip.has_export ? "export" : null;
  const needsPreview = clip.edited && !clip.has_preview && !busy;

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div className="relative min-h-0 flex-1">
        {kind ? (
          <video
            ref={video}
            key={`${kind}-${clip.updated_at}`}
            onTimeUpdate={(event) => onTime(event.currentTarget.currentTime)}
            onSeeked={(event) => onTime(event.currentTarget.currentTime)}
            src={clipMediaUrl(clip.id, kind, clip.updated_at)}
            controls
            playsInline
            className="bg-muted aspect-[9/16] h-full max-w-[min(420px,40vw)] rounded-xl object-contain"
          />
        ) : (
          <div className="bg-muted text-muted-foreground flex aspect-[9/16] h-full max-w-[min(420px,40vw)] items-center justify-center rounded-xl p-6 text-center text-sm">
            {busy ? "Rendering…" : "No rendered video yet. Make a preview to see your changes."}
          </div>
        )}
        {kind && overlay}
      </div>
      {clip.status === "running" && (
        <Progress value={clip.progress * 100} aria-label="Render progress" />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={clip.status === "failed" ? "destructive" : "outline"}>
          {needsPreview ? "Edited — preview needed" : clipStatusText(clip)}
        </Badge>
        {kind && <span className="text-muted-foreground text-xs">Showing the {kind}</span>}
      </div>
      {clip.warning && <p className="text-muted-foreground max-w-80 text-xs">{clip.warning}</p>}
      {clip.error && clip.status === "failed" && (
        <p className="text-destructive max-w-80 text-sm">{clip.error.message}</p>
      )}
    </div>
  );
}
