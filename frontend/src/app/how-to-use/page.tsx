import { ChevronDown } from "lucide-react";
import Image from "next/image";
import Link from "next/link";

import { InfoHeader, InfoList, InfoSection } from "@/components/info/info-section";
import { STYLE_LABELS } from "@/lib/format";

const LINK = "text-foreground underline underline-offset-4 hover:text-brand";

const STEPS: { title: string; text: React.ReactNode }[] = [
  {
    title: "Configure API",
    text: (
      <>
        Open the menu at the bottom of the sidebar →{" "}
        <Link href="/configure-api" className={LINK}>
          Configure API
        </Link>
        . Pick a provider, paste your key, Load models, pick a model, Test connection, then Save for
        this session.
      </>
    ),
  },
  {
    title: "Paste links",
    text: "Add up to 3 YouTube links on Home. Each one shows its title, length and whether a chat replay was found.",
  },
  {
    title: "Edit style (optional)",
    text: (
      <>
        <Link href="/style" className={LINK}>
          Edit style
        </Link>{" "}
        sets the caption style, clips per video (1–5) and whether the title shows on the video.
      </>
    ),
  },
  {
    title: "Process",
    text: "Press Process and follow the live steps: Download video, Fetch chat replay, Analyze audio and motion, Find highlights, Transcribe, Pick clips, Edit clips.",
  },
  {
    title: "Open a clip",
    text: "Watch it, copy its titles and post text, then Export and Download.",
  },
];

const FAQ: { q: string; a: string }[] = [
  {
    q: "“API is not configured”",
    a: "Set up your key in Configure API first. The key is kept only for this session, so enter it again after closing the tab.",
  },
  {
    q: "A video is private, age-restricted or removed",
    a: "That video fails with a clear message and the others keep going.",
  },
  {
    q: "“No chat replay”",
    a: "This is normal. Kliprix uses audio, motion and the transcript instead.",
  },
  {
    q: "Downloads keep failing",
    a: "YouTube changes often. Settings → Update downloader, then restart Kliprix.",
  },
  {
    q: "AI or rate-limit error",
    a: "Check your key and your quota with the provider, then try again.",
  },
  {
    q: "Disk is full",
    a: "Free space in Settings → Storage. Kliprix never deletes files on its own.",
  },
  {
    q: "The app closed in the middle of a job",
    a: "It picks up where it left off the next time it starts.",
  },
];

export default function HowToUsePage() {
  return (
    <div className="flex flex-col gap-10">
      <InfoHeader
        title="How to use"
        lead="From a long video to ready-to-post shorts in five steps."
      />

      <ol className="flex flex-col gap-3">
        {STEPS.map((step, index) => (
          <li key={step.title} className="flex gap-4 rounded-xl border p-4">
            <span
              aria-hidden
              className="bg-brand text-brand-foreground flex size-7 shrink-0 items-center justify-center rounded-full text-sm font-medium"
            >
              {index + 1}
            </span>
            <div className="flex flex-col gap-1">
              <h2 className="text-sm font-medium">{step.title}</h2>
              <p className="text-muted-foreground text-sm leading-relaxed">{step.text}</p>
            </div>
          </li>
        ))}
      </ol>

      <InfoSection title="Caption styles">
        <div className="grid grid-cols-3 gap-3">
          {(Object.keys(STYLE_LABELS) as (keyof typeof STYLE_LABELS)[]).map((name) => (
            <figure key={name} className="flex flex-col gap-1.5">
              {/* Real renders (public/style-samples), cropped to the caption band. */}
              <Image
                src={`/style-samples/${name}.png`}
                alt={`${STYLE_LABELS[name]} caption sample`}
                width={540}
                height={960}
                unoptimized
                className="aspect-[4/1] w-full rounded-lg border object-cover object-[50%_73%]"
              />
              <figcaption className="text-sm font-medium">{STYLE_LABELS[name]}</figcaption>
            </figure>
          ))}
        </div>
      </InfoSection>

      <div className="grid gap-10 sm:grid-cols-2">
        <InfoSection title="Editing a clip">
          <InfoList
            items={[
              "Edit: trim, caption style and caption words. Press Apply changes; the clip shows “Edited — preview needed”.",
              "Metadata: title, YouTube text, hashtags and LinkedIn post save as you leave the field.",
              "Preview is a fast low-res check. Export makes the full-quality file.",
              "Renders run one at a time, so a waiting render is not stuck.",
            ]}
          />
        </InfoSection>

        <InfoSection title="More clips">
          <InfoList
            items={[
              "On the batch page, More clips finds extra clips in the same video without downloading it again.",
              "New clips never repeat the ones you already have.",
            ]}
          />
        </InfoSection>

        <InfoSection title="History and storage">
          <InfoList
            items={[
              "Past batches stay in the sidebar, even after a restart.",
              "When you delete a batch, you choose whether its files go too.",
              "Settings → Storage shows disk use and what happens to source videos (Ask, Delete or Keep). A warning appears above 10 GB.",
            ]}
          />
        </InfoSection>

        <InfoSection title="Settings worth knowing">
          <InfoList
            items={[
              "Speech-to-text: Parakeet only knows English; Whisper handles Hindi and Hinglish.",
              "Whisper model Auto picks one based on your RAM.",
              "Update downloader fixes most failing YouTube downloads.",
              "System check confirms FFmpeg, versions and GPU.",
            ]}
          />
        </InfoSection>
      </div>

      <InfoSection title="Tips for better clips">
        <InfoList
          items={[
            "Videos with a chat replay usually give better picks.",
            "Gaming videos with a facecam frame best.",
            "Expect slower runs on 4 GB machines.",
          ]}
        />
      </InfoSection>

      <InfoSection title="Troubleshooting">
        <div className="divide-y rounded-xl border">
          {FAQ.map((item) => (
            <details key={item.q} className="group px-4">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3 py-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
                {item.q}
                <ChevronDown
                  className="text-muted-foreground size-4 shrink-0 transition-transform group-open:rotate-180"
                  aria-hidden
                />
              </summary>
              <p className="text-muted-foreground pb-3 text-sm leading-relaxed">{item.a}</p>
            </details>
          ))}
        </div>
      </InfoSection>
    </div>
  );
}
