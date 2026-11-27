"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Info } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ACCENT_TOGGLE, SettingGroup, SettingRow } from "@/components/settings/setting-row";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { keys, useSettings, useStorage, useUpdateSettings } from "@/hooks/queries";
import { cleanupStorage, errorMessage, type CleanupScope, type Settings } from "@/lib/api";
import { bytes } from "@/lib/format";
import { cn } from "@/lib/utils";

type Cleanup = { scope: CleanupScope; label: string; text: string };

/** Each data folder: its name, bar color, what it holds, and the cleanup that empties it. */
const FOLDERS: Record<string, { label: string; color: string; info: string; cleanup?: Cleanup }> = {
  downloads: {
    label: "Source videos",
    color: "bg-sky-500",
    info: "Videos and audio downloaded from your links. Needed to trim clips beyond their editing handles.",
    cleanup: {
      scope: "source_videos",
      label: "Delete source videos",
      text: "Deletes the downloaded videos and audio of finished batches. Clips, transcripts and the 5 s editing handles stay, so you can still edit and export; trimming beyond the handles is no longer possible.",
    },
  },
  exports: {
    label: "Exports",
    color: "bg-brand",
    info: "Your finished vertical shorts (MP4 files).",
  },
  previews: {
    label: "Previews",
    color: "bg-violet-500",
    info: "Low-resolution previews for the editor. Made again when needed.",
    cleanup: {
      scope: "previews",
      label: "Delete previews",
      text: "Deletes all low-resolution previews. Exports are kept.",
    },
  },
  work: {
    label: "Work files",
    color: "bg-amber-500",
    info: "Cut and crop files used while rendering.",
    cleanup: {
      scope: "work",
      label: "Delete work files",
      text: "Deletes cut and crop files. Clips re-cut from the source when edited; clips whose source was deleted can no longer be re-rendered.",
    },
  },
  models: {
    label: "AI models",
    color: "bg-emerald-500",
    info: "Transcription and face detection models. Downloaded again when needed.",
  },
};

const CLEAR_ALL: Cleanup = {
  scope: "all",
  label: "Clear all Kliprix storage",
  text: "Deletes everything Kliprix stored on this computer: batch history, downloads, exported clips, previews, work files and downloaded models (they download again when needed). Your settings stay. This cannot be undone. It is refused while work is running.",
};

function InfoTip({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={text}
          className="text-muted-foreground hover:text-foreground inline-flex"
        >
          <Info className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-64">{text}</TooltipContent>
    </Tooltip>
  );
}

