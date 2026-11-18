"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { invalidateBatch, keys } from "@/hooks/queries";
import { errorMessage, patchClip, type Clip, type ClipPatch } from "@/lib/api";
import { TITLE_MAX } from "@/lib/clip-edit";

type Field =
  "title" | "youtube_title" | "youtube_description" | "hashtags" | "linkedin_post" | "reason";

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
  { key: "linkedin_post", label: "LinkedIn post", multiline: true },
  { key: "reason", label: "Why this moment", multiline: true },
];

function valueOf(clip: Clip, key: Field): string {
  return key === "hashtags" ? clip.metadata.hashtags.join(" ") : clip.metadata[key];
}

function patchOf(key: Field, value: string): ClipPatch {
  if (key === "hashtags") return { hashtags: value.split(/\s+/).filter(Boolean) };
  return { [key]: value };
}

/** Metadata with Copy buttons. Text saves when a field loses focus (docs/02 C step 3). */
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
            <div className="flex items-center justify-between">
              <Label htmlFor={id}>{label}</Label>
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  navigator.clipboard.writeText(value).then(
                    () => toast.success(`${label} copied`),
                    () => toast.error("Copy failed"),
                  )
                }
              >
                <Copy aria-hidden /> Copy
              </Button>
            </div>
            {multiline ? (
              <Textarea rows={key === "linkedin_post" ? 5 : 3} {...props} />
            ) : (
              <Input maxLength={key === "title" ? TITLE_MAX : undefined} {...props} />
            )}
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
