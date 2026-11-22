"use client";

import { HardDrive } from "lucide-react";
import Link from "next/link";

import { useStorage } from "@/hooks/queries";
import { bytes } from "@/lib/format";

/** Shown on every page while the data folder is larger than the warning size (docs/02 E). */
export function StorageBanner() {
  const storage = useStorage();
  if (!storage.data?.warn) return null;
  return (
    <div
      role="status"
      className="text-muted-foreground bg-muted/40 flex w-fit items-center gap-2 rounded-full border px-3 py-1 text-xs"
    >
      <HardDrive className="size-3.5 shrink-0" aria-hidden />
      <span>
        Data uses{" "}
        <span className="text-foreground font-medium">{bytes(storage.data.total_bytes)}</span>,
        above your warning size.
      </span>
      <Link
        href="/settings#storage"
        className="text-foreground font-medium underline-offset-4 hover:underline"
      >
        Clean up
      </Link>
    </div>
  );
}
