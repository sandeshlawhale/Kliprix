"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { invalidateBatch, useLlmSession } from "@/hooks/queries";
import { errorMessage, moreClips, type Video } from "@/lib/api";

const COUNTS = [1, 2, 3, 4, 5];

/** "More clips" for a finished video whose download is still on disk (D-034): no new
 * download, new clips never overlap the ones already made. Hidden while the video works;
 * shown disabled with the reason when its download was deleted. */
export function MoreClips({
  video,
  batchId,
  defaultCount,
}: {
  video: Video;
  batchId: string;
  defaultCount: number;
}) {
  const client = useQueryClient();
  const session = useLlmSession();
  const [count, setCount] = useState(String(defaultCount));
  const add = useMutation({
    mutationFn: () => moreClips(video.id, Number(count)),
    onSuccess: () => {
      invalidateBatch(client, batchId);
      toast.success("Looking for more clips in this video");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (video.status === "queued" || video.status === "running") return null;
  const noModel = session.data?.configured === false;
  const reason = video.add_clips_blocked ?? (noModel ? "Configure the AI model first." : null);
  const label = video.clips.length > 0 ? "More clips" : "Find clips";

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={count} onValueChange={setCount} disabled={reason !== null}>
          <SelectTrigger className="w-20" aria-label="How many more clips">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {COUNTS.map((value) => (
              <SelectItem key={value} value={String(value)}>
                {value}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          size="sm"
          onClick={() => add.mutate()}
          disabled={reason !== null || add.isPending}
        >
          {add.isPending ? <Loader2 className="animate-spin" aria-hidden /> : <Plus aria-hidden />}
          {label}
        </Button>
      </div>
      <p className="text-muted-foreground text-sm">
        {reason ?? "Uses the saved download; new clips never repeat the ones you have."}
      </p>
    </div>
  );
}
