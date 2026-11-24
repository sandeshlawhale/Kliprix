# 03 — Pages and UI

Stack: Next.js (App Router) + TypeScript + Tailwind + shadcn/ui. See `09-tech-stack.md`.

## Design direction
Calm, minimal, text-first, like Claude / Claude Code: neutral grays, one accent color, generous spacing, clear typography, no gradients, no glassmorphism, no decorative animation. Monospace font only for technical values (timestamps, file paths, model ids). Light and dark themes via CSS variables (shadcn tokens). Accent color is a single token `--accent`; the real color is undecided (decision A-003), use a neutral placeholder and never hard-code colors in components.
Desktop-first (min width 1024). Mobile is not a target in v1.

## Global layout
Fixed left **sidebar (260 px, always visible)** + main content area (max width 1100 px, centered; every page uses this one width, D-038).
Above every page a top bar of fixed height (D-038): the storage warning pill on the left (empty when there is no warning), and on a batch page the batch status, Pause + Cancel (while it runs) or Resume + Cancel (while paused, D-049) and the date and time on the right. The page content always starts at the same height.

### Sidebar (top to bottom)
1. App name (text only).
2. **+ New clips** button → `/` (clears the Home form).
3. **History** list (scrollable). Item = first video title (1 line, truncated) + small text "3 videos · 12 clips". Active item highlighted. Right-click or "…" menu: Delete. Empty state: "No generations yet".
4. Bottom **account area** (a button showing a small avatar circle with "L" and the text "Local workspace"). Clicking opens a menu **above** it with:
   - Edit video style → `/style`
   - Configure API → `/configure-api`
   - Settings → `/settings`
   - Theme: Light / Dark / System (inline toggle group)
   - About → `/about` (D-039)
   - How to use → `/how-to-use` (D-039)
   - Documentation (opens GitHub README in new tab)
   There is no login and no profile page.

## Routes
| Route | Page |
|---|---|
| `/` | Home (new generation) |
| `/history/[batchId]` | Batch results |
| `/history/[batchId]/clips/[clipId]` | Clip detail + editor |
| `/style` | Edit video style (defaults) |
| `/configure-api` | LLM provider setup |
| `/settings` | Settings |
| `/about` | About Kliprix (static, D-039) |
| `/how-to-use` | How to use (static, D-039) |

## Home `/`
(D-037) Centered on the page, nothing else around it.
- Tagline (large, two lines): "Long videos in. Shorts worth posting out." Subtitle: "Paste up to 3 links. Kliprix finds the best moments, frames the speaker and captions every word, right on your computer."
- One full-width box: link field on top ("Paste a video link"). Pasting a link reads it at once; Enter adds a typed link. Validates against supported hosts (from `GET /api/system`).
- Inside the box, under the field: added videos as compact rows (thumbnail, title, channel, duration, chat replay found or not, remove button); a skeleton row while inspecting.
- Box footer: count (`1/3`), **Edit style** (secondary → `/style`, current style summary as its tooltip) and **Process** (primary), disabled until ≥1 video and API configured.
- Under the box, one muted line when the API is missing: "API is not configured. Configure API →".
States: empty, inspecting, inspect-failed (error under the field), ready, starting.

