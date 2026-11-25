"use client";

import { Plus, ZoomIn, ZoomOut } from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import type { BlurBox, Word } from "@/lib/api";
import { moveBoxTime, resizeBoxTime, wordAt, type TimelinePart } from "@/lib/clip-edit";
import { clock, hms } from "@/lib/format";
import { cn } from "@/lib/utils";

/** What the inspector above the timeline shows (D-056). */
export type Selection =
  | { kind: "video" }
  | { kind: "title" }
  | { kind: "captions" }
  | { kind: "word"; index: number }
  | { kind: "blur"; index: number };

// Pixels per second at each zoom step; speech is dense, so the default is wide.
const ZOOMS = [60, 120, 200, 320] as const;
const DEFAULT_ZOOM = 1;
const TICK_LABEL_EVERY_S = 5;
const CHAR_PX = 6.5;
const LANES = 2;
const DRAG_THRESHOLD_PX = 3;

// Row heights in px; the label column and the tracks use the same numbers so they line up.
const RULER_H = 24;
const ROWS = { video: 36, title: 36, captions: 64, blur: 36 } as const;
const WORD_TOP = [6, 34];

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

interface BoxDrag {
  index: number;
  mode: "move" | "start" | "end";
  pointerX: number;
  box: BlurBox;
  moved: boolean;
}

/** The editor's tracks (D-056): Video, Title, Captions and Blur on one ruler and playhead,
 * in clip time. Clicking an item selects it for the inspector; blur bars drag in time. */
