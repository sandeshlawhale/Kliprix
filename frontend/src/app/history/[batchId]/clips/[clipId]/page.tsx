"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, Download, Loader2 } from "lucide-react";
import Link from "next/link";
import { use } from "react";
import { toast } from "sonner";

import { clipStatusText } from "@/components/batch/clip-card";
import { EditPanel } from "@/components/clip/edit-panel";
import { MetadataPanel } from "@/components/clip/metadata-panel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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

export default function ClipPage({ params }: PageProps<"/history/[batchId]/clips/[clipId]">) {
  const { batchId, clipId } = use(params);
  const clip = useClip(clipId);
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
    <div className="flex flex-col gap-6">
      <div>
        <Button variant="ghost" size="sm" asChild>
          <Link href={`/history/${batchId}`}>
            <ArrowLeft aria-hidden /> Back to batch
          </Link>
        </Button>
      </div>
      <div className="grid grid-cols-[minmax(0,360px)_minmax(0,1fr)] gap-8">
        <PlayerColumn clip={data} />
        <Tabs defaultValue="metadata">
          <TabsList>
            <TabsTrigger value="metadata">Metadata</TabsTrigger>
            <TabsTrigger value="edit">Edit</TabsTrigger>
          </TabsList>
          <TabsContent value="metadata" className="pt-4">
            <MetadataPanel clip={data} />
          </TabsContent>
          <TabsContent value="edit" className="pt-4">
            {/* Remount the draft when the saved clip changes (after apply or a render). */}
            <EditPanel key={data.updated_at} clip={data} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

function PlayerColumn({ clip }: { clip: Clip }) {
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
  const kind = clip.has_preview ? "preview" : clip.has_export ? "export" : null;
  const needsPreview = clip.edited && !clip.has_preview && !busy;

  return (
    <div className="flex flex-col gap-3">
      {kind ? (
        <video
          key={`${kind}-${clip.updated_at}`}
          src={clipMediaUrl(clip.id, kind, clip.updated_at)}
          controls
          playsInline
          className="bg-muted aspect-[9/16] w-full rounded-lg"
        />
      ) : (
        <div className="bg-muted text-muted-foreground flex aspect-[9/16] w-full items-center justify-center rounded-lg p-6 text-center text-sm">
          {busy ? "Rendering…" : "No rendered video yet. Make a preview to see your changes."}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={clip.status === "failed" ? "destructive" : "outline"}>
          {needsPreview ? "Edited — preview needed" : clipStatusText(clip)}
        </Badge>
        {kind && <span className="text-muted-foreground text-xs">Showing the {kind}</span>}
      </div>
      {clip.warning && <p className="text-muted-foreground text-xs">{clip.warning}</p>}
      {clip.error && clip.status === "failed" && (
        <p className="text-destructive text-sm">{clip.error.message}</p>
      )}
      <div className="flex flex-wrap gap-2">
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
          <Button asChild>
            <a href={clipDownloadUrl(clip.id)} download>
              <Download aria-hidden /> Download
            </a>
          </Button>
        ) : (
          <Button disabled>
            <Download aria-hidden /> Download
          </Button>
        )}
      </div>
    </div>
  );
}
