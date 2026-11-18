"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
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
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { keys } from "@/hooks/queries";
import { deleteBatch, errorMessage, type BatchSummary } from "@/lib/api";

export function DeleteBatchDialog({
  batch,
  onClose,
}: {
  batch: BatchSummary | null;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const [withFiles, setWithFiles] = useState(true);
  const remove = useMutation({
    mutationFn: ({ id, files }: { id: string; files: boolean }) => deleteBatch(id, files),
    onSuccess: (_, { id }) => {
      void client.invalidateQueries({ queryKey: keys.batches });
      client.removeQueries({ queryKey: keys.batch(id) });
      toast.success("Batch deleted");
      if (pathname.startsWith(`/history/${id}`)) router.push("/");
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <AlertDialog open={batch !== null} onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this batch?</AlertDialogTitle>
          <AlertDialogDescription>
            “{batch?.title}” is removed from history. Running work for it is stopped.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex items-center gap-3">
          <Switch id="delete-files" checked={withFiles} onCheckedChange={setWithFiles} />
          <Label htmlFor="delete-files">Also delete its files (downloads, clips, exports)</Label>
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={remove.isPending}
            onClick={(event) => {
              event.preventDefault();
              if (batch) remove.mutate({ id: batch.id, files: withFiles });
            }}
          >
            {remove.isPending ? "Deleting…" : "Delete"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
