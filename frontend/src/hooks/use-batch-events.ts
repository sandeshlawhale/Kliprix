"use client";

// Live updates for a batch (docs/03): Server-Sent Events are hints to refetch; when the
// stream drops, poll every 3 s until it reconnects.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { keys } from "@/hooks/queries";
import { batchEventsUrl } from "@/lib/api";

const POLL_MS = 3000;
// Many events arrive in bursts; refetch at most this often.
const REFETCH_THROTTLE_MS = 500;

export function useBatchEvents(batchId: string, clipIds: string[] = []): { live: boolean } {
  const client = useQueryClient();
  const [live, setLive] = useState(false);
  const clipKey = clipIds.join(",");

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refetch = () => {
      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        void client.invalidateQueries({ queryKey: keys.batch(batchId) });
        void client.invalidateQueries({ queryKey: keys.batches });
        for (const id of clipKey ? clipKey.split(",") : []) {
          void client.invalidateQueries({ queryKey: keys.clip(id) });
        }
      }, REFETCH_THROTTLE_MS);
    };
    const source = new EventSource(batchEventsUrl(batchId));
    source.onopen = () => setLive(true);
    source.onerror = () => setLive(false);
    for (const kind of ["video", "clip", "batch"]) source.addEventListener(kind, refetch);
    const poll = setInterval(() => {
      if (source.readyState !== EventSource.OPEN) refetch();
    }, POLL_MS);
    return () => {
      source.close();
      clearInterval(poll);
      if (timer) clearTimeout(timer);
    };
  }, [batchId, clipKey, client]);

  return { live };
}
