"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, Download, FileText, Loader2, Pencil } from "lucide-react";
import Link from "next/link";
import {
  Fragment,
  use,
  useEffect,
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
import { Hint } from "@/components/clip/hint";
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
  savedParts,
  wordsOn,
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
  const [download, setDownload] = useState(false);
  useBatchEvents(batchId, [clipId]);
  const ready = clip.data?.has_export ?? false;
  const failed = clip.data?.status === "failed";
  const loadedId = clip.data?.id;
  // A download waits for its final render, then starts by itself (D-058).
  useEffect(() => {
    if (!download || !loadedId || !(ready || failed)) return;
    // Outside the effect body: the state update and the click happen once, after render.
    const timer = window.setTimeout(() => {
      setDownload(false);
      if (ready) triggerDownload(loadedId);
      else toast.error("The final render failed, so there is nothing to download.");
    }, 0);
    return () => window.clearTimeout(timer);
  }, [download, ready, failed, loadedId]);

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
      // A fresh draft only when the saved edits change (Apply, Preview, Download), not on every
      // render progress update, so editing while a preview renders keeps the draft.
      key={JSON.stringify(draftFrom(data))}
      clip={data}
      batchId={batchId}
      view={view}
      setView={setView}
      selection={selection}
      setSelection={setSelection}
      download={download}
      onDownload={() => setDownload(true)}
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
  download,
  onDownload,
}: {
  clip: Clip;
  batchId: string;
  view: View;
  setView: (view: View) => void;
  selection: Selection;
  setSelection: Dispatch<SetStateAction<Selection>>;
  download: boolean;
  onDownload: () => void;
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
        setDraft={setDraft}
        batchId={batchId}
        view={view}
        onToggleView={() => setView(view === "edit" ? "metadata" : "edit")}
        download={download}
        onDownload={onDownload}
      />
      <div className="grid min-h-0 flex-1 grid-cols-[auto_minmax(0,1fr)] gap-5">
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
                    // The base plays the saved timing until the next preview (D-057).
                    words={wordsOn(draft.words, savedParts(clip))}
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
      <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
        Live editing off
        <Hint label="About live editing">
          Click Preview once: the title, captions and blur boxes then change on the video as you
          edit.
        </Hint>
      </span>
    );
  }
  const stale = layoutChanged(draft, clip);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1" role="group" aria-label="Player shows">
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
        <Hint label="About the live view">
          Live shows your edits at once. Cuts and trims, and the stars and beeps on bad words, show
          in the rendered preview.
        </Hint>
      </div>
      {screen === "live" && stale && (
        <span className="text-xs text-amber-600 dark:text-amber-400">Cuts show after Preview</span>
      )}
    </div>
  );
}

