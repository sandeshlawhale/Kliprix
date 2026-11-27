"use client";

import {
  BookOpen,
  CircleHelp,
  Info,
  KeyRound,
  Moon,
  Palette,
  Settings as SettingsIcon,
  Sun,
} from "lucide-react";
import { useTheme } from "next-themes";

import { useOpenSettings } from "@/components/settings/settings-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarMenuButton } from "@/components/ui/sidebar";
import { useSyncedTheme } from "@/hooks/use-synced-theme";
import { REPO_URL } from "@/lib/links";

const DOCS_URL = `${REPO_URL}#readme`;

/** One click flips to the opposite of what is shown now (System by default). */
function ThemeToggle() {
  const [, setTheme] = useSyncedTheme();
  const { resolvedTheme } = useTheme();
  const dark = resolvedTheme === "dark";
  return (
    <Button
      variant="ghost"
      size="icon"
      className="shrink-0"
      onClick={() => setTheme(dark ? "light" : "dark")}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "Light theme" : "Dark theme"}
    >
      {dark ? <Sun aria-hidden /> : <Moon aria-hidden />}
    </Button>
  );
}

export function AccountMenu() {
  const openSettings = useOpenSettings();
  return (
    <div className="flex items-center gap-1">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <SidebarMenuButton className="h-11 min-w-0 flex-1">
            <span
              aria-hidden
              className="bg-muted text-muted-foreground flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-medium"
            >
              L
            </span>
            <span className="truncate">Local workspace</span>
          </SidebarMenuButton>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="start" className="w-60">
          <DropdownMenuItem onSelect={() => openSettings("style")}>
            <Palette aria-hidden /> Edit video style
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => openSettings("api")}>
            <KeyRound aria-hidden /> Configure API
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => openSettings("settings")}>
            <SettingsIcon aria-hidden /> Settings
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => openSettings("about")}>
            <Info aria-hidden /> About
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => openSettings("help")}>
            <CircleHelp aria-hidden /> How to use
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <a href={DOCS_URL} target="_blank" rel="noreferrer">
              <BookOpen aria-hidden /> Documentation
            </a>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ThemeToggle />
    </div>
  );
}
