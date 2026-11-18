import { X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { VideoMeta } from "@/lib/api";
import { clock } from "@/lib/format";

export function VideoCard({ video, onRemove }: { video: VideoMeta; onRemove: () => void }) {
  return (
    <Card className="flex flex-row items-center gap-4 p-3">
      {video.thumbnail_url ? (
        // Remote YouTube thumbnail; next/image would need a remote host config for one image.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={video.thumbnail_url}
          alt=""
          className="aspect-video w-40 shrink-0 rounded-md object-cover"
          referrerPolicy="no-referrer"
        />
      ) : (
        <div className="bg-muted aspect-video w-40 shrink-0 rounded-md" aria-hidden />
      )}
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="truncate font-medium">{video.title}</p>
        <p className="text-muted-foreground truncate text-sm">
          {video.channel ?? "Unknown channel"} ·{" "}
          <span className="font-mono">{clock(video.duration_s)}</span>
        </p>
        <div>
          <Badge variant={video.has_chat ? "secondary" : "outline"}>
            {video.has_chat ? "Chat replay found" : "No chat replay"}
          </Badge>
        </div>
      </div>
      <Button variant="ghost" size="icon" onClick={onRemove} aria-label={`Remove ${video.title}`}>
        <X />
      </Button>
    </Card>
  );
}

export function VideoCardSkeleton() {
  return (
    <Card
      className="flex flex-row items-center gap-4 p-3"
      aria-busy="true"
      aria-label="Reading the link"
    >
      <Skeleton className="aspect-video w-40 shrink-0" />
      <div className="flex flex-1 flex-col gap-2">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/3" />
      </div>
    </Card>
  );
}
