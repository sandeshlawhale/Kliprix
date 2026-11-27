"use client";

import { toast } from "sonner";

import { SettingGroup, SettingRow } from "@/components/settings/setting-row";
import { CaptionStylePicker } from "@/components/style/caption-style-picker";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useSettings, useUpdateSettings } from "@/hooks/queries";
import { errorMessage, type StyleOverrides } from "@/lib/api";

/** Kliprix decides crop and blur itself; these are not user choices (owner, 2026-11-21). */
const FIXED = { blur: 30, crop_mode: "auto" } as const;
const COUNTS = ["1", "2", "3", "4", "5"];

/** Defaults for new batches; every change saves at once (D-067). */
export function StylePane() {
  const settings = useSettings();
  const update = useUpdateSettings();
  const style = settings.data?.style_defaults;

  if (settings.isPending) return <Skeleton className="h-64 w-full" />;
  if (settings.isError || !style)
    return (
      <p role="alert" className="text-destructive text-sm">
        {errorMessage(settings.error)}
      </p>
    );

  const set = <K extends keyof StyleOverrides>(key: K, value: StyleOverrides[K]) =>
    update.mutate(
      { style_defaults: { ...style, ...FIXED, [key]: value } },
      { onError: (error) => toast.error(errorMessage(error)) },
    );

  return (
    <div className="flex flex-col gap-6">
      <p className="text-muted-foreground text-sm">
        Defaults for new batches. Each clip can still be changed later.
      </p>
      <SettingGroup title="Captions">
        <div className="py-3">
          <CaptionStylePicker
            value={style.caption_style}
            onChange={(value) => set("caption_style", value)}
          />
        </div>
      </SettingGroup>
      <SettingGroup title="Clips">
        <SettingRow label="Clips per video">
          <ToggleGroup
            type="single"
            variant="outline"
            value={String(style.clips_per_video)}
            onValueChange={(value) => value && set("clips_per_video", Number(value))}
            aria-label="Clips per video"
          >
            {COUNTS.map((count) => (
              <ToggleGroupItem key={count} value={count} className="w-9">
                {count}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </SettingRow>
        <SettingRow label="Show the title on the video" htmlFor="style-title">
          <Switch
            id="style-title"
            checked={style.show_title}
            onCheckedChange={(value) => set("show_title", value)}
          />
        </SettingRow>
      </SettingGroup>
    </div>
  );
}
