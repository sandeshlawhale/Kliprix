"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Info, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { ACCENT_TOGGLE, SettingGroup, SettingRow } from "@/components/settings/setting-row";
import { StorageSection } from "@/components/settings/storage-section";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { keys, useDownloaderRelease, useSystem } from "@/hooks/queries";
import { useSyncedTheme } from "@/hooks/use-synced-theme";
import { errorMessage, updateDownloader, type Settings } from "@/lib/api";

/** Theme, storage and the downloader; system details live under About (D-067). */
export function GeneralPane() {
  const system = useSystem();
  const release = useDownloaderRelease();
  const updateAvailable = release.data?.update_available === true;
  const [theme, setTheme] = useSyncedTheme();
  const client = useQueryClient();
  const downloader = useMutation({
    mutationFn: updateDownloader,
    onSuccess: (result) => {
      void client.invalidateQueries({ queryKey: keys.system });
      void client.invalidateQueries({ queryKey: keys.downloaderRelease });
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
            <ToggleGroupItem value="light" className={ACCENT_TOGGLE}>
              Light
            </ToggleGroupItem>
            <ToggleGroupItem value="dark" className={ACCENT_TOGGLE}>
              Dark
            </ToggleGroupItem>
            <ToggleGroupItem value="system" className={ACCENT_TOGGLE}>
              System
            </ToggleGroupItem>
          </ToggleGroup>
        </SettingRow>
      </SettingGroup>

      <StorageSection />

      <SettingGroup title="Downloader">
        <SettingRow
          label={
            <span className="flex items-center gap-1.5">
              yt-dlp
              {updateAvailable && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      aria-label="What the orange dot means"
                      className="text-muted-foreground hover:text-foreground inline-flex"
                    >
                      <Info className="size-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-64">
                    The dot means yt-dlp {release.data?.latest} is out. YouTube changes often;
                    updating keeps downloads working. Restart Kliprix after the update.
                  </TooltipContent>
                </Tooltip>
              )}
            </span>
          }
          hint={
            <>
              <span className="font-mono">{system.data?.ytdlp_version ?? "not installed"}</span>
              {updateAvailable ? (
                <>
                  {" "}
                  · <span className="text-brand">{release.data?.latest} available</span>
                </>
              ) : (
                " · update when downloads fail"
              )}
            </>
          }
        >
          <Button
            variant="outline"
            size="sm"
            className="relative"
            onClick={() => downloader.mutate()}
            disabled={downloader.isPending}
            aria-label={updateAvailable ? "Update yt-dlp (update available)" : undefined}
          >
            {downloader.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Update
            {updateAvailable && !downloader.isPending && (
              <span
                aria-hidden
                className="bg-brand ring-popover absolute -top-1 -right-1 size-2.5 rounded-full ring-2"
              />
            )}
          </Button>
        </SettingRow>
      </SettingGroup>
    </div>
  );
}
