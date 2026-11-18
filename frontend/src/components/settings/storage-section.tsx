"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

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
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { keys, useSettings, useStorage, useUpdateSettings } from "@/hooks/queries";
import { cleanupStorage, errorMessage, type CleanupScope, type Settings } from "@/lib/api";
import { bytes } from "@/lib/format";

const CLEANUPS: { scope: CleanupScope; label: string; text: string }[] = [
  {
    scope: "source_videos",
    label: "Delete source videos",
    text: "Deletes the downloaded videos and audio of finished batches. Clips, transcripts and the 5 s editing handles stay, so you can still edit and export; trimming beyond the handles is no longer possible.",
  },
  {
    scope: "previews",
    label: "Delete previews",
    text: "Deletes all low-resolution previews. Exports are kept.",
  },
  {
    scope: "work",
    label: "Delete work files",
    text: "Deletes cut and crop files. Clips re-cut from the source when edited; clips whose source was deleted can no longer be re-rendered.",
  },
];

export function StorageSection() {
  const storage = useStorage();
  const settings = useSettings();
  const update = useUpdateSettings();
  const client = useQueryClient();
  const [pending, setPending] = useState<(typeof CLEANUPS)[number] | null>(null);
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

  return (
    <section className="flex flex-col gap-4" id="storage">
      <h2 className="font-medium">Storage</h2>
      {storage.isPending && <Skeleton className="h-24 w-full" />}
      {storage.isError && <p className="text-destructive text-sm">{errorMessage(storage.error)}</p>}
      {storage.data && (
        <>
          {storage.data.warn && (
            <p role="alert" className="text-destructive text-sm">
              Kliprix data uses {bytes(storage.data.total_bytes)}, more than your warning size.
            </p>
          )}
          <dl className="grid grid-cols-[12rem_1fr] gap-y-1 text-sm">
            {Object.entries(storage.data.folders).map(([name, size]) => (
              <div key={name} className="contents">
                <dt className="text-muted-foreground capitalize">{name}</dt>
                <dd className="font-mono">{bytes(size)}</dd>
              </div>
            ))}
            <dt className="font-medium">Total</dt>
            <dd className="font-mono font-medium">{bytes(storage.data.total_bytes)}</dd>
          </dl>
        </>
      )}
      {settings.data && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor="policy">Source videos after processing</Label>
            <ToggleGroup
              id="policy"
              type="single"
              variant="outline"
              value={settings.data.source_policy}
              onValueChange={(value) =>
                value && save({ source_policy: value as Settings["source_policy"] })
              }
            >
              <ToggleGroupItem value="ask">Ask</ToggleGroupItem>
              <ToggleGroupItem value="delete">Delete</ToggleGroupItem>
              <ToggleGroupItem value="keep">Keep</ToggleGroupItem>
            </ToggleGroup>
          </div>
          <form
            className="flex items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              const value = Number(warnGb);
              if (Number.isFinite(value) && value > 0) {
                save({ storage_warn_gb: value });
                setWarnGb(null);
              } else toast.error("Enter a size above 0 GB.");
            }}
          >
            <div className="flex flex-col gap-2">
              <Label htmlFor="warn">Warn when data is larger than (GB)</Label>
              <Input
                id="warn"
                type="number"
                min={0.1}
                step={0.1}
                className="w-40"
                value={warnGb ?? String(settings.data.storage_warn_gb)}
                onChange={(event) => setWarnGb(event.target.value)}
              />
            </div>
            <Button type="submit" variant="outline" disabled={warnGb === null}>
              Save
            </Button>
          </form>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {CLEANUPS.map((item) => (
          <Button key={item.scope} variant="outline" size="sm" onClick={() => setPending(item)}>
            {item.label}
          </Button>
        ))}
      </div>
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
    </section>
  );
}
