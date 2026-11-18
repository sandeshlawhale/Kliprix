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
import { keys, useSettings } from "@/hooks/queries";
import { cleanupStorage, errorMessage, type Batch } from "@/lib/api";
import { bytes } from "@/lib/format";

const ANSWERED_KEY = "kliprix.source-asked";
const FINISHED = ["done", "partial"];

function answered(batchId: string): boolean {
  try {
    return (JSON.parse(window.localStorage.getItem(ANSWERED_KEY) ?? "[]") as string[]).includes(
      batchId,
    );
  } catch {
    return false;
  }
}

function remember(batchId: string): void {
  try {
    const list = JSON.parse(window.localStorage.getItem(ANSWERED_KEY) ?? "[]") as string[];
    window.localStorage.setItem(ANSWERED_KEY, JSON.stringify([...list, batchId]));
  } catch {
    // Storage blocked: the question may come back on the next visit.
  }
}

/** With source policy "Ask", a finished batch asks once whether to delete its downloaded
 * source videos (docs/08 "Retention"). */
export function SourcePolicyPrompt({ batch }: { batch: Batch }) {
  const settings = useSettings();
  const client = useQueryClient();
  const [dismissed, setDismissed] = useState(false);
  const remove = useMutation({
    mutationFn: () => cleanupStorage("source_videos", batch.id),
    onSuccess: (result) => {
      void client.invalidateQueries({ queryKey: keys.storage });
      toast.success(`Freed ${bytes(result.freed_bytes)}`);
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const open =
    !dismissed &&
    settings.data?.source_policy === "ask" &&
    FINISHED.includes(batch.status) &&
    !answered(batch.id);
  const close = () => {
    remember(batch.id);
    setDismissed(true);
  };

  return (
    <AlertDialog open={open} onOpenChange={(value) => !value && close()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete the source videos?</AlertDialogTitle>
          <AlertDialogDescription>
            This batch is finished. Deleting its downloaded videos frees space; clips, captions and
            the 5 s editing handles stay, so you can still edit and export (trimming is limited to
            the handles).
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={close}>Keep</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={() => {
              remove.mutate();
              close();
            }}
          >
            Delete source videos
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
