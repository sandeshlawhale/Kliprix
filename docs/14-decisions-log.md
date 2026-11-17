# 14 — Decisions log (highest authority)

If another doc disagrees with this file, this file wins. New decisions are appended with an ID and the reason.

## Decisions
| ID | Decision | Reason |
|---|---|---|
| D-001 | Working name **Kliprix**; branding, accent color, and logo undecided | Needed for folder/code names; easy to rename later |
| D-002 | Local-only app, no hosting, MIT license | Owner requirement |
| D-003 | Frontend: Next.js (App Router) + shadcn/ui + Tailwind, TypeScript. Backend: Python FastAPI. Separate folders, HTTP only | Owner requirement; keeps coupling low |
| D-004 | Docker Compose optional; `start.bat`/`start.sh` is the primary run path | Docker Desktop uses ~2 GB RAM; 4 GB machines must work |
| D-005 | Data in `data/` with subfolders; SQLite for all structured data | Owner requirement |
| D-006 | Max 3 videos per batch, up to 5 clips per video, processed one at a time | Owner requirement; low-RAM friendly |
| D-007 | Fully automatic selection and rendering; no approval step | Owner answer Q8 |
| D-008 | Pages: Home, Batch results (history), Clip detail, Edit video style, Configure API, Settings. No profile page | Owner answers Q1–Q6 |
| D-009 | Sidebar always visible; bottom "Local workspace" menu with Edit video style, Configure API, Settings, Theme, Docs | Owner answer Q2 |
| D-010 | Sidebar button named **"+ New clips"**; history items grouped by date | Owner delegated naming |
| D-011 | LLM is user-chosen (provider, model, key) via LiteLLM; key in session/memory only | Owner requirement |
| D-012 | Candidate-first transcription (signals → top 12 candidates → transcribe those spans); full transcription for ≤ 20 min videos or "Thorough mode" | CPU transcription of long streams is too slow on 4 GB |
| D-013 | Signals: chat, audio, motion (v1). No vision-LLM in v1 | Cheap, works offline, fits RAM |
| D-014 | 3 caption styles: `pop`, `karaoke`, `boxed` | Owner asked for 3 good ones; proposal pending visual check in M6 |
| D-015 | Foreground 4:5 face-crop when a face is present in ≥ 40% of samples, else full-frame; user can override | Owner: "depends on content" |
| D-016 | Captions in English and Hinglish; Hinglish via Roman-prompted Whisper + LLM transliteration with strict 1:1 word arrays | Whisper has no Hinglish mode |
| D-017 | Clip length 30–60 s; up to 90 s when the LLM flags `needs_extended`; hard validation 25–90 s | Owner requirement |
| D-018 | Edits: trim, titles/metadata, caption words, caption style, blur, crop mode; preview (540x960) before export | Owner answers Q9–Q10 |
| D-019 | Source video retention is a user setting (Ask / Delete / Keep) with a storage warning | Owner answer Q12 |
| D-020 | Metadata per clip: on-video title, YouTube title/description, hashtags, LinkedIn post, reason | Owner needs YouTube and LinkedIn posting data |
| D-021 | YouTube only in v1; `sources/` adapter interface keeps Twitch/Kick easy | Owner answer Q14 |
| D-022 | Frontend types generated from backend OpenAPI | Prevents contract drift |

| D-023 | Hinglish = Hindi written with English letters (e.g. "Namaste kaise ho aap?"), never Devanagari | Owner clarification |
| D-024 | Each video shows a muted checkpoint list (7 steps) below the video header and above the clip cards; it ticks off from real backend state and disappears when that video is fully finished; it stays if something failed | Owner UI update |

## Assumptions to confirm with the owner (agent: use these defaults until told otherwise)
| ID | Assumption |
|---|---|
| A-001 | "Download one clip at a time in a folder" means: exports are saved in `data/exports/<batch>/<video>/`, and the user downloads one clip per click through the browser |
| A-002 | "Like Claude Code" for UI means a calm, minimal, text-first look; owner may supply screenshots for exact reference |
| A-003 | Accent color and logo are placeholders (neutral) until branding is decided |
| A-004 | Chat replay is fetched automatically when available; otherwise the app continues without it |
| A-005 | Live (ongoing) streams are rejected in v1; only finished videos and VODs |
| A-006 | Storage warning threshold default is 10 GB |
| A-008 | The checkpoint list disappears per video (when that video and all its clips finish), not only when the entire batch finishes |
| A-007 | Caption visual details (colors, sizes) in `05` are starting values to tune in M6 |

## Agent assumptions (agents append here)
_(empty)_

## Agent proposals (not approved, do not implement)
_(empty)_
