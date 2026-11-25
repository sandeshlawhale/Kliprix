"use client";

import { useEffect, useMemo, useState, type CSSProperties, type RefObject } from "react";

import { useContentRect } from "@/components/clip/blur-box-overlay";
import type { Clip, Word } from "@/lib/api";
import {
  CANVAS_H,
  CANVAS_W,
  CAPTION,
  captionAt,
  chunkWords,
  COLORS,
  estimateWidth,
  fitTitle,
  EM_PER_ASS_SIZE,
  TITLE,
  type CaptionStyle,
  type Measure,
} from "@/lib/live-captions";

const FONTS = {
  title: '"Kliprix Title", Impact, sans-serif',
  caption: '"Kliprix Caption", "Kliprix Devanagari", Arial, sans-serif',
} as const;

/** Text width with the renderer's fonts once they are loaded (an estimate until then). */
function useMeasure(): Measure {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let alive = true;
    void Promise.all([
      document.fonts.load(`${TITLE.size}px ${FONTS.title}`),
      document.fonts.load(`${CAPTION.size}px ${FONTS.caption}`),
    ]).then(() => alive && setReady(true));
    return () => {
      alive = false;
    };
  }, []);
  return useMemo<Measure>(() => {
    if (!ready) return estimateWidth;
    const context = document.createElement("canvas").getContext("2d");
    if (!context) return estimateWidth;
    return (text, font, size) => {
      context.font = `${size}px ${FONTS[font]}`;
      return context.measureText(text).width;
    };
  }, [ready]);
}

/** An outline drawn behind the letters, like the renderer's ASS border. */
function outlined(width: number, color: string): CSSProperties {
  return {
    WebkitTextStroke: `${width * 2}px ${color}`,
    paintOrder: "stroke fill",
  };
}

/** Title and captions drawn over the base video (D-057), on a 1080x1920 layer scaled to the
 * player, at the positions the renderer uses. Words show uncensored here; the rendered
 * preview adds the stars and beeps. */
export function LiveOverlay({
  video,
  live,
  title,
  words,
  style,
  time,
  duration,
}: {
  video: RefObject<HTMLVideoElement | null>;
  live: NonNullable<Clip["live"]>;
  title: string;
  words: Word[];
  style: CaptionStyle;
  time: number;
  duration: number;
}) {
  const rect = useContentRect(video);
  const measure = useMeasure();
  const chunks = useMemo(
    () => chunkWords(words, style === "pop" ? CAPTION.popMaxWords : CAPTION.maxWords, measure),
    [words, style, measure],
  );
  const titleFit = useMemo(() => fitTitle(title, measure), [title, measure]);
  if (!rect) return null;

  const frame = captionAt(chunks, time, duration);
  const showTitle = live.show_title && titleFit.lines.length > 0;

  return (
    <div
      className="pointer-events-none absolute overflow-hidden rounded-xl"
      style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }}
      aria-hidden
    >
      <div
        className="absolute top-0 left-0 origin-top-left"
        style={{
          width: CANVAS_W,
          height: CANVAS_H,
          transform: `scale(${rect.width / CANVAS_W})`,
          color: COLORS.text,
        }}
      >
        {showTitle && (
          <div
            className="absolute inset-x-0 text-center whitespace-pre"
            style={{
              top: live.title_y,
              transform: "translateY(-50%)",
              fontFamily: FONTS.title,
              fontSize: titleFit.size * EM_PER_ASS_SIZE.title,
              lineHeight: `${titleFit.size}px`,
              opacity: Math.min(1, time / TITLE.fadeS),
              textShadow: `${TITLE.shadow}px ${TITLE.shadow}px 0 rgba(0,0,0,${COLORS.shadowOpacity})`,
              ...outlined(TITLE.outline, COLORS.outline),
            }}
          >
            {titleFit.lines.join("\n")}
          </div>
        )}
        {frame && (
          <div
            className="absolute inset-x-0 flex justify-center"
            style={{ top: live.caption_y, transform: "translateY(-50%)" }}
          >
            <div
              // A new group remounts, so "pop" plays its scale-in once per group.
              key={frame.chunk}
              className="text-center"
              style={{
                fontFamily: FONTS.caption,
                fontSize: CAPTION.size * EM_PER_ASS_SIZE.caption,
                lineHeight: `${CAPTION.size}px`,
                animation: style === "pop" ? "kliprix-pop 120ms linear" : undefined,
              }}
            >
              {frame.lines.map((line, number) => (
                <div key={number} className="whitespace-nowrap">
                  {line.map((word, position) => (
                    <span key={word.index}>
                      {position > 0 && " "}
                      <span style={wordStyle(style, word.index, frame.active)}>{word.text}</span>
                    </span>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/** The renderer's three styles: pop and karaoke color the spoken word yellow (karaoke dims
 * the words still to come); boxed puts a thick blue border around it. */
function wordStyle(style: CaptionStyle, index: number, active: number): CSSProperties {
  const base = outlined(CAPTION.outline, COLORS.outline);
  if (style === "pop") return { ...base, color: index === active ? COLORS.active : COLORS.text };
  if (style === "karaoke") {
    if (index === active) return { ...base, color: COLORS.active };
    return { ...base, opacity: index > active ? COLORS.upcomingOpacity : 1 };
  }
  return index === active ? { ...outlined(COLORS.boxBorder, COLORS.box) } : base;
}
