"use client";

import { BookOpen, KeyRound, Palette, Settings as SettingsIcon } from "lucide-react";
import Link from "next/link";
import { useTheme } from "next-themes";
import { useEffect } from "react";
import { toast } from "sonner";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarMenuButton } from "@/components/ui/sidebar";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useSettings, useUpdateSettings } from "@/hooks/queries";
import { errorMessage, type Settings } from "@/lib/api";

const DOCS_URL = "https://github.com/sandeshlawhale/Kliprix#readme";
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

export function AccountMenu() {
  const [theme, setTheme] = useSyncedTheme();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <SidebarMenuButton className="h-11">
          <span
            aria-hidden
            className="bg-muted text-muted-foreground flex size-7 items-center justify-center rounded-full text-xs font-medium"
          >
            L
          </span>
          <span>Local workspace</span>
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
        <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
          Theme
        </DropdownMenuLabel>
        <div className="px-2 pb-2">
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={theme ?? ""}
            onValueChange={(value) => value && setTheme(value as Theme)}
            aria-label="Theme"
            className="w-full"
          >
            <ToggleGroupItem value="light" className="flex-1">
              Light
            </ToggleGroupItem>
            <ToggleGroupItem value="dark" className="flex-1">
              Dark
            </ToggleGroupItem>
            <ToggleGroupItem value="system" className="flex-1">
              System
            </ToggleGroupItem>
          </ToggleGroup>
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <a href={DOCS_URL} target="_blank" rel="noreferrer">
            <BookOpen aria-hidden /> Documentation
          </a>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
