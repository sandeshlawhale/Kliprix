# 09 — Tech stack

**Version rule:** at project start, install the latest stable of each tool, record exact versions in lockfiles (`package-lock.json`, `requirements.txt` with pins), and do not upgrade during a milestone. Read the installed version's docs before using an API; do not rely on memory. Every dependency must be free and open source. Adding a dependency requires a line in `14-decisions-log.md`.

## Frontend
| Need | Choice | Notes |
|---|---|---|
| Framework | Next.js (App Router), TypeScript strict | Used as a UI shell; no business logic server-side |
| Styling | Tailwind CSS + shadcn/ui | Tokens in CSS variables; light/dark |
| Themes | next-themes | `class` strategy |
| Server state | TanStack Query | Caching, refetch on SSE hints |
| Forms | react-hook-form + zod | |
| API types | openapi-typescript | Generated from backend `/openapi.json` |
| Toasts | sonner | |
| Icons | lucide-react | |
| Lint/format | ESLint + Prettier | |
| Tests | Vitest for utils, Playwright smoke test (optional in M10) | |
| Node | 22 LTS or newer | Also yt-dlp's JavaScript runtime for YouTube downloads (D-025) |

## Backend
| Need | Choice | Notes |
|---|---|---|
| Language | Python 3.11–3.12 | 3.13+ not yet supported by later deps (AA-009) |
| API | FastAPI + Uvicorn | SSE via `StreamingResponse` / `sse-starlette` |
| Validation | Pydantic v2 | |
| Database | SQLite + SQLAlchemy 2.0 + Alembic | |
| Download | yt-dlp (Python API) | Updated by the user; "Update downloader" button |
| Chat | yt-dlp live_chat subtitles first; `chat-downloader` fallback | Verify in M2 which works reliably |
| Transcription | faster-whisper | int8 CPU, CUDA if present; models cached in `data/models` |
| Audio signals | librosa (+ numpy, scipy) | Chunked processing |
| Scoring | numpy / pandas | |
| Vision | MediaPipe (Face Detector, Tasks API), OpenCV (headless) | YuNet fallback |
| Video | FFmpeg + ffprobe (system install, not bundled) | Must include libass |
| Captions | Own ASS generator | libass renders via ffmpeg |
| LLM | LiteLLM | One interface for Gemini, Anthropic, OpenAI, OpenRouter, OpenAI-compatible |
| Hinglish fallback | none required; Noto Sans Devanagari font | |
| System info | psutil | RAM detection |
| Lint/type/test | ruff, mypy (or pyright), pytest | |

## Fonts (bundled, SIL OFL)
Anton (title), Montserrat ExtraBold (captions), Noto Sans Devanagari (fallback). Include each font's license file and list them in `THIRD_PARTY_NOTICES.md`.

## Licenses
Project: MIT. Do not vendor GPL code. ffmpeg is installed by the user, not redistributed. List model and library licenses in `THIRD_PARTY_NOTICES.md`.

## Why these choices (short)
- Next.js + shadcn: requested by the owner; strong ecosystem, good defaults.
- FastAPI: typed, fast to build, OpenAPI for free (feeds frontend types).
- SQLite: zero setup, ideal for local single-user; WAL for concurrent reads while the runner writes.
- faster-whisper: much lower memory and faster than original Whisper on CPU.
- LiteLLM: avoids writing and maintaining one adapter per provider.
