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
import Link from "next/link";
import { useTheme } from "next-themes";
import { useEffect } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarMenuButton } from "@/components/ui/sidebar";
import { useSettings, useUpdateSettings } from "@/hooks/queries";
import { errorMessage, type Settings } from "@/lib/api";
import { REPO_URL } from "@/lib/links";

const DOCS_URL = `${REPO_URL}#readme`;
type Theme = Settings["theme"];

/** Keeps next-themes in step with the theme saved in backend settings. */
export function useSyncedTheme(): [Theme | undefined, (theme: Theme) => void] {
  const { setTheme } = useTheme();
  const settings = useSettings();
  const update = useUpdateSettings();
  const saved = settings.data?.theme;
  useEffect(() => {
    if (saved) setTheme(saved);
  }, [saved, setTheme]);
  const change = (theme: Theme) => {
    setTheme(theme);
    update.mutate({ theme }, { onError: (error) => toast.error(errorMessage(error)) });
  };
  return [saved, change];
}

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
          <DropdownMenuItem asChild>
            <Link href="/style">
              <Palette aria-hidden /> Edit video style
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link href="/configure-api">
              <KeyRound aria-hidden /> Configure API
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link href="/settings">
              <SettingsIcon aria-hidden /> Settings
            </Link>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem asChild>
            <Link href="/about">
              <Info aria-hidden /> About
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link href="/how-to-use">
              <CircleHelp aria-hidden /> How to use
            </Link>
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
