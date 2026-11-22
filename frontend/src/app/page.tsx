"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowRight, Link2, Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore, type ClipboardEvent, type FormEvent } from "react";

import { VideoCard, VideoCardSkeleton } from "@/components/home/video-card";
import { Button } from "@/components/ui/button";
import { keys, useLlmSession, useSettings, useSystem } from "@/hooks/queries";
import { createBatch, errorMessage, inspectVideos } from "@/lib/api";
import { styleSummary } from "@/lib/format";
import { getDraft, getServerDraft, setDraft, subscribeDraft } from "@/lib/draft";
import { isSupportedLink } from "@/lib/links";
import { cn } from "@/lib/utils";

const MAX_VIDEOS = 3;

export default function HomePage() {
  const router = useRouter();
  const client = useQueryClient();
  const system = useSystem();
  const settings = useSettings();
  const llm = useLlmSession();
  const [link, setLink] = useState("");
  const items = useSyncExternalStore(subscribeDraft, getDraft, getServerDraft);
  const [inputError, setInputError] = useState<string | null>(null);
  const update = setDraft;

  const inspect = useMutation({
    mutationFn: (url: string) => inspectVideos([url]),
    onSuccess: (response, url) => {
      const result = response.results[0];
      if (!result?.ok || !result.video) {
        setInputError(result?.error?.message ?? "This link could not be read.");
        return;
      }
      const video = result.video;
      if (items.some((item) => item.video.source_id === video.source_id)) {
        setInputError("This video is already in the list.");
        return;
      }
      update([...items, { url, video }]);
      setLink("");
    },
    onError: (error) => setInputError(errorMessage(error)),
  });

  const start = useMutation({
    mutationFn: () => createBatch(items.map((item) => item.url)),
    onSuccess: ({ id }) => {
      update([]);
      void client.invalidateQueries({ queryKey: keys.batches });
      router.push(`/history/${id}`);
    },
  });

  const full = items.length >= MAX_VIDEOS;
  const hosts = system.data?.supported_hosts ?? [];
  const configured = llm.data?.configured === true;

  const addLink = (raw: string) => {
    const url = raw.trim();
    setInputError(null);
    if (!url || full || inspect.isPending) return;
    if (hosts.length > 0 && !isSupportedLink(url, hosts)) {
      setInputError(`Only links from ${hosts.join(", ")} are supported.`);
      return;
    }
    inspect.mutate(url);
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    addLink(link);
  };

  // A pasted link is read right away, so the video shows up without an extra click.
  const onPaste = (event: ClipboardEvent<HTMLInputElement>) => {
    if (link.trim()) return;
    const pasted = event.clipboardData.getData("text").trim();
    if (!pasted) return;
    event.preventDefault();
    setLink(pasted);
    addLink(pasted);
  };

  return (
    <div className="flex min-h-[calc(100svh-8.5rem)] flex-col items-center justify-center">
      <div className="flex w-full max-w-3xl flex-col items-center gap-10">
        <header className="flex flex-col items-center gap-4 text-center">
          <h1 className="text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
            Long videos in.
            <br />
            <span className="text-brand">Shorts worth posting</span> out.
          </h1>
          <p className="text-muted-foreground max-w-xl text-lg text-balance">
            Paste up to 3 links. Kliprix finds the best moments, frames the speaker and captions
            every word, right on your computer.
          </p>
        </header>

        <div className="flex w-full flex-col gap-3">
          <form
            onSubmit={onSubmit}
            className={cn(
              "bg-card w-full rounded-2xl border shadow-sm transition-shadow",
              "focus-within:border-ring focus-within:ring-ring/30 focus-within:ring-3",
              inputError && "border-destructive/60",
            )}
          >
            <div className="flex items-center gap-3 px-5 pt-4">
              <Link2 className="text-muted-foreground size-5 shrink-0" aria-hidden />
              <label htmlFor="video-link" className="sr-only">
                Video link
              </label>
              <input
                id="video-link"
                className="placeholder:text-muted-foreground h-11 w-full min-w-0 bg-transparent text-base outline-none disabled:cursor-not-allowed disabled:opacity-60"
                placeholder={full ? "3 of 3 videos added" : "Paste a video link"}
                value={link}
                onChange={(event) => setLink(event.target.value)}
                onPaste={onPaste}
                disabled={full || inspect.isPending}
                aria-invalid={inputError !== null}
                aria-describedby={inputError ? "link-error" : undefined}
                autoComplete="off"
                autoFocus
              />
              {inspect.isPending && (
                <Loader2
                  className="text-muted-foreground size-4 shrink-0 animate-spin"
                  aria-hidden
                />
              )}
            </div>

            {inputError && (
              <p id="link-error" role="alert" className="text-destructive px-5 pt-1 text-sm">
                {inputError}
              </p>
            )}

            {(items.length > 0 || inspect.isPending) && (
              <section aria-label="Videos" className="grid gap-2 px-3 pt-3">
                {items.map((item) => (
                  <VideoCard
                    key={item.video.source_id}
                    video={item.video}
                    onRemove={() => update(items.filter((other) => other !== item))}
                  />
                ))}
                {inspect.isPending && <VideoCardSkeleton />}
              </section>
            )}

            <div className="flex items-center gap-2 px-3 pt-4 pb-3">
              <span className="text-muted-foreground px-2 font-mono text-xs tabular-nums">
                {items.length}/{MAX_VIDEOS}
              </span>
              <div className="ml-auto flex items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  asChild
                  title={settings.data ? styleSummary(settings.data.style_defaults) : undefined}
                >
                  <Link href="/style">Edit style</Link>
                </Button>
                <Button
                  type="button"
                  variant="brand"
                  disabled={
                    items.length === 0 || !configured || inspect.isPending || start.isPending
                  }
                  onClick={() => start.mutate()}
                >
                  {start.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null}
                  Process
                </Button>
              </div>
            </div>
          </form>

          <div className="flex min-h-5 flex-col items-center gap-1 text-center text-sm">
            {llm.isSuccess && !configured && (
              <p className="text-muted-foreground flex items-center gap-1.5">
                <AlertCircle className="size-3.5" aria-hidden />
                API is not configured.
                <Link
                  href="/configure-api"
                  className="text-foreground inline-flex items-center gap-0.5 font-medium underline-offset-4 hover:underline"
                >
                  Configure API
                  <ArrowRight className="size-3.5" aria-hidden />
                </Link>
              </p>
            )}
            {llm.isError && (
              <p role="alert" className="text-destructive">
                {errorMessage(llm.error)}
              </p>
            )}
            {settings.isError && (
              <p role="alert" className="text-destructive">
                {errorMessage(settings.error)}
              </p>
            )}
            {start.isError && (
              <p role="alert" className="text-destructive">
                {errorMessage(start.error)}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
