"use client";

import type { CSSProperties } from "react";

import { STYLE_LABELS } from "@/lib/format";
import { COLORS } from "@/lib/live-captions";
import { cn } from "@/lib/utils";

export type CaptionStyle = keyof typeof STYLE_LABELS;

const SAMPLE = ["This", "is", "how"];
const ACTIVE = 0;
const FONT = '"Kliprix Caption", Arial, sans-serif';

/** The renderer's look for one word (same colors as the live preview, D-057). */
function wordStyle(style: CaptionStyle, index: number): CSSProperties {
  const outline: CSSProperties = {
    WebkitTextStroke: `3px ${COLORS.outline}`,
    paintOrder: "stroke fill",
  };
  const active = index === ACTIVE;
  if (style === "boxed" && active)
    return {
      backgroundColor: COLORS.box,
      borderRadius: 4,
      padding: "0 3px",
      boxDecorationBreak: "clone",
    };
  if (active) return { ...outline, color: COLORS.active };
  if (style === "karaoke" && index > ACTIVE) return { ...outline, opacity: COLORS.upcomingOpacity };
  return outline;
}

/** Each option is the caption alone on a plain tile, with the style's name under it. */
export function CaptionStylePicker({
  value,
  onChange,
}: {
  value: CaptionStyle;
  onChange: (value: CaptionStyle) => void;
}) {
  return (
    <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Caption style">
      {(Object.keys(STYLE_LABELS) as CaptionStyle[]).map((name) => {
        const selected = value === name;
        return (
          <button
            key={name}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(name)}
            className="group flex flex-col items-center gap-1.5 rounded-xl text-sm outline-none"
          >
            <span
              className={cn(
                "flex h-16 bg-neutral-800 w-full items-center justify-center rounded-xl ring-1 transition",
                "group-focus-visible:ring-brand/50 group-focus-visible:ring-[3px]",
                selected ? "ring-brand ring-2" : "ring-border group-hover:ring-foreground/30",
              )}
            >
              <span
                aria-hidden
                className="text-[15px] whitespace-nowrap text-white"
                style={{ fontFamily: FONT }}
              >
                {SAMPLE.map((word, index) => (
                  <span key={word}>
                    {index > 0 && " "}
                    <span style={wordStyle(name, index)}>{word}</span>
                  </span>
                ))}
              </span>
            </span>
            <span
              className={cn(
                "text-xs",
                selected ? "text-foreground font-medium" : "text-muted-foreground",
              )}
            >
              {STYLE_LABELS[name]}
            </span>
          </button>
        );
      })}
    </div>
  );
}
