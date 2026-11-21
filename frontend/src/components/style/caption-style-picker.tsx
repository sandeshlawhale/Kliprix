"use client";

import Image from "next/image";

import { STYLE_LABELS } from "@/lib/format";
import { cn } from "@/lib/utils";

export type CaptionStyle = keyof typeof STYLE_LABELS;

/** Each option shows only the caption band of its sample render plus the name, so the
 * picker stays short. The samples are real renders (public/style-samples). */
export function CaptionStylePicker({
  value,
  onChange,
}: {
  value: CaptionStyle;
  onChange: (value: CaptionStyle) => void;
}) {
  return (
    <div className="grid grid-cols-3 gap-3" role="radiogroup" aria-label="Caption style">
      {(Object.keys(STYLE_LABELS) as CaptionStyle[]).map((name) => {
        const selected = value === name;
        return (
          <button
            key={name}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(name)}
            className={cn(
              "group flex flex-col gap-1.5 rounded-xl border p-1.5 text-left text-sm outline-none",
              "focus-visible:ring-brand/50 transition-colors focus-visible:ring-[3px]",
              selected ? "border-brand ring-brand ring-1" : "hover:bg-muted/50",
            )}
          >
            {/* The caption sits at ~69% of the frame height; show a 4:1 band around it. */}
            <Image
              src={`/style-samples/${name}.png`}
              alt={`${STYLE_LABELS[name]} caption sample`}
              width={540}
              height={960}
              unoptimized
              className="aspect-[4/1] w-full rounded-lg object-cover object-[50%_73%]"
            />
            <span className={cn("px-0.5 font-medium", selected && "text-foreground")}>
              {STYLE_LABELS[name]}
            </span>
          </button>
        );
      })}
    </div>
  );
}
