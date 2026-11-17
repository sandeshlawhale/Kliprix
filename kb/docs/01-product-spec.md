# 01 — Product spec

## What it is
A local web app (runs on the user's PC, opened in a browser) that:
1. takes up to **3 video links** (YouTube in v1),
2. finds the best moments automatically,
3. renders up to **5 vertical shorts (1080x1920) per video**,
4. lets the user make small edits, preview, and export.

## Users
Content creators who mostly clip **gaming streams and videos, and movie clips**. People download the repo and run it on their own PC, including low-spec PCs (min 4 GB RAM).

## Goals
- One paste → finished shorts, fully automatic (no approval step before rendering).
- Good highlight picking using multiple signals: chat peaks, audio energy, motion, transcript.
- A consistent, good-looking layout (see `05-video-layout-spec.md`).
- Captions in **English and Hinglish** (Hinglish = Hindi written with English letters, exactly how people type it in chat. Example: "Namaste kaise ho aap?" Not Devanagari: "नमस्ते कैसे हो आप?").
- Works on Windows first; also Mac and Linux.
- LLM is chosen by the user (Gemini, Claude, OpenAI, others) with their own key.

## Hard limits
| Item | Limit |
|---|---|
| Videos per batch | 3 |
| Clips per video | up to 5 |
| Clip length | 30–60 s default; up to 90 s only when the moment needs context |
| Output | 1080x1920, 30 fps, MP4 (H.264 + AAC) |
| Processing | one clip at a time (queue) |

## Non-goals (do NOT build)
Hosting or deployment, user accounts or login, multi-user, automatic upload to YouTube/Instagram/LinkedIn, a full timeline editor, mobile app, paid features, watermark or channel branding, approval step before rendering, multimodal (vision) analysis in v1, Twitch/Kick in v1 (architecture must allow adding them later, see `06-architecture.md`).

## Metadata generated per clip
On-video title, YouTube title, YouTube description, hashtags, LinkedIn post text, short reason why this moment was picked. (Instagram can reuse the YouTube description + hashtags.)

## Success criteria
- Paste a 15-minute gaming video link → 5 playable, well-framed shorts with captions and metadata, no manual steps.
- App survives restart: history and finished clips are still there.
- Runs on a 4 GB RAM PC without crashing (slower is acceptable).
- A new user can install and run from the README in under 15 minutes.
