"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { useState, useSyncExternalStore, type ReactNode } from "react";

import { SettingsDialogProvider } from "@/components/settings/settings-dialog";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

const noSubscription = () => () => undefined;

/** True only in the browser. Every page reads live data from the local backend through
 * the browser, so the shell renders client-side only; this avoids hydration mismatches when
 * a query resolves before its part of the page hydrates. */
function useMounted(): boolean {
  return useSyncExternalStore(
    noSubscription,
    () => true,
    () => false,
  );
}

export function Providers({ children }: { children: ReactNode }) {
  const mounted = useMounted();
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 2000 } },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
        <TooltipProvider>
          {mounted ? <SettingsDialogProvider>{children}</SettingsDialogProvider> : null}
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
