"use client";

import { ChevronLeft, ChevronRight, Trash2, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Word } from "@/lib/api";
import { wordAt } from "@/lib/clip-edit";
import { clock } from "@/lib/format";
import { cn } from "@/lib/utils";

// Pixels per second at each zoom step; speech is dense, so the default is wide.
const ZOOMS = [60, 120, 200, 320] as const;
const DEFAULT_ZOOM = 1;
const TICK_LABEL_EVERY_S = 5;
const CHAR_PX = 6.5;
const LANES = 2;
const LANE_TOP = [32, 64];

/** Chip width: the word's text when there is room before the next word on its row,
 * never less than its spoken time. */
function chipWidth(words: Word[], index: number, px: number): number {
  const word = words[index]!;
  const spoken = (word.end - word.start) * px - 2;
  const text = word.text.length * CHAR_PX + 10;
  const next = words[index + LANES];
  const room = next ? (next.start - word.start) * px - 3 : text;
  return Math.max(14, spoken, Math.min(text, room));
}

/** Caption words on a time track (D-055): each word sits at its time, the playhead follows
 * the video, and clicking a word jumps there. The picked word can be retyped or removed;
 * a word cleared to nothing is removed too. Times never change here. */
export function CaptionTimeline({
  words,
  onChange,
  length,
  time,
  onSeek,
}: {
  words: Word[];
  onChange: (words: Word[]) => void;
  length: number;
  time: number;
  onSeek: (time: number) => void;
}) {
  const [selected, setSelected] = useState<number | null>(null);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  const PX_PER_S = ZOOMS[zoom]!;
  const track = useRef<HTMLDivElement>(null);
  const chips = useRef<(HTMLButtonElement | null)[]>([]);
  const end = Math.max(length, words.at(-1)?.end ?? 0, 1);
  const width = Math.ceil(end * PX_PER_S) + 24;
  const playing = wordAt(words, time);
  const word = selected === null ? undefined : words[selected];

  // Keep the playhead in view while the video plays.
  useEffect(() => {
    const box = track.current;
    if (!box) return;
    const x = time * PX_PER_S;
    if (x < box.scrollLeft + 24 || x > box.scrollLeft + box.clientWidth - 48) {
      box.scrollTo({ left: Math.max(0, x - box.clientWidth / 3) });
    }
  }, [time, PX_PER_S]);

  const pick = (index: number) => {
    const target = words[index];
    if (!target) return;
    setSelected(index);
    onSeek(target.start);
    chips.current[index]?.focus();
  };
  const remove = (index: number) => {
    onChange(words.filter((_, i) => i !== index));
    if (words.length <= 1) setSelected(null);
    else setSelected(Math.min(index, words.length - 2));
  };
  const onChipKey = (event: KeyboardEvent, index: number) => {
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      remove(index);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      pick(Math.min(index + 1, words.length - 1));
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      pick(Math.max(index - 1, 0));
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground text-xs">{words.length} words</span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="Zoom out"
            disabled={zoom === 0}
            onClick={() => setZoom((value) => value - 1)}
          >
            <ZoomOut aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="Zoom in"
            disabled={zoom === ZOOMS.length - 1}
            onClick={() => setZoom((value) => value + 1)}
          >
            <ZoomIn aria-hidden />
          </Button>
        </div>
      </div>
      <div
        ref={track}
        className="bg-muted/40 relative overflow-x-auto rounded-md border"
        aria-label="Caption timeline"
      >
        <div className="relative h-[104px]" style={{ width }}>
          {/* Ruler: a tick every second, a label every 5 s. Click it to seek. */}
          <div
            className="absolute inset-x-0 top-0 h-6 cursor-pointer border-b"
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              onSeek(Math.max(0, (event.clientX - rect.left) / PX_PER_S));
            }}
            aria-hidden
          >
            {Array.from({ length: Math.floor(end) + 1 }, (_, second) => (
              <div
                key={second}
                className="border-border absolute bottom-0 border-l"
                style={{
                  left: second * PX_PER_S,
                  height: second % TICK_LABEL_EVERY_S === 0 ? 10 : 5,
                }}
              >
                {second % TICK_LABEL_EVERY_S === 0 && (
                  <span className="text-muted-foreground absolute -top-4 left-1 font-mono text-[10px]">
                    {clock(second)}
                  </span>
                )}
              </div>
            ))}
          </div>
          {words.map((item, index) => (
            <button
              // Words have no id; the index is their identity in the draft.
              key={index}
              ref={(node) => {
                chips.current[index] = node;
              }}
              type="button"
              title={`${item.text} · ${item.start.toFixed(2)}–${item.end.toFixed(2)} s`}
              onClick={() => pick(index)}
              onKeyDown={(event) => onChipKey(event, index)}
              className={cn(
                "bg-background absolute h-7 truncate rounded border px-1 text-left text-xs transition-colors",
                "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                index === playing && "bg-accent",
                // The picked word shows in full, above its neighbours.
                index === selected && "border-brand ring-brand z-10 min-w-max ring-1",
              )}
              style={{
                left: item.start * PX_PER_S,
                top: LANE_TOP[index % LANES],
                width: chipWidth(words, index, PX_PER_S),
              }}
            >
              {item.text}
            </button>
          ))}
          <div
            className="bg-brand pointer-events-none absolute inset-y-0 w-0.5"
            style={{ left: time * PX_PER_S }}
            aria-hidden
          />
        </div>
      </div>

      {word && selected !== null ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Previous word"
            disabled={selected === 0}
            onClick={() => pick(selected - 1)}
          >
            <ChevronLeft aria-hidden />
          </Button>
          <Input
            aria-label={`Caption word ${selected + 1}`}
            value={word.text}
            onChange={(event) =>
              onChange(
                words.map((w, i) => (i === selected ? { ...w, text: event.target.value } : w)),
              )
            }
            onBlur={(event) => {
              // Cleared to nothing = removed; the Remove button removes it itself.
              const next = event.relatedTarget as HTMLElement | null;
              if (!word.text.trim() && next?.dataset.action !== "remove") remove(selected);
            }}
            className="h-8 w-48"
          />
          <Button
            variant="ghost"
            size="icon"
            aria-label="Next word"
            disabled={selected >= words.length - 1}
            onClick={() => pick(selected + 1)}
          >
            <ChevronRight aria-hidden />
          </Button>
          <span className="text-muted-foreground font-mono text-xs">
            {word.start.toFixed(2)}–{word.end.toFixed(2)} s
          </span>
          <Button variant="outline" size="sm" data-action="remove" onClick={() => remove(selected)}>
            <Trash2 aria-hidden /> Remove word
          </Button>
        </div>
      ) : (
        <p className="text-muted-foreground text-xs">
          Click a word to jump to it and change it. Delete removes the picked word; arrow keys move
          between words.
        </p>
      )}
    </div>
  );
}
