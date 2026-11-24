"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { invalidateBatch, keys } from "@/hooks/queries";
import { errorMessage, patchClip, type Clip, type ClipPatch } from "@/lib/api";
import { TITLE_MAX } from "@/lib/clip-edit";

// The LinkedIn post is still generated and stored, but no longer shown (D-055).
type Field = "title" | "youtube_title" | "youtube_description" | "hashtags" | "reason";

const FIELDS: { key: Field; label: string; multiline: boolean; note?: string }[] = [
  {
    key: "title",
    label: "On-video title",
    multiline: false,
    note: "Shown on the video; changing it needs a new preview.",
  },
  { key: "youtube_title", label: "YouTube title", multiline: false },
  { key: "youtube_description", label: "YouTube description", multiline: true },
  { key: "hashtags", label: "Hashtags", multiline: false },
  { key: "reason", label: "Why this moment", multiline: true },
];

function valueOf(clip: Clip, key: Field): string {
  return key === "hashtags" ? clip.metadata.hashtags.join(" ") : clip.metadata[key];
}

function patchOf(key: Field, value: string): ClipPatch {
  if (key === "hashtags") return { hashtags: value.split(/\s+/).filter(Boolean) };
  return { [key]: value };
}

/** Metadata fields, each with a copy icon in its top-right corner that shows on hover
 * (D-055). Text saves when a field loses focus (docs/02 C step 3). */
export function MetadataPanel({ clip }: { clip: Clip }) {
  const client = useQueryClient();
  const [drafts, setDrafts] = useState<Partial<Record<Field, string>>>({});
  const save = useMutation({
    mutationFn: ({ patch }: { key: Field; patch: ClipPatch }) => patchClip(clip.id, patch),
    onSuccess: (updated, { key }) => {
      client.setQueryData(keys.clip(clip.id), updated);
      invalidateBatch(client, clip.batch_id);
      setDrafts((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      toast.success("Saved");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <div className="flex flex-col gap-5">
      {FIELDS.map(({ key, label, multiline, note }) => {
        const value = drafts[key] ?? valueOf(clip, key);
        const commit = () => {
          if (drafts[key] === undefined || drafts[key] === valueOf(clip, key)) return;
          save.mutate({ key, patch: patchOf(key, drafts[key] ?? "") });
        };
        const id = `meta-${key}`;
        const props = {
          id,
          value,
          onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
            setDrafts((current) => ({ ...current, [key]: event.target.value })),
          onBlur: commit,
        };
        return (
          <div key={key} className="flex flex-col gap-2">
            <Label htmlFor={id}>{label}</Label>
            <div className="group relative">
              {multiline ? (
                <Textarea rows={3} className="pr-10" {...props} />
              ) : (
                <Input
                  maxLength={key === "title" ? TITLE_MAX : undefined}
                  className="pr-10"
                  {...props}
                />
              )}
              <CopyButton label={label} value={value} />
            </div>
            {key === "title" && (
              <span className="text-muted-foreground text-xs">
                {value.length}/{TITLE_MAX} · {note}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function CopyButton({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={`Copy ${label}`}
      title="Copy"
      // Shown on hover or keyboard focus of the field; never takes focus from typing.
      className="text-muted-foreground hover:text-foreground absolute top-1 right-1 size-7 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 focus-visible:opacity-100"
      onMouseDown={(event) => event.preventDefault()}
      onClick={() =>
        navigator.clipboard.writeText(value).then(
          () => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
            toast.success(`${label} copied`);
          },
          () => toast.error("Copy failed"),
        )
      }
    >
      {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
    </Button>
  );
}
