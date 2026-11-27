"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { SettingGroup, SettingRow } from "@/components/settings/setting-row";
import { StorageSection } from "@/components/settings/storage-section";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { keys, useSystem } from "@/hooks/queries";
import { useSyncedTheme } from "@/hooks/use-synced-theme";
import { errorMessage, updateDownloader, type Settings } from "@/lib/api";

/** Theme, storage and the downloader; system details live under About (D-067). */
export function GeneralPane() {
  const system = useSystem();
  const [theme, setTheme] = useSyncedTheme();
  const client = useQueryClient();
  const downloader = useMutation({
    mutationFn: updateDownloader,
    onSuccess: (result) => {
      void client.invalidateQueries({ queryKey: keys.system });
      toast.success(
        result.before === result.after
          ? `yt-dlp is up to date (${result.after ?? "unknown"}).`
          : `yt-dlp updated ${result.before ?? "?"} → ${result.after ?? "?"}. Restart Kliprix to use it.`,
      );
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <div className="flex flex-col gap-6">
      <SettingGroup title="Appearance">
        <SettingRow label="Theme">
          <ToggleGroup
            type="single"
            variant="outline"
            value={theme ?? ""}
            onValueChange={(value) => value && setTheme(value as Settings["theme"])}
            aria-label="Theme"
          >
            <ToggleGroupItem value="light">Light</ToggleGroupItem>
            <ToggleGroupItem value="dark">Dark</ToggleGroupItem>
            <ToggleGroupItem value="system">System</ToggleGroupItem>
          </ToggleGroup>
        </SettingRow>
      </SettingGroup>

      <StorageSection />

      <SettingGroup title="Downloader">
        <SettingRow
          label="yt-dlp"
          hint={
            <>
              <span className="font-mono">{system.data?.ytdlp_version ?? "not installed"}</span> ·
              update when downloads fail
            </>
          }
        >
          <Button
            variant="outline"
            size="sm"
            onClick={() => downloader.mutate()}
            disabled={downloader.isPending}
          >
            {downloader.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Update
          </Button>
        </SettingRow>
      </SettingGroup>
    </div>
  );
}
