"use client";

import { ChevronLeft, ChevronRight, Scissors, Trash2, TriangleAlert } from "lucide-react";
import { useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { toast } from "sonner";

import { EditTimeline, type Selection } from "@/components/clip/edit-timeline";
import { CaptionStylePicker } from "@/components/style/caption-style-picker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { type BlurBox, type Clip, type Word } from "@/lib/api";
import {
  draftErrors,
  draftEnd,
  draftStart,
  lengthWarning,
  MAX_BLUR_BOXES,
  MIN_PART_S,
  movePartEdge,
  newBlurBox,
  normalized,
  playedLength,
  splitAt,
  timelineParts,
  withMontage,
  withoutPart,
  withTrim,
  type Part,
  TITLE_MAX,
  type EditDraft,
} from "@/lib/clip-edit";
import { hms, parseTime } from "@/lib/format";

/** The clip editor's right column (D-056): an inspector for whatever is selected, the
 * timeline under it, then Apply. Video-affecting edits are collected in the draft and saved
 * together with "Apply changes"; the clip then needs a new preview (edits never re-render
 * silently). The draft lives in the clip page, so blur boxes can also be moved on the video. */
export function EditPanel({
  clip,
  draft,
  setDraft,
  videoTime,
  time,
  onSeek,
  selection,
  onSelect,
}: {
  clip: Clip;
  draft: EditDraft;
  setDraft: Dispatch<SetStateAction<EditDraft>>;
  videoTime: () => number;
  /** The player's current time (clip seconds) and a way to jump there. */
  time: number;
  onSeek: (time: number) => void;
  selection: Selection;
  onSelect: (selection: Selection) => void;
}) {
  const errors = draftErrors(draft, clip);
  const canDraw = clip.has_preview || clip.has_export;
  const length = playedLength(draft);

  const setBoxes = (boxes: BlurBox[]) => setDraft((current) => ({ ...current, blur_boxes: boxes }));
  const setWords = (words: Word[]) => setDraft((current) => ({ ...current, words }));
  const removeWord = (index: number) => {
    setWords(draft.words.filter((_, i) => i !== index));
    onSelect(
      draft.words.length <= 1
        ? { kind: "captions" }
        : { kind: "word", index: Math.min(index, draft.words.length - 2) },
    );
  };
  const removeBox = (index: number) => {
    setBoxes(draft.blur_boxes.filter((_, i) => i !== index));
    onSelect({ kind: "blur", index: -1 });
  };
  const addBox = () => {
    const box = newBlurBox(videoTime(), length);
    setBoxes([...draft.blur_boxes, box]);
    onSelect({ kind: "blur", index: draft.blur_boxes.length });
  };
  // Cut tools (D-059): split the part under the playhead, delete a part.
  const split = () => {
    const result = splitAt(draft, videoTime());
    if (!result) {
      toast.error(`Each piece must stay at least ${MIN_PART_S} s long.`);
      return;
    }
    setDraft(result.draft);
    onSelect({ kind: "part", index: result.index });
  };
  const removePart = (index: number) => {
    if (draft.parts.length < 2) return;
    setDraft((current) => withoutPart(current, index));
    onSelect({ kind: "part", index: Math.max(0, index - 1) });
  };
  const setParts = (parts: Part[]) => setDraft((current) => ({ ...current, parts }));

  return (
    <div
      className="flex h-full min-h-0 flex-col gap-4"
      onKeyDown={(event) => {
        // S splits at the playhead, like video editors; not while typing.
        const target = event.target as HTMLElement;
        if (event.key.toLowerCase() !== "s" || event.ctrlKey || event.metaKey) return;
        if (target.closest("input, textarea, [contenteditable=true]")) return;
        event.preventDefault();
        split();
      }}
    >
      <section
        aria-label="Selected item"
        className="min-h-0 flex-1 overflow-y-auto rounded-md border p-4"
      >
        <Inspector
          clip={clip}
          draft={draft}
          setDraft={setDraft}
          length={length}
          videoTime={videoTime}
          selection={selection}
          onSelect={onSelect}
          onSeek={onSeek}
          onRemoveWord={removeWord}
          onRemoveBox={removeBox}
          onAddBox={addBox}
          canDraw={canDraw}
          onSplit={split}
          onRemovePart={removePart}
        />
      </section>

      <EditTimeline
        parts={timelineParts(draft)}
        onPartsChange={setParts}
        bounds={clip.source_bounds}
        onSplit={split}
        onRemovePart={removePart}
        title={draft.title}
        words={draft.words}
        boxes={draft.blur_boxes}
        length={length}
        time={time}
        onSeek={onSeek}
        selection={selection}
        onSelect={onSelect}
        onBoxesChange={setBoxes}
        onRemoveWord={removeWord}
        onRemoveBox={removeBox}
        onAddBox={addBox}
        canAddBox={canDraw && draft.blur_boxes.length < MAX_BLUR_BOXES}
      />

      {errors.length > 0 && (
        <ul className="text-destructive list-disc pl-5 text-sm" role="alert">
          {errors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Inspector({
  clip,
  draft,
  setDraft,
  length,
  videoTime,
  selection,
  onSelect,
  onSeek,
  onRemoveWord,
  onRemoveBox,
  onAddBox,
  canDraw,
  onSplit,
  onRemovePart,
}: {
  clip: Clip;
  draft: EditDraft;
  setDraft: Dispatch<SetStateAction<EditDraft>>;
  length: number;
  videoTime: () => number;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  onSeek: (time: number) => void;
  onRemoveWord: (index: number) => void;
  onRemoveBox: (index: number) => void;
  onAddBox: () => void;
  canDraw: boolean;
  onSplit: () => void;
  onRemovePart: (index: number) => void;
}) {
  const set = <K extends keyof EditDraft>(key: K, value: EditDraft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  switch (selection.kind) {
    case "video":
      return (
        <VideoInspector
          clip={clip}
          draft={draft}
          setDraft={setDraft}
          length={length}
          onSplit={onSplit}
        />
      );

    case "part": {
      const index = selection.index;
      const part = draft.parts[index];
      if (!part) {
        return (
          <VideoInspector
            clip={clip}
            draft={draft}
            setDraft={setDraft}
            length={length}
            onSplit={onSplit}
          />
        );
      }
      const update = (edge: "start" | "end", value: number) => {
        const delta = value - (edge === "start" ? part[0] : part[1]);
        setDraft((current) => ({
          ...current,
          parts: movePartEdge(current.parts, index, edge, delta, clip.source_bounds),
        }));
      };
      return (
        <Panel
          title={`Part ${index + 1} of ${draft.parts.length}`}
          subtitle={`Plays ${(part[1] - part[0]).toFixed(1)} s of the source. Drag its ends on the timeline to shorten or extend it.`}
          action={
            <Button
              variant="outline"
              size="sm"
              disabled={draft.parts.length < 2}
              title={draft.parts.length < 2 ? "Split the clip first (S)" : undefined}
              onClick={() => onRemovePart(index)}
            >
              <Trash2 aria-hidden /> Delete part
            </Button>
          }
        >
          <div className="flex flex-wrap items-end gap-4">
            <TimeField
              label="From"
              id={`part-${index}-start`}
              value={part[0]}
              onChange={(v) => update("start", v)}
            />
            <TimeField
              label="To"
              id={`part-${index}-end`}
              value={part[1]}
              onChange={(v) => update("end", v)}
            />
            <Button variant="outline" size="sm" className="mb-0.5" onClick={onSplit}>
              <Scissors aria-hidden /> Split at playhead
            </Button>
          </div>
          <p className="text-muted-foreground text-xs">
            Times are positions in the source video. Captions and blur boxes move to the new timing
            when you apply.
          </p>
        </Panel>
      );
    }

    case "title":
      return (
        <Panel title="On-video title" subtitle="Shown at the top of the video for the whole clip.">
          <div className="flex flex-col gap-1">
            <Label htmlFor="edit-title">Title</Label>
            <Input
              id="edit-title"
              value={draft.title}
              maxLength={TITLE_MAX}
              onChange={(event) => set("title", event.target.value)}
            />
            <span className="text-muted-foreground text-xs">
              {draft.title.length}/{TITLE_MAX} characters
            </span>
          </div>
        </Panel>
      );

    case "captions":
      return (
        <Panel
          title="Captions"
          subtitle={
            clip.words.length === 0
              ? "No speech was found in this clip, so it has no captions."
              : `${draft.words.length} words. Click a word on the timeline to change it.`
          }
        >
          <div className="flex flex-col gap-2">
            <Label>Caption style</Label>
            <CaptionStylePicker
              value={draft.caption_style}
              onChange={(value) => set("caption_style", value)}
            />
          </div>
        </Panel>
      );

    case "word": {
      const index = selection.index;
      const word = draft.words[index];
      if (!word) return <Empty>Click a word on the timeline to change it.</Empty>;
      const pick = (to: number) => {
        const target = draft.words[to];
        if (!target) return;
        onSelect({ kind: "word", index: to });
        onSeek(target.start);
      };
      return (
        <Panel
          title={`Caption word ${index + 1} of ${draft.words.length}`}
          subtitle={`Spoken ${word.start.toFixed(2)}–${word.end.toFixed(2)} s`}
        >
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              aria-label="Previous word"
              disabled={index === 0}
              onClick={() => pick(index - 1)}
            >
              <ChevronLeft aria-hidden />
            </Button>
            <Input
              aria-label={`Caption word ${index + 1}`}
              value={word.text}
              onChange={(event) =>
                set(
                  "words",
                  draft.words.map((w, i) => (i === index ? { ...w, text: event.target.value } : w)),
                )
              }
              onBlur={(event) => {
                // Cleared to nothing = removed; the Remove button removes it itself.
                const next = event.relatedTarget as HTMLElement | null;
                if (!word.text.trim() && next?.dataset.action !== "remove") onRemoveWord(index);
              }}
              className="h-9 w-48"
            />
            <Button
              variant="ghost"
              size="icon"
              aria-label="Next word"
              disabled={index >= draft.words.length - 1}
              onClick={() => pick(index + 1)}
            >
              <ChevronRight aria-hidden />
            </Button>
            <Button
              variant="outline"
              size="sm"
              data-action="remove"
              onClick={() => onRemoveWord(index)}
            >
              <Trash2 aria-hidden /> Remove word
            </Button>
          </div>
        </Panel>
      );
    }

    case "blur": {
      const index = selection.index;
      const box = draft.blur_boxes[index];
      if (!box) {
        return (
          <Panel
            title="Blur boxes"
            subtitle="Hide part of the video, such as a scene that is not fine for YouTube. Captions and the title stay on top."
          >
            <p className="text-muted-foreground text-sm">
              {canDraw
                ? `${draft.blur_boxes.length} of ${MAX_BLUR_BOXES} boxes. Play the video to the moment and add a box, or click a bar on the timeline.`
                : "Make a preview first to draw boxes on it."}
            </p>
            <div>
              <Button
                variant="outline"
                disabled={!canDraw || draft.blur_boxes.length >= MAX_BLUR_BOXES}
                onClick={onAddBox}
              >
                Add blur box at {videoTime().toFixed(1)} s
              </Button>
            </div>
          </Panel>
        );
      }
      const update = (change: Partial<BlurBox>) =>
        set(
          "blur_boxes",
          draft.blur_boxes.map((old, i) => (i === index ? { ...old, ...change } : old)),
        );
      const now = () => Math.round(videoTime() * 100) / 100;
      return (
        <Panel
          title={`Blur box ${index + 1}`}
          subtitle="Drag it on the video to move it, pull its corner to resize it."
          action={
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Remove blur box ${index + 1}`}
              onClick={() => onRemoveBox(index)}
            >
              <Trash2 aria-hidden />
            </Button>
          }
        >
          <div className="flex flex-wrap items-end gap-3">
            <NumberField
              label="From (s)"
              id={`box-${index}-start`}
              value={box.start}
              onChange={(v) => update({ start: v })}
            />
            <NumberField
              label="To (s)"
              id={`box-${index}-end`}
              value={box.end}
              onChange={(v) => update({ end: v })}
            />
            <div className="flex gap-1 pb-0.5">
              <Button variant="outline" size="sm" onClick={() => update({ start: now() })}>
                From = now
              </Button>
              <Button variant="outline" size="sm" onClick={() => update({ end: now() })}>
                To = now
              </Button>
            </div>
          </div>
          {length > 0 && box.end > length && (
            <p className="text-muted-foreground text-xs">
              The clip ends at {length.toFixed(1)} s; the box stops there.
            </p>
          )}
        </Panel>
      );
    }
  }
}

/** Trim, cut and montage, in source time (the Video track). */
function VideoInspector({
  clip,
  draft,
  setDraft,
  length,
  onSplit,
}: {
  clip: Clip;
  draft: EditDraft;
  setDraft: Dispatch<SetStateAction<EditDraft>>;
  length: number;
  onSplit: () => void;
}) {
  const { min_start_s: min, max_end_s: max } = clip.source_bounds;
  const start = draftStart(draft);
  const end = draftEnd(draft);
  const warning = lengthWarning(draft);
  const planned = clip.edit.parts as Part[];
  const hasPlanned = planned.length > 1;
  const montage = normalized(draft.parts).length > 1;
  const skipped = end - start - length;
  const trim = (from: number, to: number) => setDraft((current) => withTrim(current, from, to));

  return (
    <Panel
      title="Video"
      subtitle="Trim the clip, or split it at the playhead (S) and delete a part. Times are positions in the source video."
      action={
        <Button variant="outline" size="sm" onClick={onSplit}>
          <Scissors aria-hidden /> Split at playhead
        </Button>
      }
    >
      <Slider
        min={min}
        max={max}
        step={0.1}
        minStepsBetweenThumbs={50}
        value={[start, end]}
        onValueChange={([from, to]) => trim(from ?? start, to ?? end)}
        aria-label="Trim range"
      />
      <div className="flex flex-wrap items-end gap-4">
        <TimeField label="Start" id="trim-start" value={start} onChange={(v) => trim(v, end)} />
        <TimeField label="End" id="trim-end" value={end} onChange={(v) => trim(start, v)} />
        <span className="text-muted-foreground pb-2 text-sm">
          Length <span className="font-mono">{length.toFixed(1)} s</span>
          {montage && ` · ${normalized(draft.parts).length} parts, ${skipped.toFixed(1)} s cut`}
        </span>
      </div>
      <p className="text-muted-foreground text-xs">
        Available range{" "}
        <span className="font-mono">
          {hms(min)}–{hms(max)}
        </span>
        {max - min < 120
          ? " (the source video was deleted; trimming is limited to the saved handles)"
          : ""}
        .
      </p>
      {warning && (
        <p className="text-foreground flex items-center gap-2 text-sm">
          <TriangleAlert className="size-4" aria-hidden /> {warning}
        </p>
      )}
      {hasPlanned && (
        <div className="flex flex-col gap-2 rounded-md border p-3">
          <div className="flex items-center gap-3">
            <Switch
              id="montage"
              checked={montage}
              onCheckedChange={(checked) =>
                setDraft((current) => withMontage(current, checked, planned))
              }
            />
            <Label htmlFor="montage">Play only the parts (skip the gaps)</Label>
          </div>
          <p className="text-muted-foreground text-xs">
            {montage
              ? `Plays ${normalized(draft.parts).length} parts and skips ${skipped.toFixed(0)} s: ` +
                normalized(draft.parts)
                  .map(([a, b]) => `${hms(a)}–${hms(b)}`)
                  .join(", ") +
                "."
              : "Plays the whole range without skipping."}
          </p>
        </div>
      )}
    </Panel>
  );
}

function Panel({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <h3 className="font-medium">{title}</h3>
          {subtitle && <p className="text-muted-foreground text-sm">{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </div>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-muted-foreground text-sm">{children}</p>;
}

/** A source position typed as hh:mm:ss (also mm:ss or seconds); applied on Enter or when
 * the field loses focus, so typing is not interrupted (D-055). */
function TimeField({
  label,
  id,
  value,
  onChange,
}: {
  label: string;
  id: string;
  value: number;
  onChange: (value: number) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const invalid = text !== null && parseTime(text) === null;
  const commit = () => {
    if (text === null) return;
    const parsed = parseTime(text);
    if (parsed !== null) onChange(parsed);
    setText(null);
  };
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        inputMode="numeric"
        placeholder="hh:mm:ss"
        aria-invalid={invalid}
        className="w-28 font-mono"
        value={text ?? (Number.isFinite(value) ? hms(value) : "")}
        onChange={(event) => setText(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") commit();
          if (event.key === "Escape") setText(null);
        }}
      />
    </div>
  );
}

function NumberField({
  label,
  id,
  value,
  onChange,
}: {
  label: string;
  id: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type="number"
        step={0.1}
        className="w-28 font-mono"
        value={Number.isFinite(value) ? Number(value.toFixed(2)) : ""}
        onChange={(event) => onChange(event.target.valueAsNumber)}
      />
    </div>
  );
}
