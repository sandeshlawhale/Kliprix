"use client";

import { Loader2 } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useSettings, useUpdateSettings } from "@/hooks/queries";
import { errorMessage, type StyleOverrides } from "@/lib/api";
import { CROP_LABELS, LANGUAGE_LABELS, STYLE_LABELS } from "@/lib/format";
import { cn } from "@/lib/utils";

const STYLE_HELP = {
  pop: "1–3 words at a time, popping in; the spoken word turns yellow.",
  karaoke: "A short phrase stays on screen; the spoken word lights up.",
  boxed: "A short phrase; the spoken word sits on a colored box.",
} as const;

export default function StylePage() {
  const settings = useSettings();
  const update = useUpdateSettings();
  const [draft, setDraft] = useState<StyleOverrides | null>(null);
  const saved = settings.data?.style_defaults;
  const style = draft ?? saved;

  if (settings.isPending) return <Skeleton className="h-96 w-full max-w-3xl" />;
  if (settings.isError || !style)
    return (
      <p role="alert" className="text-destructive">
        {errorMessage(settings.error)}
      </p>
    );

  const set = <K extends keyof StyleOverrides>(key: K, value: StyleOverrides[K]) =>
    setDraft({ ...style, [key]: value });
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(saved);

  return (
    <div className="flex max-w-3xl flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Edit video style</h1>
        <p className="text-muted-foreground">
          Defaults for new batches. Each clip can still be changed later.
        </p>
      </header>

      <section className="flex flex-col gap-3">
        <h2 className="font-medium">Caption style</h2>
        <div className="grid grid-cols-3 gap-4" role="radiogroup" aria-label="Caption style">
          {(Object.keys(STYLE_LABELS) as (keyof typeof STYLE_LABELS)[]).map((name) => (
            <button
              key={name}
              type="button"
              role="radio"
              aria-checked={style.caption_style === name}
              onClick={() => set("caption_style", name)}
              className={cn(
                "flex flex-col gap-2 rounded-lg border p-2 text-left outline-none focus-visible:ring-[3px]",
                "focus-visible:ring-ring/50",
                style.caption_style === name
                  ? "border-primary ring-primary ring-1"
                  : "hover:bg-muted/50",
              )}
            >
              <Image
                src={`/style-samples/${name}.png`}
                alt={`${STYLE_LABELS[name]} captions rendered by Kliprix on a test pattern`}
                width={540}
                height={960}
                unoptimized
                className="aspect-[9/16] w-full rounded-md object-cover"
              />
              <span className="font-medium">{STYLE_LABELS[name]}</span>
              <span className="text-muted-foreground text-xs">{STYLE_HELP[name]}</span>
            </button>
          ))}
        </div>
      </section>

      <Field label="Caption language" id="language">
        <ToggleGroup
          id="language"
          type="single"
          variant="outline"
          value={style.caption_language}
          onValueChange={(value) =>
            value && set("caption_language", value as StyleOverrides["caption_language"])
          }
        >
          {(Object.keys(LANGUAGE_LABELS) as (keyof typeof LANGUAGE_LABELS)[]).map((key) => (
            <ToggleGroupItem key={key} value={key}>
              {LANGUAGE_LABELS[key]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </Field>

      <Field label="Clip length" id="length">
        <ToggleGroup
          id="length"
          type="single"
          variant="outline"
          value={style.clip_length_mode}
          onValueChange={(value) =>
            value && set("clip_length_mode", value as StyleOverrides["clip_length_mode"])
          }
        >
          <ToggleGroupItem value="standard">Standard 30–60 s</ToggleGroupItem>
          <ToggleGroupItem value="extended">Allow up to 90 s</ToggleGroupItem>
        </ToggleGroup>
      </Field>

      <Field label={`Clips per video: ${style.clips_per_video}`} id="clips">
        <Slider
          id="clips"
          min={1}
          max={5}
          step={1}
          value={[style.clips_per_video]}
          onValueChange={([value]) => set("clips_per_video", value ?? 5)}
          className="max-w-sm"
          aria-label="Clips per video"
        />
      </Field>

      <Field label={`Background blur: ${style.blur}`} id="blur">
        <Slider
          id="blur"
          min={0}
          max={60}
          step={1}
          value={[style.blur]}
          onValueChange={([value]) => set("blur", value ?? 30)}
          className="max-w-sm"
          aria-label="Background blur"
        />
      </Field>

      <Field label="Crop" id="crop">
        <ToggleGroup
          id="crop"
          type="single"
          variant="outline"
          value={style.crop_mode}
          onValueChange={(value) => value && set("crop_mode", value as StyleOverrides["crop_mode"])}
        >
          {(Object.keys(CROP_LABELS) as (keyof typeof CROP_LABELS)[]).map((key) => (
            <ToggleGroupItem key={key} value={key}>
              {CROP_LABELS[key]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        <p className="text-muted-foreground text-xs">
          Auto follows a face when one is visible most of the time.
        </p>
      </Field>

      <div className="flex items-center gap-3">
        <Switch
          id="title"
          checked={style.show_title}
          onCheckedChange={(value) => set("show_title", value)}
        />
        <Label htmlFor="title">Show the title on the video</Label>
      </div>

      <div className="flex items-center gap-3">
        <Button
          disabled={!dirty || update.isPending}
          onClick={() =>
            update.mutate(
              { style_defaults: style },
              {
                onSuccess: () => {
                  setDraft(null);
                  toast.success("Style saved");
                },
                onError: (error) => toast.error(errorMessage(error)),
              },
            )
          }
        >
          {update.isPending && <Loader2 className="animate-spin" aria-hidden />}
          Save
        </Button>
        <Button variant="ghost" asChild>
          <Link href="/">Back to Home</Link>
        </Button>
        {dirty && <span className="text-muted-foreground text-sm">Unsaved changes</span>}
      </div>
    </div>
  );
}

function Field({ label, id, children }: { label: string; id: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}
