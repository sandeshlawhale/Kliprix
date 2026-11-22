import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { VideoMeta } from "@/lib/api";
import { clock } from "@/lib/format";

/** One added video, shown inside the home page's link box. */
export function VideoCard({ video, onRemove }: { video: VideoMeta; onRemove: () => void }) {
  return (
    <div className="bg-muted/60 flex items-center gap-3 rounded-xl p-2">
      {video.thumbnail_url ? (
        // Remote YouTube thumbnail; next/image would need a remote host config for one image.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={video.thumbnail_url}
          alt=""
          className="aspect-video w-24 shrink-0 rounded-lg object-cover"
          referrerPolicy="no-referrer"
        />
      ) : (
        <div className="bg-muted aspect-video w-24 shrink-0 rounded-lg" aria-hidden />
      )}
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="truncate text-sm font-medium">{video.title}</p>
        <p className="text-muted-foreground truncate text-xs">
          {video.channel ?? "Unknown channel"} ·{" "}
          <span className="font-mono">{clock(video.duration_s)}</span> ·{" "}
          {video.has_chat ? "Chat replay found" : "No chat replay"}
        </p>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        onClick={onRemove}
        aria-label={`Remove ${video.title}`}
      >
        <X />
      </Button>
    </div>
  );
}

export function VideoCardSkeleton() {
  return (
    <div
      className="bg-muted/60 flex items-center gap-3 rounded-xl p-2"
      aria-busy="true"
      aria-label="Reading the link"
    >
      <Skeleton className="aspect-video w-24 shrink-0 rounded-lg" />
      <div className="flex flex-1 flex-col gap-2">
        <Skeleton className="h-3.5 w-2/3" />
        <Skeleton className="h-3 w-1/3" />
      </div>
    </div>
  );
}
