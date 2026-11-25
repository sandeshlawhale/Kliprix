"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play, Scissors } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { invalidateBatch, useLlmSession } from "@/hooks/queries";
import { customClip, errorMessage, videoSourceUrl, type Video } from "@/lib/api";
import { hms, parseTime } from "@/lib/format";

// Same length rule as an edited clip (D-061).
const MIN_S = 5;
const MAX_S = 90;
const FIRST_LENGTH_S = 30;

/** "Custom clip" (D-061): play the saved download, pick a start and an end, and the app
 * makes that clip like the others (captions, title and metadata, layout). Shown next to
 * More clips; disabled with the reason while the video works or after its download was
 * deleted. */
export function CustomClip({ video, batchId }: { video: Video; batchId: string }) {
  const [open, setOpen] = useState(false);
  const session = useLlmSession();
  if (video.status === "queued" || video.status === "running") return null;
  const noModel = session.data?.configured === false;
  const reason = video.add_clips_blocked ?? (noModel ? "Configure the AI model first." : null);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={reason !== null}
          title={reason ?? "Pick a part of the video yourself and make it a clip"}
        >
          <Scissors aria-hidden /> Custom clip
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-4xl">
        {open && <Picker video={video} batchId={batchId} onDone={() => setOpen(false)} />}
      </DialogContent>
    </Dialog>
  );
}

function Picker({ video, batchId, onDone }: { video: Video; batchId: string; onDone: () => void }) {
  const client = useQueryClient();
  const player = useRef<HTMLVideoElement>(null);
  const stopAt = useRef<number | null>(null);
  const duration = video.duration_s ?? 0;
  const [range, setRange] = useState<[number, number]>([0, Math.min(FIRST_LENGTH_S, duration)]);
  const [start, end] = range;
  const length = end - start;
  const error =
    length < MIN_S || length > MAX_S
      ? `A clip must be ${MIN_S}–${MAX_S} s long (this one is ${length.toFixed(1)} s).`
      : null;
  const now = () => Math.round((player.current?.currentTime ?? 0) * 10) / 10;
  const make = useMutation({
    mutationFn: () => customClip(video.id, start, end),
    onSuccess: () => {
      invalidateBatch(client, batchId);
      toast.success("Making your clip. It shows up below when it is ready.");
      onDone();
    },
    onError: (failure) => toast.error(errorMessage(failure)),
  });
  const playRange = () => {
    const element = player.current;
    if (!element) return;
    element.currentTime = start;
    stopAt.current = end;
    void element.play();
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>Custom clip</DialogTitle>
        <DialogDescription>
          Play the video, set where the clip starts and ends, then make it. It gets captions, a
          title and the same layout as the other clips.
        </DialogDescription>
      </DialogHeader>
      <video
        ref={player}
        src={videoSourceUrl(video.id)}
        controls
        playsInline
        preload="metadata"
        className="bg-muted aspect-video max-h-[50svh] w-full rounded-lg object-contain"
        onTimeUpdate={(event) => {
          const limit = stopAt.current;
          if (limit !== null && event.currentTarget.currentTime >= limit) {
            event.currentTarget.pause();
            stopAt.current = null;
          }
        }}
      />
      {duration > 0 && (
        <Slider
          min={0}
          max={duration}
          step={0.1}
          minStepsBetweenThumbs={MIN_S * 10}
          value={range}
          onValueChange={([from, to]) => setRange([from ?? start, to ?? end])}
          aria-label="Clip range in the video"
        />
      )}
      <div className="flex flex-wrap items-end gap-4">
        <TimeInput
          label="Start"
          id="custom-start"
          value={start}
          onChange={(value) => setRange([Math.max(0, value), end])}
        />
        <Button variant="outline" size="sm" onClick={() => setRange([now(), end])}>
          Start = now
        </Button>
        <TimeInput
          label="End"
          id="custom-end"
          value={end}
          onChange={(value) => setRange([start, Math.min(duration || value, value)])}
        />
        <Button variant="outline" size="sm" onClick={() => setRange([start, now()])}>
          End = now
        </Button>
        <Button variant="ghost" size="sm" onClick={playRange}>
          <Play aria-hidden /> Play the range
        </Button>
        <span className="text-muted-foreground pb-1.5 text-sm">
          Length <span className="font-mono">{length.toFixed(1)} s</span>
        </span>
      </div>
      {error && (
        <p className="text-destructive text-sm" role="alert">
          {error}
        </p>
      )}
      <DialogFooter>
        <Button disabled={error !== null || make.isPending} onClick={() => make.mutate()}>
          {make.isPending && <Loader2 className="animate-spin" aria-hidden />}
          Make clip
        </Button>
      </DialogFooter>
    </>
  );
}

/** An hh:mm:ss position (also mm:ss or seconds), applied on Enter or when it loses focus. */
function TimeInput({
  label,
  id,
  value,
  onChange,
}: {
  label: string;
  id: string;
  value: number;
  onChange: (value: number) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const invalid = text !== null && parseTime(text) === null;
  const commit = () => {
    if (text === null) return;
    const parsed = parseTime(text);
    if (parsed !== null) onChange(parsed);
    setText(null);
  };
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        inputMode="numeric"
        placeholder="hh:mm:ss"
        aria-invalid={invalid}
        className="w-28 font-mono"
        value={text ?? hms(value)}
        onChange={(event) => setText(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") commit();
          if (event.key === "Escape") setText(null);
        }}
      />
    </div>
  );
}
