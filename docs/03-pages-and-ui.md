# 03 — Pages and UI

Stack: Next.js (App Router) + TypeScript + Tailwind + shadcn/ui. See `09-tech-stack.md`.

## Design direction
Calm, minimal, text-first, like Claude / Claude Code: neutral grays, one accent color, generous spacing, clear typography, no gradients, no glassmorphism, no decorative animation. Monospace font only for technical values (timestamps, file paths, model ids). Light and dark themes via CSS variables (shadcn tokens). Accent color is a single token `--accent`; the real color is undecided (decision A-003), use a neutral placeholder and never hard-code colors in components.
Desktop-first (min width 1024). Mobile is not a target in v1.

## Global layout
Fixed left **sidebar (260 px, always visible)** + main content area (max width 1100 px, centered).

### Sidebar (top to bottom)
1. App name (text only).
2. **+ New clips** button → `/` (clears the Home form).
3. **History** list (scrollable). Item = first video title (1 line, truncated) + small text "3 videos · 12 clips". Active item highlighted. Right-click or "…" menu: Delete. Empty state: "No generations yet".
4. Bottom **account area** (a button showing a small avatar circle with "L" and the text "Local workspace"). Clicking opens a menu **above** it with:
   - Edit video style → `/style`
   - Configure API → `/configure-api`
   - Settings → `/settings`
   - Theme: Light / Dark / System (inline toggle group)
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

## Home `/`
- Title: "Turn videos into shorts". Subtitle: "Paste up to 3 links."
- Input + **Add** button (Enter also adds). Validates against supported hosts (YouTube in v1; host list comes from backend `GET /api/system`).
- Below: video cards (thumbnail, title, channel, duration, chat badge, remove button). Loading skeleton while inspecting.
- Row: **Edit video style** (secondary button → `/style`) and a read-only summary of current style ("Pop captions · 30–60 s · 5 clips · English").
- **Start** button (primary), disabled until ≥1 valid video and API configured. If API missing: inline alert with "Configure API" button.
States: empty, inspecting, inspect-failed (per card error), ready, starting.

## Batch results `/history/[batchId]`
- Header: batch date, overall status.
- Per video section, top to bottom: video header (title, source badge), then the **checkpoint list**, then a grid of up to 5 **clip cards**.
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
  - **Edit:** trim start/end (two number inputs + slider, shows resulting length, warns outside 25–90 s), caption style (3 options with small visual examples), caption text editor (list of words/phrases, editable inline), background blur (slider), crop mode (Auto / Face / Full frame).
- Unsaved/needs-preview state clearly shown. Edits never silently re-render.

## Edit video style `/style`
Defaults used for new batches (saved to settings): caption style (3), caption language (English / Hinglish), clip length mode (Standard 30–60 s / Allow up to 90 s), clips per video (1–5), background blur strength, crop mode default, title on/off. Each caption style shown with an example image/animation made from the real renderer (generated sample), not a mock screenshot.

## Configure API `/configure-api`
Fields in order: **Provider** dropdown (Gemini, Claude (Anthropic), OpenAI, OpenRouter, Other OpenAI-compatible) → **API key** (password input, never shown after save) → **Load models** button → **Model** dropdown (loaded from the provider; includes "Enter model id manually") → **Test connection** → **Save for this session**. Shows current status: "Configured: Gemini · model-name" (never the key). **Clear** button forgets it. For "OpenAI-compatible", also a Base URL field.

## Settings `/settings`
Sections: Appearance (theme), Storage (usage per folder, source-video policy Ask/Delete/Keep, warning size, cleanup buttons with confirmation), Transcription (Speech-to-text engine: Parakeet / Whisper with a one-line note that Hinglish uses Whisper (D-028); Whisper model: Auto / tiny / base / small / medium, shows detected RAM and what Auto chose), Downloader (yt-dlp version + "Update downloader"), System check (ffmpeg found, Python/Node versions, GPU yes/no), About (version, MIT license).

## Global UI rules
- Every async action shows loading, success, and error states. Errors use the backend `error.message` text.
- Toasts (sonner) for transient confirmations; inline alerts for blocking problems.
- Destructive actions use a confirmation dialog.
- Keyboard accessible, visible focus rings, labels on all inputs, AA contrast in both themes.
- Dates shown in the user's local time.

## shadcn components to use
sidebar, button, input, textarea, card, badge, progress, dialog, alert-dialog, dropdown-menu, popover, select, tabs, slider, toggle-group, switch, skeleton, tooltip, scroll-area, separator, alert, sonner, form (react-hook-form + zod). Install only what is used.
