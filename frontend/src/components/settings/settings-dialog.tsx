"use client";

import { Info, KeyRound, Palette, Settings as SettingsIcon, XIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { AboutPane } from "@/components/settings/about-pane";
import { ApiPane } from "@/components/settings/api-pane";
import { GeneralPane } from "@/components/settings/general-pane";
import { StylePane } from "@/components/settings/style-pane";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export type SettingsTab = "style" | "api" | "settings" | "about";

const TABS: { id: SettingsTab; label: string; icon: typeof Palette }[] = [
  { id: "style", label: "Edit video style", icon: Palette },
  { id: "api", label: "Configure API", icon: KeyRound },
  { id: "settings", label: "Settings", icon: SettingsIcon },
  { id: "about", label: "About", icon: Info },
];

type OpenSettings = (tab?: SettingsTab) => void;
const SettingsContext = createContext<OpenSettings | null>(null);

/** Opens the settings dialog on a tab, over whatever page is showing (D-067). */
export function useOpenSettings(): OpenSettings {
  const open = useContext(SettingsContext);
  if (!open) throw new Error("useOpenSettings needs SettingsDialogProvider");
  return open;
}

export function SettingsDialogProvider({ children }: { children: React.ReactNode }) {
  const [tab, setTab] = useState<SettingsTab | null>(null);
  const open = useCallback<OpenSettings>((next = "settings") => setTab(next), []);
  const value = useMemo(() => open, [open]);
  return (
    <SettingsContext.Provider value={value}>
      {children}
      <SettingsDialog tab={tab} onTab={setTab} />
    </SettingsContext.Provider>
  );
}

/** For the old routes (/style, /configure-api, /settings, /about): go Home, open the tab. */
export function OpenSettingsRoute({ tab }: { tab: SettingsTab }) {
  const open = useOpenSettings();
  const router = useRouter();
  useEffect(() => {
    open(tab);
    router.replace("/");
  }, [open, router, tab]);
  return null;
}

/** A text button that opens the dialog on a tab; used where pages used to link to a route. */
export function SettingsLink({
  tab,
  className,
  children,
}: {
  tab: SettingsTab;
  className?: string;
  children: React.ReactNode;
}) {
  const open = useOpenSettings();
  return (
    <button type="button" className={cn("cursor-pointer", className)} onClick={() => open(tab)}>
      {children}
    </button>
  );
}

function SettingsDialog({
  tab,
  onTab,
}: {
  tab: SettingsTab | null;
  onTab: (tab: SettingsTab | null) => void;
}) {
  const current = TABS.find((item) => item.id === tab) ?? TABS[0]!;
  return (
    <Dialog open={tab !== null} onOpenChange={(open) => !open && onTab(null)}>
      <DialogContent
        showCloseButton={false}
        // Focus the panel itself, not the first tab, so no focus ring shows on open.
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          (event.currentTarget as HTMLElement).focus();
        }}
        className="flex h-[min(640px,calc(100vh-4rem))] w-[calc(100%-2rem)] max-w-[920px] gap-0 overflow-hidden p-0 sm:max-w-[920px]"
      >
        <nav
          aria-label="Settings sections"
          className="bg-sidebar/60 flex w-56 shrink-0 flex-col gap-1 border-r p-3"
        >
          <DialogTitle className="px-2 pt-1 pb-3 text-lg font-semibold">Settings</DialogTitle>
          <DialogDescription className="sr-only">
            Video style, AI provider, app settings and information about Kliprix.
          </DialogDescription>
          {TABS.map((item) => {
            const active = item.id === current.id;
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                aria-current={active ? "page" : undefined}
                onClick={() => onTab(item.id)}
                className={cn(
                  "flex h-9 items-center gap-2.5 rounded-lg px-2.5 text-left text-sm outline-none",
                  "focus-visible:ring-ring/50 transition-colors focus-visible:ring-[3px]",
                  active
                    ? "bg-accent text-accent-foreground font-medium"
                    : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                )}
              >
                <Icon className="size-4 shrink-0" aria-hidden />
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center justify-between gap-3 px-6 pt-2">
            <h2 className="text-base font-semibold">{current.label}</h2>
            <DialogClose asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Close settings">
                <XIcon />
              </Button>
            </DialogClose>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6 [scrollbar-color:var(--border)_transparent] [scrollbar-width:thin]">
            {current.id === "style" && <StylePane />}
            {current.id === "api" && <ApiPane />}
            {current.id === "settings" && <GeneralPane />}
            {current.id === "about" && <AboutPane />}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
