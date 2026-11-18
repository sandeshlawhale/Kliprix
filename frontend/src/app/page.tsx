"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore, type FormEvent } from "react";

import { VideoCard, VideoCardSkeleton } from "@/components/home/video-card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { keys, useLlmSession, useSettings, useSystem } from "@/hooks/queries";
import { createBatch, errorMessage, inspectVideos } from "@/lib/api";
import { styleSummary } from "@/lib/format";
import { getDraft, getServerDraft, setDraft, subscribeDraft } from "@/lib/draft";
import { isSupportedLink } from "@/lib/links";

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

  const add = (event: FormEvent) => {
    event.preventDefault();
    const url = link.trim();
    setInputError(null);
    if (!url || full) return;
    if (hosts.length > 0 && !isSupportedLink(url, hosts)) {
      setInputError(`Only links from ${hosts.join(", ")} are supported.`);
      return;
    }
    inspect.mutate(url);
  };

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Turn videos into shorts</h1>
        <p className="text-muted-foreground">Paste up to 3 links.</p>
      </header>

      <form onSubmit={add} className="flex flex-col gap-2">
        <label htmlFor="video-link" className="sr-only">
          Video link
        </label>
        <div className="flex gap-2">
          <Input
            id="video-link"
            placeholder={full ? "3/3 videos added" : "Paste a video link"}
            value={link}
            onChange={(event) => setLink(event.target.value)}
            disabled={full || inspect.isPending}
            aria-invalid={inputError !== null}
            aria-describedby={inputError ? "link-error" : undefined}
            autoComplete="off"
          />
          <Button type="submit" disabled={full || inspect.isPending || !link.trim()}>
            {inspect.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null}
            Add
          </Button>
          <span className="text-muted-foreground self-center font-mono text-sm tabular-nums">
            {items.length}/{MAX_VIDEOS}
          </span>
        </div>
        {inputError && (
          <p id="link-error" role="alert" className="text-destructive text-sm">
            {inputError}
          </p>
        )}
      </form>

      {(items.length > 0 || inspect.isPending) && (
        <section aria-label="Videos" className="grid gap-3">
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

      <section className="flex flex-wrap items-center gap-3">
        <Button variant="outline" asChild>
          <Link href="/style">Edit video style</Link>
        </Button>
        {settings.data && (
          <span className="text-muted-foreground text-sm">
            {styleSummary(settings.data.style_defaults)}
          </span>
        )}
        {settings.isError && (
          <span className="text-destructive text-sm">{errorMessage(settings.error)}</span>
        )}
      </section>

      {llm.isSuccess && !configured && (
        <Alert>
          <AlertCircle aria-hidden />
          <AlertTitle>The AI model is not configured</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center gap-3">
            Clips are picked by your own LLM. Add a provider and key first.
            <Button size="sm" asChild>
              <Link href="/configure-api">Configure API</Link>
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {llm.isError && (
        <p role="alert" className="text-destructive text-sm">
          {errorMessage(llm.error)}
        </p>
      )}

      <div className="flex flex-col items-start gap-2">
        <Button
          size="lg"
          disabled={items.length === 0 || !configured || start.isPending}
          onClick={() => start.mutate()}
        >
          {start.isPending ? <Loader2 className="animate-spin" aria-hidden /> : null}
          Start
        </Button>
        {start.isError && (
          <p role="alert" className="text-destructive text-sm">
            {errorMessage(start.error)}
          </p>
        )}
      </div>
    </div>
  );
}
