"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import { toast } from "sonner";

import { StorageBanner } from "@/components/layout/storage-banner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { invalidateBatch, useBatch } from "@/hooks/queries";
import { cancelBatch, errorMessage } from "@/lib/api";

const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Processing",
  done: "Done",
  partial: "Partly done",
  failed: "Failed",
  cancelled: "Cancelled",
};
const ACTIVE = ["queued", "running"];

/** The row above every page (D-038): the storage warning on the left, the open batch's
 * status and date on the right. It keeps its height when both are empty, so the page
 * content always starts at the same place. */
export function TopBar() {
  const pathname = usePathname();
  const batchId = /^\/history\/([^/]+)\/?$/.exec(pathname)?.[1];
  return (
    <div className="mb-6 flex min-h-8 items-center justify-between gap-4">
      <StorageBanner />
      {batchId && <BatchStamp batchId={batchId} />}
    </div>
  );
}

function BatchStamp({ batchId }: { batchId: string }) {
  const batch = useBatch(batchId);
  const client = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => cancelBatch(batchId),
    onSuccess: () => invalidateBatch(client, batchId),
    onError: (error) => toast.error(errorMessage(error)),
  });
  if (!batch.data) return null;
  const { status, created_at: createdAt } = batch.data;
  const created = new Date(createdAt);
  return (
    <div className="ml-auto flex shrink-0 items-center gap-3">
      <Badge variant="outline">{STATUS_LABELS[status] ?? status}</Badge>
      {ACTIVE.includes(status) && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => cancel.mutate()}
          disabled={cancel.isPending}
        >
          Cancel
        </Button>
      )}
      <time dateTime={createdAt} className="text-muted-foreground text-sm tabular-nums">
        {created.toLocaleDateString(undefined, { dateStyle: "medium" })} ·{" "}
        {created.toLocaleTimeString(undefined, { timeStyle: "short" })}
      </time>
    </div>
  );
}
