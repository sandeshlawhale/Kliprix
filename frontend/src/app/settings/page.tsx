"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { useSyncedTheme } from "@/components/layout/account-menu";
import { StorageSection } from "@/components/settings/storage-section";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { keys, useSettings, useSystem, useUpdateSettings } from "@/hooks/queries";
import { errorMessage, getHealth, updateDownloader, type Settings } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

const WHISPER = ["auto", "tiny", "base", "small", "medium"] as const;

export default function SettingsPage() {
  const settings = useSettings();
  const system = useSystem();
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const update = useUpdateSettings();
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

  const save = (change: Partial<Settings>) =>
    update.mutate(change, { onError: (error) => toast.error(errorMessage(error)) });

  return (
    <div className="flex max-w-3xl flex-col gap-8">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>

      <Section title="Appearance">
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
      </Section>

      <Separator />
      <StorageSection />
      <Separator />

      <Section title="Transcription">
        {settings.isPending || system.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : settings.isError ? (
          <p className="text-destructive text-sm">{errorMessage(settings.error)}</p>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="whisper">Whisper model</Label>
              <Select
                value={settings.data.whisper_model}
                onValueChange={(value) =>
                  save({ whisper_model: value as Settings["whisper_model"] })
                }
              >
                <SelectTrigger id="whisper" className="w-60">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {WHISPER.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name === "auto" ? "Auto" : name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {system.data && (
                <p className="text-muted-foreground text-sm">
                  Detected {system.data.ram_gb} GB RAM{system.data.gpu ? " and an NVIDIA GPU" : ""};
                  Auto uses <span className="font-mono">{system.data.whisper_auto_model}</span>.
                </p>
              )}
            </div>
            <div className="flex items-center gap-3">
              <Switch
                id="thorough"
                checked={settings.data.thorough_mode}
                onCheckedChange={(value) => save({ thorough_mode: value })}
              />
              <Label htmlFor="thorough">
                Thorough mode: transcribe whole videos (slower on long streams)
              </Label>
            </div>
          </div>
        )}
      </Section>

      <Separator />

      <Section title="Downloader">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm">
            yt-dlp{" "}
            <span className="font-mono">{system.data?.ytdlp_version ?? "not installed"}</span>
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => downloader.mutate()}
            disabled={downloader.isPending}
          >
            {downloader.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Update downloader
          </Button>
        </div>
        <p className="text-muted-foreground text-sm">
          Update when YouTube downloads start failing. Restart Kliprix afterwards.
        </p>
      </Section>

      <Separator />

      <Section title="System check">
        {system.isPending && <Skeleton className="h-20 w-full" />}
        {system.isError && <p className="text-destructive text-sm">{errorMessage(system.error)}</p>}
        {system.data && (
          <dl className="grid grid-cols-[12rem_1fr] gap-y-1 text-sm">
            <dt className="text-muted-foreground">ffmpeg</dt>
            <dd>
              {system.data.ffmpeg.found ? (
                <span className="font-mono">{system.data.ffmpeg.version}</span>
              ) : (
                <span className="text-destructive">not found</span>
              )}
              {system.data.ffmpeg.found && !system.data.ffmpeg.libass && (
                <span className="text-destructive"> (no libass: captions cannot be drawn)</span>
              )}
            </dd>
            <dt className="text-muted-foreground">Python</dt>
            <dd className="font-mono">{system.data.python}</dd>
            <dt className="text-muted-foreground">RAM</dt>
            <dd>{system.data.ram_gb} GB</dd>
            <dt className="text-muted-foreground">NVIDIA GPU</dt>
            <dd>{system.data.gpu ? "yes" : "no"}</dd>
            <dt className="text-muted-foreground">Free disk</dt>
            <dd>{system.data.disk_free_gb} GB</dd>
          </dl>
        )}
      </Section>

      <Separator />

      <Section title="About">
        <p className="text-sm">
          Kliprix <span className="font-mono">{health.data?.version ?? ""}</span> · MIT license ·
          runs only on this computer.
        </p>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="font-medium">{title}</h2>
      {children}
    </section>
  );
}
