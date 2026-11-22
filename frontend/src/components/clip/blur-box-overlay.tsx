"use client";

import { useEffect, useRef, useState, type PointerEvent, type RefObject } from "react";

import type { BlurBox } from "@/lib/api";
import { boxVisible, clampBox } from "@/lib/clip-edit";
import { cn } from "@/lib/utils";

// The rendered clip is always 9:16 (1080x1920).
const VIDEO_RATIO = 9 / 16;

interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Where the 9:16 picture sits inside the <video> element (object-contain may letterbox). */
function useContentRect(video: RefObject<HTMLVideoElement | null>): Rect | null {
  const [rect, setRect] = useState<Rect | null>(null);
  useEffect(() => {
    const element = video.current;
    if (!element) return;
    const measure = () => {
      const width = element.clientWidth;
      const height = element.clientHeight;
      const fitWidth = Math.min(width, height * VIDEO_RATIO);
      const fitHeight = fitWidth / VIDEO_RATIO;
      setRect({
        left: element.offsetLeft + (width - fitWidth) / 2,
        top: element.offsetTop + (height - fitHeight) / 2,
        width: fitWidth,
        height: fitHeight,
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [video]);
  return rect;
}

type DragMode = "move" | "resize";

interface Drag {
  mode: DragMode;
  index: number;
  pointerX: number;
  pointerY: number;
  box: BlurBox;
}

/** Draft blur boxes drawn over the player. A box shows while the video time is inside it,
 * and the selected box always shows. Drag a box to move it, its corner to resize it. */
export function BlurBoxOverlay({
  video,
  boxes,
  time,
  selected,
  onSelect,
  onChange,
}: {
  video: RefObject<HTMLVideoElement | null>;
  boxes: BlurBox[];
  time: number;
  selected: number | null;
  onSelect: (index: number) => void;
  onChange: (index: number, box: BlurBox) => void;
}) {
  const rect = useContentRect(video);
  const drag = useRef<Drag | null>(null);
  if (!rect) return null;

  const start = (event: PointerEvent<HTMLElement>, index: number, mode: DragMode) => {
    const box = boxes[index];
    if (!box) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { mode, index, pointerX: event.clientX, pointerY: event.clientY, box };
    onSelect(index);
  };
  const move = (event: PointerEvent<HTMLElement>) => {
    const current = drag.current;
    if (!current) return;
    const dx = (event.clientX - current.pointerX) / rect.width;
    const dy = (event.clientY - current.pointerY) / rect.height;
    const { box } = current;
    const next =
      current.mode === "move"
        ? { ...box, x: box.x + dx, y: box.y + dy }
        : {
            ...box,
            w: Math.min(1 - box.x, box.w + dx),
            h: Math.min(1 - box.y, box.h + dy),
          };
    onChange(current.index, clampBox(next));
  };
  const end = () => {
    drag.current = null;
  };

  return (
    <div
      className="pointer-events-none absolute"
      style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
    >
      {boxes.map((box, index) =>
        boxVisible(box, time) || index === selected ? (
          <div
            // Boxes have no stable id; the index is their identity in the draft.
            key={index}
            role="button"
            tabIndex={-1}
            aria-label={`Blur box ${index + 1}`}
            onPointerDown={(event) => start(event, index, "move")}
            onPointerMove={move}
            onPointerUp={end}
            onPointerCancel={end}
            className={cn(
              "pointer-events-auto absolute cursor-move touch-none rounded-sm border-2 backdrop-blur-md",
              index === selected ? "border-brand" : "border-white/80",
              !boxVisible(box, time) && "border-dashed opacity-60",
            )}
            style={{
              left: `${box.x * 100}%`,
              top: `${box.y * 100}%`,
              width: `${box.w * 100}%`,
              height: `${box.h * 100}%`,
            }}
          >
            <span className="absolute top-0.5 left-1 rounded bg-black/60 px-1 text-[10px] text-white">
              {index + 1}
            </span>
            <span
              aria-hidden
              onPointerDown={(event) => start(event, index, "resize")}
              onPointerMove={move}
              onPointerUp={end}
              onPointerCancel={end}
              className="absolute -right-1.5 -bottom-1.5 size-3.5 cursor-nwse-resize rounded-sm border-2 border-white bg-black/70"
            />
          </div>
        ) : null,
      )}
    </div>
  );
}
