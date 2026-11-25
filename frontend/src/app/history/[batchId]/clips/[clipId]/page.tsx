"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, Download, FileText, Loader2, Pencil } from "lucide-react";
import Link from "next/link";
import {
  Fragment,
  use,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type RefObject,
  type SetStateAction,
} from "react";
import { toast } from "sonner";

import { clipStatusText } from "@/components/batch/clip-card";
import { BlurBoxOverlay } from "@/components/clip/blur-box-overlay";
import { EditPanel } from "@/components/clip/edit-panel";
import type { Selection } from "@/components/clip/edit-timeline";
import { LiveOverlay } from "@/components/clip/live-overlay";
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
  patchClip,
  previewClip,
  type Clip,
} from "@/lib/api";
import {
  draftErrors,
  draftFrom,
  isDirty,
  layoutChanged,
  patchFrom,
  type EditDraft,
} from "@/lib/clip-edit";

type View = "metadata" | "edit";
/** Edit view (D-057): the base video with the draft drawn live, or the rendered preview. */
type Screen = "live" | "rendered";

export default function ClipPage({ params }: PageProps<"/history/[batchId]/clips/[clipId]">) {
  const { batchId, clipId } = use(params);
  const clip = useClip(clipId);
  const [view, setView] = useState<View>("metadata");
  const [selection, setSelection] = useState<Selection>({ kind: "video" });
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
    <Editor
      // A fresh draft only when the saved edits change (Apply, Preview, Export), not on every
      // render progress update, so editing while a preview renders keeps the draft.
      key={JSON.stringify(draftFrom(data))}
      clip={data}
      batchId={batchId}
      view={view}
      setView={setView}
      selection={selection}
      setSelection={setSelection}
    />
  );
}

/** Toolbar, player and side panel share the edit draft: blur boxes are drawn on the video,
 * the live view shows the draft, and Preview/Export save it first (D-057). Edit view
 * (D-056): the player alone on the left; the right column is the inspector, the timeline
 * and Apply. Both select the same item (a blur box on the video is a bar below). */
function Editor({
  clip,
  batchId,
  view,
  setView,
  selection,
  setSelection,
}: {
  clip: Clip;
  batchId: string;
  view: View;
  setView: (view: View) => void;
  selection: Selection;
  setSelection: Dispatch<SetStateAction<Selection>>;
}) {
  const [draft, setDraft] = useState<EditDraft>(() => draftFrom(clip));
  const [time, setTime] = useState(0);
  const [screen, setScreen] = useState<Screen>("live");
  const video = useRef<HTMLVideoElement>(null);
  const editing = view === "edit";
  const live = editing && screen === "live" ? (clip.live ?? null) : null;
  const selectedBox = selection.kind === "blur" && selection.index >= 0 ? selection.index : null;
  const seek = (to: number) => {
    if (video.current) video.current.currentTime = to;
    setTime(to);
  };

  return (
    // Fits the window (main has 2.5rem padding top and bottom); only the side panel scrolls.
    <div className="flex h-[calc(100svh-8.5rem)] min-h-[520px] flex-col gap-5">
      <Toolbar
        clip={clip}
        draft={draft}
        batchId={batchId}
        view={view}
        onToggleView={() => setView(view === "edit" ? "metadata" : "edit")}
      />
      <div className="grid min-h-0 flex-1 grid-cols-[auto_minmax(0,1fr)] gap-8">
        <Player
          clip={clip}
          video={video}
          live={live !== null}
          onTime={setTime}
          overlay={
            editing ? (
              <>
                {live && (
                  <LiveOverlay
                    video={video}
                    live={live}
                    title={draft.title}
                    words={draft.words}
                    style={draft.caption_style}
                    time={time}
                    duration={clip.duration_s}
                  />
                )}
                <BlurBoxOverlay
                  video={video}
                  boxes={draft.blur_boxes}
                  time={time}
                  selected={selectedBox}
                  onSelect={(index) => setSelection({ kind: "blur", index })}
                  onChange={(index, box) =>
                    setDraft((current) => ({
                      ...current,
                      blur_boxes: current.blur_boxes.map((old, i) => (i === index ? box : old)),
                    }))
                  }
                />
              </>
            ) : null
          }
          footer={
            editing ? (
              <LiveSwitch clip={clip} draft={draft} screen={screen} onScreen={setScreen} />
            ) : null
          }
        />
        {editing ? (
          <section aria-label="Edit" className="min-h-0">
            <EditPanel
              clip={clip}
              draft={draft}
              setDraft={setDraft}
              videoTime={() => video.current?.currentTime ?? time}
              time={time}
              onSeek={seek}
              selection={selection}
              onSelect={setSelection}
            />
          </section>
        ) : (
          <section aria-label="Metadata" className="min-h-0 overflow-y-auto pr-2">
            <MetadataPanel clip={clip} />
          </section>
        )}
      </div>
    </div>
  );
}

