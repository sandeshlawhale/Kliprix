"use client";

import { HardDrive } from "lucide-react";
import Link from "next/link";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useStorage } from "@/hooks/queries";
import { bytes } from "@/lib/format";

/** Shown on every page while the data folder is larger than the warning size (docs/02 E). */
export function StorageBanner() {
  const storage = useStorage();
  if (!storage.data?.warn) return null;
  return (
    <Alert className="mb-6">
      <HardDrive aria-hidden />
      <AlertDescription className="flex flex-wrap items-center gap-3">
        Kliprix data uses {bytes(storage.data.total_bytes)}, more than your warning size.
        <Button size="sm" variant="outline" asChild>
          <Link href="/settings#storage">Clean up</Link>
        </Button>
      </AlertDescription>
    </Alert>
  );
}