export function EditTimeline({
  parts,
  title,
  words,
  boxes,
  length,
  time,
  onSeek,
  selection,
  onSelect,
  onBoxesChange,
  onRemoveWord,
  onRemoveBox,
  onAddBox,
  canAddBox,
}: {
  parts: TimelinePart[];
  title: string;
  words: Word[];
  boxes: BlurBox[];
  length: number;
  time: number;
  onSeek: (time: number) => void;
  selection: Selection;
  onSelect: (selection: Selection) => void;
  onBoxesChange: (boxes: BlurBox[]) => void;
  onRemoveWord: (index: number) => void;
  onRemoveBox: (index: number) => void;
  onAddBox: () => void;
  canAddBox: boolean;
}) {
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  const px = ZOOMS[zoom]!;
  const scroller = useRef<HTMLDivElement>(null);
  const chips = useRef<(HTMLButtonElement | null)[]>([]);
  const drag = useRef<BoxDrag | null>(null);
  const end = Math.max(length, words.at(-1)?.end ?? 0, 1);
  const width = Math.ceil(end * px) + 24;
  const playing = wordAt(words, time);
  const selectedWord = selection.kind === "word" ? selection.index : null;
  const selectedBox = selection.kind === "blur" ? selection.index : null;

  // Keep the playhead in view while the video plays.
  useEffect(() => {
    const box = scroller.current;
    if (!box) return;
    const x = time * px;
    if (x < box.scrollLeft + 24 || x > box.scrollLeft + box.clientWidth - 48) {
      box.scrollTo({ left: Math.max(0, x - box.clientWidth / 3) });
    }
  }, [time, px]);

  const pickWord = (index: number) => {
    const word = words[index];
    if (!word) return;
    onSelect({ kind: "word", index });
    onSeek(word.start);
    chips.current[index]?.focus();
  };
  const onWordKey = (event: KeyboardEvent, index: number) => {
    if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      onRemoveWord(index);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      pickWord(Math.min(index + 1, words.length - 1));
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      pickWord(Math.max(index - 1, 0));
    }
  };

  const startBoxDrag = (event: PointerEvent<HTMLElement>, index: number, mode: BoxDrag["mode"]) => {
    const box = boxes[index];
    if (!box || event.button !== 0) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { index, mode, pointerX: event.clientX, box, moved: false };
    onSelect({ kind: "blur", index });
  };
  const moveBoxDrag = (event: PointerEvent<HTMLElement>) => {
    const current = drag.current;
    if (!current) return;
    const dx = event.clientX - current.pointerX;
    if (!current.moved && Math.abs(dx) < DRAG_THRESHOLD_PX) return;
    current.moved = true;
    const delta = dx / px;
    const next =
      current.mode === "move"
        ? moveBoxTime(current.box, delta, length)
        : resizeBoxTime(current.box, current.mode, delta, length);
    onBoxesChange(boxes.map((box, i) => (i === current.index ? next : box)));
  };
  const endBoxDrag = () => {
    const current = drag.current;
    drag.current = null;
    // A plain click (no drag) jumps to where the box starts.
    if (current && !current.moved) onSeek(current.box.start);
  };

  const seekFromRuler = (event: PointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    onSeek(Math.min(length, Math.max(0, (event.clientX - rect.left) / px)));
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground font-mono text-xs">
          {hms(time)} / {hms(length)}
        </span>
        <div className="flex items-center gap-1">
          <span className="text-muted-foreground mr-1 text-xs">{px} px/s</span>
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

      <div className="bg-muted/40 flex overflow-hidden rounded-md border">
        {/* Track names; each selects that track's settings in the inspector. */}
        <div className="bg-background flex w-24 flex-none flex-col border-r">
          <div style={{ height: RULER_H }} className="border-b" />
          <TrackName
            height={ROWS.video}
            active={selection.kind === "video"}
            onClick={() => onSelect({ kind: "video" })}
          >
            Video
          </TrackName>
          <TrackName
            height={ROWS.title}
            active={selection.kind === "title"}
            onClick={() => onSelect({ kind: "title" })}
          >
            Title
          </TrackName>
          <TrackName
            height={ROWS.captions}
            active={selection.kind === "captions" || selection.kind === "word"}
            onClick={() => onSelect({ kind: "captions" })}
          >
            Captions
          </TrackName>
          <div className="flex items-center border-b last:border-b-0" style={{ height: ROWS.blur }}>
            <TrackName
              height={ROWS.blur}
              active={selection.kind === "blur"}
              onClick={() => onSelect({ kind: "blur", index: -1 })}
              className="flex-1 border-b-0"
            >
              Blur
            </TrackName>
            <Button
              variant="ghost"
              size="icon"
              className="mr-1 size-6"
              aria-label={`Add blur box at ${time.toFixed(1)} s`}
              title={`Add blur box at ${time.toFixed(1)} s`}
              disabled={!canAddBox}
              onClick={onAddBox}
            >
              <Plus aria-hidden />
            </Button>
          </div>
        </div>

        <div
          ref={scroller}
          className="relative min-w-0 flex-1 overflow-x-auto"
          aria-label="Timeline"
        >
          <div className="relative" style={{ width }}>
            {/* Ruler: a tick every second, a label every 5 s. Click or drag it to seek. */}
            <div
              className="relative cursor-pointer border-b"
              style={{ height: RULER_H }}
              onPointerDown={(event) => {
                event.currentTarget.setPointerCapture(event.pointerId);
                seekFromRuler(event);
              }}
              onPointerMove={(event) => {
                if (event.currentTarget.hasPointerCapture(event.pointerId)) seekFromRuler(event);
              }}
              aria-hidden
            >
              {Array.from({ length: Math.floor(end) + 1 }, (_, second) => (
                <div
                  key={second}
                  className="border-border absolute bottom-0 border-l"
                  style={{
                    left: second * px,
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

            {/* Video: the played parts, end to end; a montage shows a cut mark at each join. */}
            <Row height={ROWS.video}>
              {parts.map((part, index) => (
                <button
                  key={index}
                  type="button"
                  onClick={() => onSelect({ kind: "video" })}
                  title={`Source ${hms(part.source[0])}–${hms(part.source[1])}`}
                  className={cn(
                    "bg-secondary text-secondary-foreground absolute top-1.5 bottom-1.5 truncate rounded border px-2 text-left text-xs",
                    selection.kind === "video" && "border-brand ring-brand ring-1",
                  )}
                  style={{ left: part.start * px + 1, width: (part.end - part.start) * px - 2 }}
                >
                  {parts.length > 1 ? `Part ${index + 1} · ` : ""}
                  {hms(part.source[0])}–{hms(part.source[1])}
                </button>
              ))}
              {parts.slice(1).map((part, index) => (
                <div
                  key={`cut-${index}`}
                  className="border-muted-foreground pointer-events-none absolute inset-y-0.5 border-l-2 border-dashed"
                  style={{ left: part.start * px - 1 }}
                  title="Skipped gap"
                  aria-hidden
                />
              ))}
            </Row>

            {/* Title: on the video for the whole clip. */}
            <Row height={ROWS.title}>
              <button
                type="button"
                onClick={() => onSelect({ kind: "title" })}
                className={cn(
                  "bg-background absolute top-1.5 bottom-1.5 truncate rounded border px-2 text-left text-xs",
                  !title && "text-muted-foreground italic",
                  selection.kind === "title" && "border-brand ring-brand ring-1",
                )}
                style={{ left: 1, width: length * px - 2 }}
              >
                {title || "No title"}
              </button>
            </Row>

            {/* Captions: each word at its time in two rows. */}
            <Row height={ROWS.captions}>
              {words.map((word, index) => (
                <button
                  // Words have no id; the index is their identity in the draft.
                  key={index}
                  ref={(node) => {
                    chips.current[index] = node;
                  }}
                  type="button"
                  title={`${word.text} · ${word.start.toFixed(2)}–${word.end.toFixed(2)} s`}
                  onClick={() => pickWord(index)}
                  onKeyDown={(event) => onWordKey(event, index)}
                  className={cn(
                    "bg-background absolute h-6 truncate rounded border px-1 text-left text-xs transition-colors",
                    "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                    index === playing && "bg-accent",
                    // The picked word shows in full, above its neighbours.
                    index === selectedWord && "border-brand ring-brand z-10 min-w-max ring-1",
                  )}
                  style={{
                    left: word.start * px,
                    top: WORD_TOP[index % LANES],
                    width: chipWidth(words, index, px),
                  }}
                >
                  {word.text}
                </button>
              ))}
            </Row>

            {/* Blur: one bar per box; drag it to move it in time, its ends to change From/To. */}
            <Row height={ROWS.blur} last>
              {boxes.map((box, index) => (
                <div
                  // Boxes have no stable id; the index is their identity in the draft.
                  key={index}
                  role="button"
                  tabIndex={0}
                  aria-label={`Blur box ${index + 1}, ${box.start.toFixed(1)}–${box.end.toFixed(1)} s`}
                  aria-pressed={index === selectedBox}
                  onPointerDown={(event) => startBoxDrag(event, index, "move")}
                  onPointerMove={moveBoxDrag}
                  onPointerUp={endBoxDrag}
                  onPointerCancel={() => (drag.current = null)}
                  onKeyDown={(event) => {
                    if (event.key === "Delete" || event.key === "Backspace") {
                      event.preventDefault();
                      onRemoveBox(index);
                    } else if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelect({ kind: "blur", index });
                      onSeek(box.start);
                    }
                  }}
                  className={cn(
                    "absolute top-1.5 bottom-1.5 cursor-grab touch-none rounded border bg-pink-200/70 text-xs text-pink-950 select-none active:cursor-grabbing dark:bg-pink-900/60 dark:text-pink-50",
                    "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                    index === selectedBox && "border-brand ring-brand z-10 ring-1",
                  )}
                  style={{ left: box.start * px, width: Math.max(8, (box.end - box.start) * px) }}
                >
                  <span className="pointer-events-none absolute inset-y-0 left-2 flex items-center truncate">
                    Box {index + 1}
                  </span>
                  <span
                    aria-hidden
                    className="absolute inset-y-0 left-0 w-1.5 cursor-ew-resize rounded-l bg-pink-500/60"
                    onPointerDown={(event) => startBoxDrag(event, index, "start")}
                    onPointerMove={moveBoxDrag}
                    onPointerUp={endBoxDrag}
                  />
                  <span
                    aria-hidden
                    className="absolute inset-y-0 right-0 w-1.5 cursor-ew-resize rounded-r bg-pink-500/60"
                    onPointerDown={(event) => startBoxDrag(event, index, "end")}
                    onPointerMove={moveBoxDrag}
                    onPointerUp={endBoxDrag}
                  />
                </div>
              ))}
            </Row>

            <div
              className="bg-brand pointer-events-none absolute inset-y-0 z-20 w-0.5"
              style={{ left: time * px }}
              aria-hidden
            />
          </div>
        </div>
      </div>
      <p className="text-muted-foreground text-xs">
        Click an item to change it above. Drag a blur bar to move it, its ends to make it shorter or
        longer. Delete removes the picked word or box; arrow keys move between words.
      </p>
    </div>
  );
}

function Row({ height, last, children }: { height: number; last?: boolean; children: ReactNode }) {
  return (
    <div className={cn("relative", !last && "border-b")} style={{ height }}>
      {children}
    </div>
  );
}

function TrackName({
  height,
  active,
  onClick,
  className,
  children,
}: {
  height: number;
  active: boolean;
  onClick: () => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "hover:bg-muted flex items-center border-b px-3 text-left text-xs transition-colors",
        active ? "text-foreground font-medium" : "text-muted-foreground",
        className,
      )}
      style={{ height }}
    >
      {children}
    </button>
  );
}
