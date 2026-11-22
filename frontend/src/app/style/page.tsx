"use client";

import { ArrowLeft, Loader2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { CaptionStylePicker } from "@/components/style/caption-style-picker";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useSettings, useUpdateSettings } from "@/hooks/queries";
import { errorMessage, type StyleOverrides } from "@/lib/api";

/** Kliprix decides crop and blur itself; these are not user choices (owner, 2026-11-21). */
const FIXED = { blur: 30, crop_mode: "auto" } as const;

export default function StylePage() {
  const settings = useSettings();
  const update = useUpdateSettings();
  const [draft, setDraft] = useState<StyleOverrides | null>(null);
  const saved = settings.data?.style_defaults;
  const style = draft ?? saved;

  if (settings.isPending) return <Skeleton className="h-96 w-full" />;
  if (settings.isError || !style)
    return (
      <p role="alert" className="text-destructive">
        {errorMessage(settings.error)}
      </p>
    );

  const set = <K extends keyof StyleOverrides>(key: K, value: StyleOverrides[K]) =>
    setDraft({ ...style, [key]: value });
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(saved);
  const save = () =>
    update.mutate(
      { style_defaults: { ...style, ...FIXED } },
      {
        onSuccess: () => {
          setDraft(null);
          toast.success("Style saved");
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    );

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <Button variant="ghost" size="sm" asChild className="-ml-2.5">
            <Link href="/">
              <ArrowLeft aria-hidden /> Back to Home
            </Link>
          </Button>
          <div className="flex items-center gap-3">
            {dirty && <span className="text-muted-foreground text-sm">Unsaved changes</span>}
            <Button disabled={!dirty || update.isPending} onClick={save}>
              {update.isPending && <Loader2 className="animate-spin" aria-hidden />}
              Save
            </Button>
          </div>
        </div>
        <header className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold tracking-tight">Edit video style</h1>
          <p className="text-muted-foreground">
            Defaults for new batches. Each clip can still be changed later.
          </p>
        </header>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="font-medium">Caption style</h2>
        <CaptionStylePicker
          value={style.caption_style}
          onChange={(value) => set("caption_style", value)}
        />
      </section>

      <div className="flex flex-col gap-2">
        <Label htmlFor="clips">Clips per video: {style.clips_per_video}</Label>
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
      </div>

      <div className="flex items-center gap-3">
        <Switch
          id="title"
          checked={style.show_title}
          onCheckedChange={(value) => set("show_title", value)}
        />
        <Label htmlFor="title">Show the title on the video</Label>
      </div>
    </div>
  );
}
