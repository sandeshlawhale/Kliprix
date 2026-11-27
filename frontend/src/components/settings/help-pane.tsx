"use client";

import { ChevronDown } from "lucide-react";

import { SettingGroup } from "@/components/settings/setting-row";
import { SettingsLink } from "@/components/settings/settings-dialog";

const LINK = "text-foreground font-medium underline-offset-4 hover:underline";

const STEPS: { title: string; text: React.ReactNode }[] = [
  {
    title: "Connect your AI",
    text: (
      <>
        In{" "}
        <SettingsLink tab="api" className={LINK}>
          Configure API
        </SettingsLink>
        , pick a provider, paste your key, choose a model and Save.
      </>
    ),
  },
  { title: "Paste links", text: "Add up to 3 YouTube links on Home." },
  {
    title: "Pick a style",
    text: (
      <>
        Optional:{" "}
        <SettingsLink tab="style" className={LINK}>
          Edit video style
        </SettingsLink>{" "}
        sets captions, clips per video and the title.
      </>
    ),
  },
  { title: "Process", text: "Press Process and follow the live steps on the batch page." },
  { title: "Post", text: "Open a clip, edit if needed, copy the text and Download." },
];

const TIPS = [
  "More clips finds extra moments in the same video, without downloading it again.",
  "Custom clip lets you pick any range of a downloaded video.",
  "In the editor: S splits, Delete removes a part, drag ends to trim.",
  "Videos with a chat replay and a facecam usually give the best clips.",
];

const FAQ: { q: string; a: React.ReactNode }[] = [
  {
    q: "“API is not configured”",
    a: (
      <>
        Set up a key in{" "}
        <SettingsLink tab="api" className={LINK}>
          Configure API
        </SettingsLink>
        . Save keeps it for next time.
      </>
    ),
  },
  {
    q: "Downloads keep failing",
    a: (
      <>
        YouTube changes often. Update yt-dlp in{" "}
        <SettingsLink tab="settings" className={LINK}>
          Settings
        </SettingsLink>
        , then restart Kliprix.
      </>
    ),
  },
  { q: "“No chat replay”", a: "Normal. Kliprix uses audio, motion and the transcript instead." },
  {
    q: "A video is private or removed",
    a: "That video fails with a message; the others keep going.",
  },
  { q: "AI or rate-limit error", a: "Check your key and quota with the provider, then retry." },
  {
    q: "A video failed halfway",
    a: "Use Resume on it. Finished steps are kept, nothing downloads again.",
  },
  {
    q: "Disk is full",
    a: (
      <>
        Free space under{" "}
        <SettingsLink tab="settings" className={LINK}>
          Settings
        </SettingsLink>{" "}
        → Storage.
      </>
    ),
  },
];

/** Short steps, tips and troubleshooting, as a dialog tab (D-067). */
export function HelpPane() {
  return (
    <div className="flex flex-col gap-6">
      <p className="text-muted-foreground text-sm">From a long video to shorts in five steps.</p>

      <SettingGroup title="Steps">
        {STEPS.map((step, index) => (
          <div key={step.title} className="flex min-h-14 items-center gap-4 py-3">
            <span
              aria-hidden
              className="border-brand/40 text-brand flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold tabular-nums"
            >
              {index + 1}
            </span>
            <div className="flex min-w-0 flex-col gap-0.5">
              <span className="text-sm font-medium">{step.title}</span>
              <span className="text-muted-foreground text-xs">{step.text}</span>
            </div>
          </div>
        ))}
      </SettingGroup>

      <SettingGroup title="Tips">
        {TIPS.map((tip) => (
          <p key={tip} className="text-muted-foreground flex min-h-11 items-center py-2 text-sm">
            {tip}
          </p>
        ))}
      </SettingGroup>

      <SettingGroup title="Troubleshooting">
        {FAQ.map((item) => (
          <details key={item.q} className="group">
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 py-2 text-sm font-medium [&::-webkit-details-marker]:hidden">
              {item.q}
              <ChevronDown
                className="text-muted-foreground size-4 shrink-0 transition-transform group-open:rotate-180"
                aria-hidden
              />
            </summary>
            <p className="text-muted-foreground pb-3 text-sm">{item.a}</p>
          </details>
        ))}
      </SettingGroup>
    </div>
  );
}
