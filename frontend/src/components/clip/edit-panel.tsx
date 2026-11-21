"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { CaptionStylePicker } from "@/components/style/caption-style-picker";
import { invalidateBatch, keys } from "@/hooks/queries";
import { errorMessage, patchClip, type Clip } from "@/lib/api";
import {
  draftErrors,
  draftFrom,
  isDirty,
  lengthWarning,
  patchFrom,
  wordLines,
  type EditDraft,
} from "@/lib/clip-edit";
import { clock } from "@/lib/format";

/** Video-affecting edits are collected here and saved together with "Apply changes"; the
 * clip is then marked edited and needs a new preview (edits never re-render silently). */
export function EditPanel({ clip }: { clip: Clip }) {
  const client = useQueryClient();
  const [draft, setDraft] = useState<EditDraft>(() => draftFrom(clip));
  const errors = draftErrors(draft, clip);
  const warning = lengthWarning(draft);
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
  const length = draft.end_s - draft.start_s;

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