## Batch results `/history/[batchId]`
- (D-038) No page heading; the batch status, Cancel and date/time sit in the top bar. Every video section looks the same, with one or three videos.
- Per video section, top to bottom: a header row with the video title (shown even while the video waits: taken from the home page's link check), platform badge, "Waiting" while queued, chat badge when done, and its own elapsed timer, which starts when this video's work starts; channel and notes under it. At the right end of the same row: **More clips** (D-034) as a count (1–5) select, a button, and an info icon whose tooltip says "Uses the saved download; new clips never repeat the ones you have." (or why it is disabled, e.g. the download was deleted or the AI model is not configured); hidden while the video works. Then the **checkpoint list** (only while this video is being worked on, or when something failed), then the grid of **clip cards**.
- **Checkpoint list** (sits directly below the video header and above the clip cards):
  - A compact, muted (low-contrast gray text, small size) vertical list of the steps for this video, in order:
    1. Download video
    2. Fetch chat replay
    3. Analyze audio and motion
    4. Find highlights
    5. Transcribe
    6. Pick clips
    7. Edit clips (shows count, e.g. "Edit clips 2/5")
  - Each row shows a state icon: ☐ pending (muted), a small spinner while running (text slightly brighter), ✓ done, – skipped (e.g. "Fetch chat replay" when no chat exists, with the text "skipped, no chat replay"), ✕ failed (red, with the error message under it).
  - A step becomes ✓ only when the backend reports it finished. Never tick on a timer.
  - When everything for that video is finished (all steps done or skipped and all clips Done), the list **fades out and disappears**. If any step or clip failed, the list stays visible so the user sees what failed.
  - Collapsed to a single line summary ("4 of 7 steps done") is NOT used in v1; always show the full list while processing.
- Clip card: 9:16 thumbnail (real frame, or skeleton while not ready), on-video title, duration, status badge: **Queued / Processing (stage, %) / Done / Failed / Edited**. Failed shows the error and a Retry button. Done card click → clip detail.
- Live updates via SSE; if SSE disconnects, fall back to polling every 3 s.
- Queued clips show their position in the queue.

## Clip detail `/history/[batchId]/clips/[clipId]`
Two columns.
- **Left:** 9:16 video player (preview if available else export, else raw-cut message). Under it: Preview button, Export button, Download button (enabled only after export), status text.
- **Right (tabs):**
  - **Metadata:** on-video title, YouTube title, YouTube description, hashtags, LinkedIn post, "Why this moment". Each with a Copy button and editable text.
  - **Edit:** trim start/end (two number inputs + slider, shows resulting length, warns outside 25–90 s; for a montage clip (D-051) a "Skip the quiet gaps (montage)" switch lists the parts that play and the seconds skipped, and the length is the played length), caption style (3 options with small visual examples), caption text editor (list of words/phrases, editable inline), background blur (slider), crop mode (Auto / Face / Full frame).
- Unsaved/needs-preview state clearly shown. Edits never silently re-render.

## Edit video style `/style`
Defaults used for new batches (saved to settings): caption style (3), caption language (English / Hinglish), clip length mode (Standard 30–60 s / Allow up to 90 s), clips per video (1–5), background blur strength, crop mode default, title on/off. Each caption style shown with an example image/animation made from the real renderer (generated sample), not a mock screenshot.

## Configure API `/configure-api`
Fields in order: **Provider** dropdown (Gemini, Claude (Anthropic), OpenAI, OpenRouter, Other OpenAI-compatible) → **API key** (password input, never shown after save) → **Load models** button → **Model** dropdown (loaded from the provider; includes "Enter model id manually") → **Test connection** → **Save for this session** → **Save** (D-053: encrypted in the database with the model, loaded on every start). Every model row has a pin button; pinned models are listed first under "Pinned" (per provider, in this browser). Shows current status: "Configured: Gemini · model-name" (never the key) and, when saved, "Saved on this PC (encrypted): Gemini · model-name". **Clear session** forgets the in-memory session; **Delete saved key** deletes the saved one. For "OpenAI-compatible", also a Base URL field.

## Settings `/settings`
Sections: Appearance (theme), Storage (usage per folder, source-video policy Ask/Delete/Keep, warning size, cleanup buttons with confirmation), Transcription (Speech-to-text engine: Parakeet / Whisper with a one-line note that Parakeet only knows English (D-028, D-029); Whisper model: Auto / tiny / base / small / medium, shows detected RAM and what Auto chose), Downloader (yt-dlp version + "Update downloader"), System check (ffmpeg found, Python/Node versions, GPU yes/no), About (version, MIT license).

## Global UI rules
- Every async action shows loading, success, and error states. Errors use the backend `error.message` text.
- Toasts (sonner) for transient confirmations; inline alerts for blocking problems.
- Destructive actions use a confirmation dialog.
- Keyboard accessible, visible focus rings, labels on all inputs, AA contrast in both themes.
- Dates shown in the user's local time.

## shadcn components to use
sidebar, button, input, textarea, card, badge, progress, dialog, alert-dialog, dropdown-menu, popover, select, tabs, slider, toggle-group, switch, skeleton, tooltip, scroll-area, separator, alert, sonner, form (react-hook-form + zod). Install only what is used.
