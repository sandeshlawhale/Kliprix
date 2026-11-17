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
| ID | Assumption | Reason |
|---|---|---|
| AA-001 | The knowledge base was committed under `kb/`; moved `kb/AGENTS.md`, `kb/CLAUDE.md`, `kb/docs/` to the repo root (M0) | `06-architecture.md` puts `docs/`, `frontend/`, `backend/` side by side at the root |
| AA-002 | Backend venv uses Python 3.12 (`py -3.12`), even when a newer default Python is installed | Spec says 3.11+; later deps (faster-whisper/ctranslate2, MediaPipe) ship wheels for 3.12 first |
| AA-003 | Backend dependencies split into `requirements.txt` (runtime) and `requirements-dev.txt` (ruff, mypy, pytest, httpx2), both fully pinned incl. transitive deps | Spec asks for pinned deps and lint/type/test tooling; dev tools are not needed at runtime |
| AA-004 | Tests use `httpx2` instead of `httpx` | Installed Starlette (1.10) deprecates `httpx` for its TestClient and asks for `httpx2` |
| AA-005 | FastAPI's native OpenTelemetry hooks are switched off in `create_app`; Next.js telemetry is disabled in the frontend scripts | Hard rule 3 / `11-constraints.md`: no telemetry |
| AA-006 | `doctor.py` warns (not fails) below 5 GB free disk or below 4 GB total RAM; it fails on missing Python 3.11–3.12, Node 20+, ffmpeg, ffprobe, or libass | Spec lists the checks but no disk threshold; low RAM must still be allowed to run (4 GB tier) |
| AA-007 | Start backend with `uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000` from `backend/` | App factory per `06-architecture.md`; start scripts arrive in M10 |
| AA-008 | Keep `frontend/AGENTS.md` (Next.js agent rules block) | `next dev` re-creates it; it points agents to the installed Next.js docs, matching hard rule 6 |
| AA-009 | Backend Python is limited to 3.11–3.12 (`requires-python = ">=3.11,<3.13"`). `doctor.py` picks the interpreter: the backend venv if its version is supported (a venv on another version is a FAIL with a recreate hint), else `py -3.12`/`py -3.11`, `python3.12`/`python3.11`, then `python3`/`python`. `doctor.py --print-python` prints only that path (exit 1 + reason on stderr otherwise) for the M10 start scripts to call | Spec says 3.11+; 3.13+/3.14 lack wheels for later deps (AA-002). No start scripts exist before M10, so the selection logic lives in doctor.py, which is stdlib-only and runs on any Python 3 |
| AA-010 | A relative `DATA_DIR` (e.g. `./data` in `.env.example`) is resolved against the repo root, in both the backend and `doctor.py` | The backend runs from `backend/`; resolving against the working directory would put data in `backend/data` |
| AA-011 | HTTP status per error code: INVALID_URL/UNSUPPORTED_HOST/TOO_MANY_VIDEOS 400; VIDEO_* and GEO_BLOCKED 422; DOWNLOAD_FAILED/DOWNLOADER_OUTDATED/LLM_BAD_RESPONSE 502; FFMPEG_MISSING 503; LLM_NOT_CONFIGURED/CONFLICT 409; LLM_AUTH_FAILED 401; LLM_RATE_LIMITED 429; DISK_FULL 507; NOT_FOUND 404 (also used for 405); VALIDATION_ERROR 422; TRANSCRIBE_FAILED/RENDER_FAILED/INTERNAL 500. Validation error details hold only `loc` and `msg`, never the rejected input | `07` lists codes but no statuses; inputs may contain an API key |
| AA-012 | Settings defaults: theme `system`; style `pop`, `en`, `standard`, 5 clips, blur 30, crop `auto`, title on; whisper `auto`; source policy `ask`; storage warning 10 GB; thorough mode off. Stored one row per top-level key; PUT merges partial bodies (also inside `style_defaults`); explicit `null` is a VALIDATION_ERROR | No doc lists all defaults; values taken from 02/05/11, A-006, and "never delete without a user action" |
| AA-013 | The backend applies Alembic migrations on every start (no-op when current); `alembic upgrade head` from `backend/` also works and reads `DATA_DIR` | A fresh clone must work without a manual step; start scripts (M10) can still run migrations explicitly |
| AA-014 | Deferred endpoints: `POST /api/llm/models` and `POST /api/llm/test` to M5 (they call the provider); `POST /api/system/update-downloader` to M2 (needs yt-dlp); `POST /api/system/open-folder` until the UI needs it (marked optional in 07). `ytdlp_version` is `null` until yt-dlp is installed (M2) | M1 scope says "without provider calls yet"; no fake endpoints |
| AA-015 | The LLM session store is created per app in `create_app` (`app.state.llm_session`) instead of a bare module global; `PUT /api/llm/session` rejects `base_url` for providers that do not use it and requires it for `openai_compatible` | Same single in-memory object per process, but testable; strict input avoids silently ignored settings |
| AA-016 | `/api/system` `gpu` = an NVIDIA GPU answered `nvidia-smi`; Whisper Auto: NVIDIA → `medium` (`large-v3` if VRAM ≥ 8 GB), else RAM ≥ 0.9×16 GB → `medium`, ≥ 0.9×8 GB → `small`, else `base` (`tiny` if < 1.5 GB free). Values are measured on each request | `11` hardware table; machines report slightly less than nominal RAM (same tolerance as doctor) |

### Dependencies added
| Milestone | Package | Why |
|---|---|---|
| M0 | fastapi, uvicorn[standard] (+ pinned transitive deps) | Backend API (09-tech-stack) |
| M0 | ruff, mypy, pytest, httpx2 (dev) | Lint/type/test tooling (09, 12) |
| M0 | next, react, react-dom, tailwindcss, @tailwindcss/turbopack, typescript, eslint, eslint-config-next, @types/* (from `create-next-app`) | Frontend shell (09-tech-stack) |
| M0 | prettier, eslint-config-prettier (dev) | Formatting (09-tech-stack) |
| M0 | openapi-typescript (dev) | Generates `src/lib/api-types.ts` from backend OpenAPI (06, 09) |
| M1 | SQLAlchemy 2.1.9, alembic (+ Mako, MarkupSafe) | SQLite access and migrations (08, 09). 2.1 is the current stable 2.x line; the docs say "2.0" meaning the 2.x API |
| M1 | psutil (+ types-psutil, dev) | RAM detection for `/api/system` and Whisper Auto (09, 11) |

## Agent proposals (not approved, do not implement)
_(empty)_
