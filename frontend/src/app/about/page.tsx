"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  Captions,
  Clapperboard,
  FileText,
  ScanFace,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { InfoHeader, InfoItem, InfoList, InfoSection } from "@/components/info/info-section";
import { Separator } from "@/components/ui/separator";
import { getHealth } from "@/lib/api";
import { REPO_URL } from "@/lib/links";

const LINK = "text-foreground underline underline-offset-4 hover:text-brand";

export default function AboutPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });

  return (
    <div className="flex flex-col gap-10">
      <InfoHeader
        title="About Kliprix"
        lead="An open-source app that runs on your own PC. Paste up to 3 YouTube links and it makes up to 5 vertical shorts from each one, with no manual editing."
      />

      <p className="text-3xl font-semibold tracking-tight text-balance">
        Long videos in. <span className="text-brand">Shorts worth posting</span> out.
      </p>

      <InfoSection title="What it does for you">
        <p className="text-muted-foreground text-sm">
          Made for creators who clip gaming streams, gaming videos and movie clips.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <InfoItem icon={Sparkles} title="Finds the best moments">
            Chat peaks, audio energy, motion and the transcript point to the parts worth clipping.
          </InfoItem>
          <InfoItem icon={ScanFace} title="Frames the speaker">
            Crops to the face automatically, or keeps the full frame when there is no face.
          </InfoItem>
          <InfoItem icon={Captions} title="Captions every word">
            Word-by-word captions in three styles: Pop, Karaoke and Boxed.
          </InfoItem>
          <InfoItem icon={FileText} title="Writes the text">
            On-video title, YouTube title and description, hashtags, a LinkedIn post, and why each
            moment was picked.
          </InfoItem>
          <InfoItem icon={Clapperboard} title="Ready to post">
            1080×1920 MP4 files at 30 fps.
          </InfoItem>
          <InfoItem icon={Bot} title="Bring your own AI">
            Gemini, Claude, OpenAI, OpenRouter or any OpenAI-compatible server. Your key, your
            provider, nothing else to pay.
          </InfoItem>
        </div>
      </InfoSection>

      <section className="bg-muted/40 flex flex-col gap-3 rounded-xl border p-5">
        <h2 className="flex items-center gap-2 text-lg font-medium">
          <ShieldCheck className="text-brand size-5" aria-hidden /> Private by design
        </h2>
        <InfoList
          items={[
            <>
              Everything runs on your computer. The app only listens on{" "}
              <code className="font-mono">127.0.0.1</code>.
            </>,
            "No accounts, no analytics, no update checks.",
            "It only goes online for the video platform, your AI provider, and one-time setup downloads.",
            "Your API key stays in memory for this session. It is never saved to disk, and closing the tab forgets it.",
          ]}
        />
      </section>

      <div className="grid gap-10 sm:grid-cols-2">
        <InfoSection title="What it doesn't do">
          <InfoList
            items={[
              "No auto-upload to YouTube or Instagram.",
              "No full timeline editor.",
              "No watermark, no mobile app.",
              "Twitch and Kick support is planned.",
            ]}
          />
        </InfoSection>

        <InfoSection title="System requirements">
          <InfoList
            items={[
              "Windows 10/11, macOS or Linux.",
              "4 GB RAM works (slower), 8 GB is normal, 16 GB or an NVIDIA GPU is fastest.",
              "FFmpeg with libass, Python 3.11–3.12 and Node 22 or newer.",
            ]}
          />
        </InfoSection>
      </div>

      <InfoSection title="Responsible use">
        <p className="text-muted-foreground text-sm leading-relaxed">
          You are responsible for having the rights to the videos you clip and for following each
          platform&apos;s terms.
        </p>
      </InfoSection>

      <Separator />

      <footer className="text-muted-foreground flex flex-col gap-2 text-sm">
        <p>
          Kliprix <span className="font-mono">{health.data?.version ?? ""}</span> · MIT license ·{" "}
          <a href={REPO_URL} target="_blank" rel="noreferrer" className={LINK}>
            GitHub
          </a>
        </p>
        <p className="leading-relaxed">
          Built with yt-dlp, faster-whisper, Parakeet, MediaPipe, FFmpeg and LiteLLM, and the fonts
          Anton, Montserrat and Noto Sans Devanagari. See{" "}
          <a
            href={`${REPO_URL}/blob/main/THIRD_PARTY_NOTICES.md`}
            target="_blank"
            rel="noreferrer"
            className={LINK}
          >
            third-party notices
          </a>
          .
        </p>
      </footer>
    </div>
  );
}
