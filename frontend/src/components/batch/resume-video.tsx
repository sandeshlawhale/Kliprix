"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCw } from "lucide-react";
import { SettingsLink } from "@/components/settings/settings-dialog";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { invalidateBatch, useLlmSession } from "@/hooks/queries";
import { errorMessage, retryVideo, type Video } from "@/lib/api";

/** "Resume" for a failed video (P-001): runs it again from the step that failed. Every
 * finished step (download, analysis, transcript, picks) is reused, nothing is repeated. */
export function ResumeVideo({ video, batchId }: { video: Video; batchId: string }) {
  const client = useQueryClient();
  const session = useLlmSession();
  const resume = useMutation({
    mutationFn: () => retryVideo(video.id),
    onSuccess: () => {
      invalidateBatch(client, batchId);
      toast.success("Resuming from the step that failed");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const failedStep = video.checkpoints.find((step) => step.state === "failed")?.label;
  const noModel = session.data?.configured === false;

  return (
    <div className="mt-2 flex flex-wrap items-center gap-3">
      <Button
        size="sm"
        variant="outline"
        onClick={() => resume.mutate()}
        disabled={resume.isPending || noModel}
      >
        {resume.isPending ? (
          <Loader2 className="animate-spin" aria-hidden />
        ) : (
          <RotateCw aria-hidden />
        )}
        {failedStep ? `Resume from ${failedStep}` : "Resume"}
      </Button>
      {noModel ? (
        <span className="text-muted-foreground text-xs">
          <SettingsLink tab="api" className="underline underline-offset-2">
            Configure the AI model
          </SettingsLink>{" "}
          first.
        </span>
      ) : (
        <span className="text-muted-foreground text-xs">
          Finished steps are kept; nothing is downloaded again.
        </span>
      )}
    </div>
  );
}
