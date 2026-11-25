"use client";

import { Info, Keyboard } from "lucide-react";
import type { ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** A small info icon; the help text shows on hover or focus (D-063: hints stay out of the
 * way of the editor). */
export function Hint({
  children,
  label = "Help",
  className,
}: {
  children: ReactNode;
  label?: string;
  className?: string;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={label}
          className={cn(
            "text-muted-foreground hover:text-foreground inline-flex size-5 shrink-0 items-center justify-center rounded-sm",
            className,
          )}
        >
          <Info className="size-3.5" aria-hidden />
        </button>
      </TooltipTrigger>
      <TooltipContent className="block max-w-64 leading-snug">{children}</TooltipContent>
    </Tooltip>
  );
}

/** A key cap, e.g. <Kbd>S</Kbd>. */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="bg-background/20 inline-flex min-w-5 items-center justify-center rounded border border-current/30 px-1 font-mono text-[11px] leading-4">
      {children}
    </kbd>
  );
}

const SHORTCUTS: [ReactNode, string][] = [
  [<Kbd key="s">S</Kbd>, "Split at playhead"],
  [<Kbd key="del">Del</Kbd>, "Delete selected"],
  [
    <span key="arrows" className="inline-flex gap-0.5">
      <Kbd>←</Kbd>
      <Kbd>→</Kbd>
    </span>,
    "Previous / next word",
  ],
  [<span key="drag">Drag ends</span>, "Shorten / extend"],
];

/** The timeline's shortcuts behind a keyboard icon. */
export function ShortcutsHint() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label="Keyboard shortcuts"
          className="text-muted-foreground hover:text-foreground inline-flex size-7 items-center justify-center rounded-md"
        >
          <Keyboard className="size-4" aria-hidden />
        </button>
      </TooltipTrigger>
      <TooltipContent className="block">
        <dl className="grid grid-cols-[auto_auto] items-center gap-x-3 gap-y-1">
          {SHORTCUTS.map(([keys, action]) => (
            <div key={action} className="contents">
              <dt className="justify-self-end">{keys}</dt>
              <dd>{action}</dd>
            </div>
          ))}
        </dl>
      </TooltipContent>
    </Tooltip>
  );
}
