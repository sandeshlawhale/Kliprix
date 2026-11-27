"use client";

import { useTheme } from "next-themes";
import { useEffect } from "react";
import { toast } from "sonner";

import { useSettings, useUpdateSettings } from "@/hooks/queries";
import { errorMessage, type Settings } from "@/lib/api";

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