function Toolbar({
  clip,
  draft,
  setDraft,
  batchId,
  view,
  onToggleView,
  download,
  onDownload,
}: {
  clip: Clip;
  draft: EditDraft;
  setDraft: Dispatch<SetStateAction<EditDraft>>;
  batchId: string;
  view: View;
  onToggleView: () => void;
  /** A download waits for its final render (kept above the editor, which remounts). */
  download: boolean;
  onDownload: () => void;
}) {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: keys.clip(clip.id) });
    invalidateBatch(client, clip.batch_id);
  };
  // Preview and Download use what is on screen: unsaved edits are saved first (D-057).
  const save = async () => {
    const errors = draftErrors(draft, clip);
    if (errors.length > 0) throw new Error(errors[0]);
    if (isDirty(draft, clip)) {
      const updated = await patchClip(clip.id, patchFrom(draft, clip));
      client.setQueryData(keys.clip(clip.id), updated);
      return updated;
    }
    return clip;
  };
  const apply = useMutation({
    mutationFn: save,
    onSuccess: () => {
      refresh();
      toast.success("Changes applied.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const preview = useMutation({
    mutationFn: async () => {
      await save();
      return previewClip(clip.id);
    },
    onSuccess: refresh,
    onError: (error) => toast.error(errorMessage(error)),
  });
  // Download (D-058): the final file when it is current, else render it first and download
  // when it is ready.
  const prepare = useMutation({
    mutationFn: async () => {
      const saved = await save();
      if (!saved.has_export) await exportClip(clip.id);
      return saved.has_export;
    },
    onSuccess: (ready) => {
      refresh();
      if (ready) triggerDownload(clip.id);
      else onDownload();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const busy = clip.status === "queued" || clip.status === "running";
  const dirty = isDirty(draft, clip);
  const errors = draftErrors(draft, clip);
  const editing = view === "edit";

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <Button variant="ghost" size="sm" asChild className="-ml-2.5">
        <Link href={`/history/${batchId}`}>
          <ArrowLeft aria-hidden /> Back to batch
        </Link>
      </Button>
      <div className="flex flex-wrap items-center gap-2">
        {editing && dirty && (
          <>
            <span className="text-muted-foreground text-sm">Unsaved changes</span>
            <Button variant="ghost" onClick={() => setDraft(draftFrom(clip))}>
              Discard
            </Button>
            <Button
              variant="outline"
              disabled={errors.length > 0 || apply.isPending || busy}
              title={busy ? "Wait for the current render to finish." : errors[0]}
              onClick={() => apply.mutate()}
            >
              {apply.isPending && <Loader2 className="animate-spin" aria-hidden />}
              Apply changes
            </Button>
          </>
        )}
        <Button variant="outline" onClick={onToggleView} aria-pressed={editing}>
          {editing ? (
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
          variant="brand"
          disabled={download || prepare.isPending || (busy && !clip.has_export)}
          onClick={() => prepare.mutate()}
          title={
            dirty || !clip.has_export
              ? "Saves your changes, renders the final video, then downloads it"
              : undefined
          }
        >
          {download || prepare.isPending ? (
            <Loader2 className="animate-spin" aria-hidden />
          ) : (
            <Download aria-hidden />
          )}
          {download ? "Preparing download…" : "Download"}
        </Button>
      </div>
    </div>
  );
}

/** Start the browser download of the clip's final video. */
function triggerDownload(clipId: string) {
  const link = document.createElement("a");
  link.href = clipDownloadUrl(clipId);
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
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
      <div className="min-h-0 flex-1">
        {/* The box is exactly 9:16 like the clip (D-064): its height is the free height, capped
            so the width stays at most min(300px, 26vw); the empty 9:16 SVG gives the width, so
            the player column is as wide as the video and no gray bands show around it. */}
        <div className="relative h-full max-h-[calc(min(300px,26vw)*16/9)] w-fit max-w-[min(300px,26vw)]">
          <svg
            viewBox="0 0 9 16"
            width={1080}
            height={1920}
            className="block h-full w-auto"
            aria-hidden
          />
          {kind ? (
            <video
              ref={video}
              key={`${kind}-${version}`}
              onTimeUpdate={(event) => onTime(event.currentTarget.currentTime)}
              onSeeked={(event) => onTime(event.currentTarget.currentTime)}
              src={clipMediaUrl(clip.id, kind, version)}
              controls
              playsInline
              className="bg-muted absolute inset-0 h-full w-full rounded-xl object-contain"
            />
          ) : (
            <div className="bg-muted text-muted-foreground absolute inset-0 flex items-center justify-center rounded-xl p-6 text-center text-sm">
              {busy ? "Rendering…" : "No rendered video yet. Make a preview to see your changes."}
            </div>
          )}
          {/* Overlays measure the <video>, so they remount with it when the source changes. */}
          {kind && <Fragment key={`overlay-${kind}-${version}`}>{overlay}</Fragment>}
        </div>
      </div>
      {/* w-0 + min-w-full: the text below wraps to the video's width instead of widening it. */}
      <div className="flex w-0 min-w-full flex-col gap-2">
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
        {clip.warning && <p className="text-muted-foreground text-xs">{clip.warning}</p>}
        {clip.error && clip.status === "failed" && (
          <p className="text-destructive text-sm">{clip.error.message}</p>
        )}
      </div>
    </div>
  );
}
