"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";

import { SettingGroup, SettingRow } from "@/components/settings/setting-row";
import { SettingsLink } from "@/components/settings/settings-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useSystem } from "@/hooks/queries";
import { errorMessage, getHealth } from "@/lib/api";
import { REPO_URL } from "@/lib/links";

const VALUE = "text-muted-foreground font-mono text-xs";
const LINK =
  "text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm transition-colors";

/** Version, this computer's setup and links (moved out of Settings, D-067). */
export function AboutPane() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const system = useSystem();
  const data = system.data;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-4 py-2">
        <div className="bg-brand text-brand-foreground flex size-12 items-center justify-center rounded-xl text-xl font-bold">
          K
        </div>
        <div className="flex flex-col">
          <span className="text-lg font-semibold">Kliprix</span>
          <span className="text-muted-foreground text-sm">
            <span className="font-mono">{health.data?.version ?? "…"}</span> · MIT license · runs
            only on this computer
          </span>
        </div>
      </div>

      <SettingGroup title="This computer">
        {system.isPending && <Skeleton className="my-3 h-40 w-full" />}
        {system.isError && (
          <p className="text-destructive py-3 text-sm">{errorMessage(system.error)}</p>
        )}
        {data && (
          <>
            <SettingRow label="FFmpeg" className="min-h-11 py-2">
              {data.ffmpeg.found ? (
                <span className={VALUE}>
                  {data.ffmpeg.version}
                  {!data.ffmpeg.libass && (
                    <span className="text-destructive font-sans"> · no libass, no captions</span>
                  )}
                </span>
              ) : (
                <span className="text-destructive text-xs">not found</span>
              )}
            </SettingRow>
            <SettingRow label="yt-dlp" className="min-h-11 py-2">
              <span className={VALUE}>{data.ytdlp_version ?? "not installed"}</span>
            </SettingRow>
            <SettingRow label="Python" className="min-h-11 py-2">
              <span className={VALUE}>{data.python}</span>
            </SettingRow>
            <SettingRow label="Memory" className="min-h-11 py-2">
              <span className={VALUE}>{data.ram_gb} GB</span>
            </SettingRow>
            <SettingRow label="NVIDIA GPU" className="min-h-11 py-2">
              <span className={VALUE}>{data.gpu ? "yes" : "no"}</span>
            </SettingRow>
            <SettingRow label="Free disk" className="min-h-11 py-2">
              <span className={VALUE}>{data.disk_free_gb} GB</span>
            </SettingRow>
          </>
        )}
      </SettingGroup>

      <SettingGroup title="Links">
        <SettingRow label="How to use" className="min-h-11 py-2">
          <SettingsLink tab="help" className={LINK}>
            Open
          </SettingsLink>
        </SettingRow>
        <SettingRow label="Source code" className="min-h-11 py-2">
          <a href={REPO_URL} target="_blank" rel="noreferrer" className={LINK}>
            GitHub <ArrowUpRight className="size-3.5" aria-hidden />
          </a>
        </SettingRow>
        <SettingRow label="Third-party notices" className="min-h-11 py-2">
          <a
            href={`${REPO_URL}/blob/main/THIRD_PARTY_NOTICES.md`}
            target="_blank"
            rel="noreferrer"
            className={LINK}
          >
            View <ArrowUpRight className="size-3.5" aria-hidden />
          </a>
        </SettingRow>
      </SettingGroup>

      <p className="text-muted-foreground text-xs leading-relaxed">
        You are responsible for having the rights to the videos you clip and for following each
        platform&apos;s terms.
      </p>
    </div>
  );
}
