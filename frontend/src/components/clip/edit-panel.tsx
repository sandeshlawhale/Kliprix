"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Trash2, TriangleAlert } from "lucide-react";
import type { Dispatch, SetStateAction } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { CaptionStylePicker } from "@/components/style/caption-style-picker";
import { invalidateBatch, keys } from "@/hooks/queries";
import { errorMessage, patchClip, type Clip } from "@/lib/api";
import {
  draftErrors,
  draftFrom,
  isDirty,
  lengthWarning,
  MAX_BLUR_BOXES,
  newBlurBox,
  patchFrom,
  playedLength,
  playedParts,
  wordLines,
  type EditDraft,
} from "@/lib/clip-edit";
import { clock } from "@/lib/format";

/** Video-affecting edits are collected here and saved together with "Apply changes"; the
 * clip is then marked edited and needs a new preview (edits never re-render silently).
 * The draft lives in the clip page, so blur boxes can also be moved on the video. */
export function EditPanel({
  clip,
  draft,
  setDraft,
  videoTime,
  selectedBox,
  onSelectBox,
}: {
  clip: Clip;
  draft: EditDraft;
  setDraft: Dispatch<SetStateAction<EditDraft>>;
  videoTime: () => number;
  selectedBox: number | null;
  onSelectBox: (index: number | null) => void;
}) {
  const client = useQueryClient();
  const errors = draftErrors(draft, clip);
  const warning = lengthWarning(draft, clip.edit.parts);
  const dirty = isDirty(draft, clip);
  const busy = clip.status === "running" || clip.status === "queued";
  const save = useMutation({
    mutationFn: () => patchClip(clip.id, patchFrom(draft, clip)),
    onSuccess: (updated) => {
      client.setQueryData(keys.clip(clip.id), updated);
      invalidateBatch(client, clip.batch_id);
      setDraft(draftFrom(updated));
      toast.success("Changes applied. Make a preview to see them.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const set = <K extends keyof EditDraft>(key: K, value: EditDraft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const { min_start_s: min, max_end_s: max } = clip.source_bounds;
  const length = playedLength(draft, clip.edit.parts);
  const hasParts = clip.edit.parts.length > 1;
  const played = playedParts(draft, clip.edit.parts);
  const skipped = draft.end_s - draft.start_s - length;

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h3 className="font-medium">Trim</h3>
        <Slider
          min={min}
          max={max}
          step={0.1}
          minStepsBetweenThumbs={50}
          value={[draft.start_s, draft.end_s]}
          onValueChange={([start, end]) =>
            setDraft((current) => ({
              ...current,
              start_s: start ?? current.start_s,
              end_s: end ?? current.end_s,
            }))
          }
          aria-label="Trim range"
        />
        <div className="flex flex-wrap items-end gap-4">
          <NumberField
            label="Start (s)"
            id="trim-start"
            value={draft.start_s}
            onChange={(v) => set("start_s", v)}
          />
          <NumberField
            label="End (s)"
            id="trim-end"
            value={draft.end_s}
            onChange={(v) => set("end_s", v)}
          />
          <span className="text-muted-foreground pb-2 text-sm">
            Length <span className="font-mono">{length.toFixed(1)} s</span> · source{" "}
            {clock(draft.start_s)}–{clock(draft.end_s)}
          </span>
        </div>
        <p className="text-muted-foreground text-xs">
          Available range {min.toFixed(1)}–{max.toFixed(1)} s
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
        {hasParts && (
          <div className="flex flex-col gap-2 rounded-md border p-3">
            <div className="flex items-center gap-3">
              <Switch
                id="montage"
                checked={draft.montage}
                onCheckedChange={(checked) => set("montage", checked)}
              />
              <Label htmlFor="montage">Skip the quiet gaps (montage)</Label>
            </div>
            <p className="text-muted-foreground text-xs">
              {draft.montage && played.length > 1
                ? `Plays ${played.length} parts and skips ${skipped.toFixed(0)} s where nothing is said or happens: ` +
                  played.map(([start, end]) => `${clock(start)}–${clock(end)}`).join(", ") +
                  "."
                : "Plays the whole range without skipping."}
            </p>
          </div>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="font-medium">Caption style</h3>
        <CaptionStylePicker
          value={draft.caption_style}
          onChange={(value) => set("caption_style", value)}
        />
      </section>

      <section className="flex flex-col gap-3">
        <h3 className="font-medium">Caption text</h3>
        {draft.words.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No speech was found in this clip, so it has no captions.
          </p>
        ) : (
          <div className="flex max-h-72 flex-col gap-2 overflow-y-auto rounded-md border p-2">
            {wordLines(draft.words).map((line) => (
              <div key={line[0]} className="flex flex-wrap items-center gap-1">
                <span className="text-muted-foreground w-12 font-mono text-xs">
                  {draft.words[line[0] ?? 0]?.start.toFixed(1)}s
                </span>
                {line.map((index) => (
                  <Input
                    key={index}
                    aria-label={`Caption word ${index + 1}`}
                    value={draft.words[index]?.text ?? ""}
                    onChange={(event) =>
                      set(
                        "words",
                        draft.words.map((word, i) =>
                          i === index ? { ...word, text: event.target.value } : word,
                        ),
                      )
                    }
                    className="h-7 w-auto min-w-0 px-1.5 text-sm"
                    style={{
                      width: `${Math.max(3, (draft.words[index]?.text.length ?? 0) + 2)}ch`,
                    }}
                  />
                ))}
              </div>
            ))}
          </div>
        )}
      </section>

      <BlurBoxes
        draft={draft}
        setDraft={setDraft}
        length={length}
        videoTime={videoTime}
        selected={selectedBox}
        onSelect={onSelectBox}
        canDraw={clip.has_preview || clip.has_export}
      />

      {errors.length > 0 && (
        <ul className="text-destructive list-disc pl-5 text-sm" role="alert">
          {errors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      )}
      <div className="bg-background/90 sticky bottom-0 -mx-1 flex items-center gap-3 border-t px-1 py-3 backdrop-blur">
        <Button
          disabled={!dirty || errors.length > 0 || save.isPending || busy}
          onClick={() => save.mutate()}
        >
          {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
          Apply changes
        </Button>
        <Button variant="ghost" disabled={!dirty} onClick={() => setDraft(draftFrom(clip))}>
          Discard
        </Button>
        {dirty && <span className="text-muted-foreground text-sm">Unsaved changes</span>}
        {busy && (
          <span className="text-muted-foreground text-sm">
            Wait for the current render to finish.
          </span>
        )}
      </div>
    </div>
  );
}

/** Manual blur boxes (D-048): rectangles of the vertical clip blurred for a time range. */
function BlurBoxes({
  draft,
  setDraft,
  length,
  videoTime,
  selected,
  onSelect,
  canDraw,
}: {
  draft: EditDraft;
  setDraft: Dispatch<SetStateAction<EditDraft>>;
  length: number;
  videoTime: () => number;
  selected: number | null;
  onSelect: (index: number | null) => void;
  canDraw: boolean;
}) {
  const boxes = draft.blur_boxes;
  const update = (index: number, change: Partial<EditDraft["blur_boxes"][number]>) =>
    setDraft((current) => ({
      ...current,
      blur_boxes: current.blur_boxes.map((box, i) => (i === index ? { ...box, ...change } : box)),
    }));
  const now = () => Math.round(videoTime() * 100) / 100;

  return (
    <section className="flex flex-col gap-3">
      <h3 className="font-medium">Blur boxes</h3>
      <p className="text-muted-foreground text-sm">
        Hide part of the video, such as a scene that is not fine for YouTube. Play the video to the
        moment, add a box, then drag it on the video and pull its corner to resize. Captions and the
        title stay on top.
      </p>
      {!canDraw && (
        <p className="text-muted-foreground text-sm">Make a preview first to draw boxes on it.</p>
      )}
      {boxes.map((box, index) => (
        <div
          // Boxes have no stable id; the index is their identity in the draft.
          key={index}
          className={
            "flex flex-wrap items-end gap-3 rounded-md border p-2 " +
            (index === selected ? "border-brand" : "")
          }
        >
          <Button
            variant={index === selected ? "secondary" : "ghost"}
            size="sm"
            onClick={() => onSelect(index)}
            aria-pressed={index === selected}
          >
            Box {index + 1}
          </Button>
          <NumberField
            label="From (s)"
            id={`box-${index}-start`}
            value={box.start}
            onChange={(v) => update(index, { start: v })}
          />
          <NumberField
            label="To (s)"
            id={`box-${index}-end`}
            value={box.end}
            onChange={(v) => update(index, { end: v })}
          />
          <div className="flex gap-1 pb-0.5">
            <Button variant="outline" size="sm" onClick={() => update(index, { start: now() })}>
              From = now
            </Button>
            <Button variant="outline" size="sm" onClick={() => update(index, { end: now() })}>
              To = now
            </Button>
          </div>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Remove blur box ${index + 1}`}
            onClick={() => {
              setDraft((current) => ({
                ...current,
                blur_boxes: current.blur_boxes.filter((_, i) => i !== index),
              }));
              onSelect(null);
            }}
          >
            <Trash2 aria-hidden />
          </Button>
        </div>
      ))}
      <div>
        <Button
          variant="outline"
          disabled={!canDraw || boxes.length >= MAX_BLUR_BOXES}
          onClick={() => {
            const box = newBlurBox(videoTime(), length);
            setDraft((current) => ({ ...current, blur_boxes: [...current.blur_boxes, box] }));
            onSelect(boxes.length);
          }}
        >
          <Plus aria-hidden /> Add blur box at {now().toFixed(1)} s
        </Button>
      </div>
    </section>
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