/** Usage bar, one row per folder with its cleanup, and the storage settings (D-067). */
export function StorageSection() {
  const storage = useStorage();
  const settings = useSettings();
  const update = useUpdateSettings();
  const client = useQueryClient();
  const [pending, setPending] = useState<Cleanup | null>(null);
  const [warnGb, setWarnGb] = useState<string | null>(null);
  const cleanup = useMutation({
    mutationFn: (scope: CleanupScope) => cleanupStorage(scope),
    onSuccess: (result) => {
      void client.invalidateQueries({ queryKey: keys.storage });
      toast.success(`Freed ${bytes(result.freed_bytes)}`);
      setPending(null);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const save = (change: Partial<Settings>) =>
    update.mutate(change, {
      onSuccess: () => void client.invalidateQueries({ queryKey: keys.storage }),
      onError: (error) => toast.error(errorMessage(error)),
    });
  const saveWarn = () => {
    if (warnGb === null) return;
    const value = Number(warnGb);
    if (Number.isFinite(value) && value > 0) {
      if (value !== settings.data?.storage_warn_gb) save({ storage_warn_gb: value });
    } else toast.error("Enter a size above 0 GB.");
    setWarnGb(null);
  };

  const data = storage.data;
  const folders = data ? Object.entries(data.folders) : [];
  const total = data?.total_bytes ?? 0;

  return (
    <div className="flex flex-col gap-6" id="storage">
      <SettingGroup title="Storage">
        {storage.isPending && <Skeleton className="my-3 h-20 w-full" />}
        {storage.isError && (
          <p className="text-destructive py-3 text-sm">{errorMessage(storage.error)}</p>
        )}
        {data && (
          <div className="flex flex-col gap-3 py-3">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-2xl font-semibold tabular-nums">{bytes(total)}</span>
              <span
                className={cn(
                  "text-xs",
                  data.warn ? "text-destructive font-medium" : "text-muted-foreground",
                )}
              >
                {data.warn ? "Above your warning size" : "used by Kliprix"}
              </span>
            </div>
            <div
              className="bg-muted flex h-2 w-full gap-px overflow-hidden rounded-full"
              role="img"
              aria-label={`Kliprix uses ${bytes(total)}`}
            >
              {total > 0 &&
                folders.map(([name, size]) =>
                  size > 0 ? (
                    <span
                      key={name}
                      className={cn("h-full", FOLDERS[name]?.color ?? "bg-muted-foreground")}
                      style={{ width: `${(size / total) * 100}%` }}
                    />
                  ) : null,
                )}
            </div>
          </div>
        )}
        {folders.map(([name, size]) => {
          const folder = FOLDERS[name];
          return (
            <SettingRow
              key={name}
              className="min-h-11 py-2"
              label={
                <span className="flex items-center gap-2">
                  <span
                    aria-hidden
                    className={cn("size-2 rounded-full", folder?.color ?? "bg-muted-foreground")}
                  />
                  <span className={folder ? undefined : "capitalize"}>{folder?.label ?? name}</span>
                  {folder && <InfoTip text={folder.info} />}
                </span>
              }
            >
              <span className="text-muted-foreground font-mono text-xs tabular-nums">
                {bytes(size)}
              </span>
              {folder?.cleanup ? (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={size === 0}
                  onClick={() => setPending(folder.cleanup ?? null)}
                >
                  Delete
                </Button>
              ) : (
                <span className="w-[3.75rem]" aria-hidden />
              )}
            </SettingRow>
          );
        })}
      </SettingGroup>

      {settings.data && (
        <SettingGroup title="Storage options">
          <SettingRow label="Source videos after processing">
            <ToggleGroup
              type="single"
              variant="outline"
              value={settings.data.source_policy}
              onValueChange={(value) =>
                value && save({ source_policy: value as Settings["source_policy"] })
              }
              aria-label="Source videos after processing"
            >
              <ToggleGroupItem value="ask" className={ACCENT_TOGGLE}>
                Ask
              </ToggleGroupItem>
              <ToggleGroupItem value="delete" className={ACCENT_TOGGLE}>
                Delete
              </ToggleGroupItem>
              <ToggleGroupItem value="keep" className={ACCENT_TOGGLE}>
                Keep
              </ToggleGroupItem>
            </ToggleGroup>
          </SettingRow>
          <SettingRow label="Warn when data is larger than" htmlFor="warn">
            <div className="relative">
              <Input
                id="warn"
                type="number"
                min={0.1}
                step={0.1}
                className="w-28 pr-9 text-right tabular-nums"
                value={warnGb ?? String(settings.data.storage_warn_gb)}
                onChange={(event) => setWarnGb(event.target.value)}
                onBlur={saveWarn}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.currentTarget.blur();
                }}
              />
              <span className="text-muted-foreground pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs">
                GB
              </span>
            </div>
          </SettingRow>
          <SettingRow label="Clear all storage" hint="History, clips, downloads and models.">
            <Button variant="destructive" size="sm" onClick={() => setPending(CLEAR_ALL)}>
              Clear all
            </Button>
          </SettingRow>
        </SettingGroup>
      )}

      <AlertDialog open={pending !== null} onOpenChange={(open) => !open && setPending(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{pending?.label}?</AlertDialogTitle>
            <AlertDialogDescription>{pending?.text}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={cleanup.isPending}
              onClick={(event) => {
                event.preventDefault();
                if (pending) cleanup.mutate(pending.scope);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
