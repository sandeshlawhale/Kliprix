"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Info, Loader2, Plus } from "lucide-react";
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { invalidateBatch, useLlmSession } from "@/hooks/queries";
import { errorMessage, moreClips, type Video } from "@/lib/api";

const COUNTS = [1, 2, 3, 4, 5];
const HINT = "Uses the saved download; new clips never repeat the ones you have.";

/** "More clips" for a finished video whose download is still on disk (D-034), shown at the
 * right of the video's title (D-038). Hidden while the video works; disabled with the
 * reason in the tooltip when its download was deleted. */
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
    <div className="flex shrink-0 items-center gap-1.5">
      <Select value={count} onValueChange={setCount} disabled={reason !== null}>
        <SelectTrigger size="sm" className="w-14" aria-label="How many more clips">
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
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={reason ?? HINT}
            className="text-muted-foreground hover:text-foreground inline-flex size-7 items-center justify-center rounded-md"
          >
            <Info className="size-4" />
          </button>
        </TooltipTrigger>
        <TooltipContent>{reason ?? HINT}</TooltipContent>
      </Tooltip>
    </div>
  );
}