/** Live view or rendered preview, and what the live view cannot show yet. */
function LiveSwitch({
  clip,
  draft,
  screen,
  onScreen,
}: {
  clip: Clip;
  draft: EditDraft;
  screen: Screen;
  onScreen: (screen: Screen) => void;
}) {
  if (!clip.live) {
    return (
      <p className="text-muted-foreground max-w-80 text-xs">
        Click Preview once to edit live: the title, captions and blur boxes then change on the video
        as you edit.
      </p>
    );
  }
  return (
    <div className="flex max-w-80 flex-col gap-1.5">
      <div className="flex gap-1" role="group" aria-label="Player shows">
        <Button
          size="sm"
          variant={screen === "live" ? "secondary" : "ghost"}
          aria-pressed={screen === "live"}
          onClick={() => onScreen("live")}
        >
          Live
        </Button>
        <Button
          size="sm"
          variant={screen === "rendered" ? "secondary" : "ghost"}
          aria-pressed={screen === "rendered"}
          disabled={!clip.has_preview && !clip.has_export}
          onClick={() => onScreen("rendered")}
        >
          Rendered
        </Button>
      </div>
      {screen === "live" && (
        <p className="text-muted-foreground text-xs">
          {layoutChanged(draft, clip)
            ? `Trim and montage changes show after Preview; the live view plays the saved ${clip.duration_s.toFixed(1)} s.`
            : "Edits show at once. Bad words get their stars and beeps in the rendered preview."}
        </p>
      )}
    </div>
  );
}

function Toolbar({
  clip,
  draft,
  batchId,
  view,
  onToggleView,
}: {
  clip: Clip;
  draft: EditDraft;
  batchId: string;
  view: View;
  onToggleView: () => void;
}) {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: keys.clip(clip.id) });
    invalidateBatch(client, clip.batch_id);
  };
  // Preview and Export use what is on screen: unsaved edits are saved first (D-057).
  const saveThen = async (start: (id: string) => Promise<unknown>) => {
    const errors = draftErrors(draft, clip);
    if (errors.length > 0) throw new Error(errors[0]);
    if (isDirty(draft, clip)) {
      const updated = await patchClip(clip.id, patchFrom(draft, clip));
      client.setQueryData(keys.clip(clip.id), updated);
    }
    return start(clip.id);
  };
  const preview = useMutation({
    mutationFn: () => saveThen(previewClip),
    onSuccess: refresh,
    onError: (error) => toast.error(errorMessage(error)),
  });
  const render = useMutation({
    mutationFn: () => saveThen(exportClip),
    onSuccess: refresh,
    onError: (error) => toast.error(errorMessage(error)),
  });
  const busy = clip.status === "queued" || clip.status === "running";
  const dirty = isDirty(draft, clip);

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
          title={dirty ? "Saves your changes, then makes a preview" : undefined}
        >
          {preview.isPending && <Loader2 className="animate-spin" aria-hidden />}
          Preview
        </Button>
        <Button
          variant="outline"
          disabled={busy || render.isPending}
          onClick={() => render.mutate()}
          title={dirty ? "Saves your changes, then exports" : undefined}
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
  live,
  onTime,
  overlay,
  footer,
}: {
  clip: Clip;
  video: RefObject<HTMLVideoElement | null>;
  /** Play the live-preview base (D-057) instead of the rendered video. */
  live: boolean;
  onTime: (time: number) => void;
  overlay: ReactNode;
  footer: ReactNode;
}) {
  const busy = clip.status === "queued" || clip.status === "running";
  const kind = live ? "base" : clip.has_preview ? "preview" : clip.has_export ? "export" : null;
  // The base only changes with a new preview render; other updates (render progress) must
  // not reload it while you edit.
  const version = kind === "base" ? (clip.live?.version ?? "") : clip.updated_at;
  const needsPreview = clip.edited && !clip.has_preview && !busy;

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div className="relative min-h-0 flex-1">
        {kind ? (
          <video
            ref={video}
            key={`${kind}-${version}`}
            onTimeUpdate={(event) => onTime(event.currentTarget.currentTime)}
            onSeeked={(event) => onTime(event.currentTarget.currentTime)}
            src={clipMediaUrl(clip.id, kind, version)}
            controls
            playsInline
            className="bg-muted aspect-[9/16] h-full max-w-[min(420px,40vw)] rounded-xl object-contain"
          />
        ) : (
          // A plain box gets its width from its height only after the grid sized the column,
          // so it spilled over the side panel. An empty SVG with the video's own 1080x1920 size
          // sizes the column exactly like the <video>.
          <div className="relative h-full">
            <svg
              viewBox="0 0 9 16"
              width={1080}
              height={1920}
              className="block h-full w-auto max-w-[min(420px,40vw)]"
              aria-hidden
            />
            <div className="bg-muted text-muted-foreground absolute inset-0 flex items-center justify-center rounded-xl p-6 text-center text-sm">
              {busy ? "Rendering…" : "No rendered video yet. Make a preview to see your changes."}
            </div>
          </div>
        )}
        {/* Overlays measure the <video>, so they remount with it when the source changes. */}
        {kind && <Fragment key={`overlay-${kind}-${version}`}>{overlay}</Fragment>}
      </div>
      {clip.status === "running" && (
        <Progress value={clip.progress * 100} aria-label="Render progress" />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={clip.status === "failed" ? "destructive" : "outline"}>
          {needsPreview ? "Edited — preview needed" : clipStatusText(clip)}
        </Badge>
        {kind && (
          <span className="text-muted-foreground text-xs">
            {kind === "base" ? "Live view" : `Showing the ${kind}`}
          </span>
        )}
      </div>
      {footer}
      {clip.warning && <p className="text-muted-foreground max-w-80 text-xs">{clip.warning}</p>}
      {clip.error && clip.status === "failed" && (
        <p className="text-destructive max-w-80 text-sm">{clip.error.message}</p>
      )}
    </div>
  );
}
