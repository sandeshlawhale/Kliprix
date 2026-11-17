# 13 — Build plan

Build in this order. Do not start a milestone until the previous "Done when" passes. After each milestone, commit and tag `mN`.

| M | Goal | Read docs | Done when |
|---|---|---|---|
| M0 | Repo scaffold: folders, `.gitignore`, LICENSE (MIT), `scripts/doctor.py`, pinned deps, lint/type tooling, empty FastAPI + Next.js apps that start | 06, 09, 11, 12 | `doctor.py` runs and reports correctly on this machine; backend `/api/health` returns ok; frontend page loads and proxies a health call |
| M1 | Backend foundation: config, errors, logging, SQLite + Alembic migrations, settings and system endpoints, LLM session endpoints (without provider calls yet) | 07, 08 | migrations apply on a fresh DB; `/api/system` shows real ffmpeg/RAM info; pytest passes; OpenAPI generated |
| M2 | Sources: URL allowlist, `inspect`, download, chat replay (YouTube adapter behind the `sources` interface) | 04, 07 | `inspect` returns real metadata for a test link; download produces `source.mp4`; chat fetched for a stream with replay and cleanly absent otherwise; errors map to codes (test a private/invalid link) |
| M3 | Signals and candidates: audio, motion, chat, scoring, candidates.json (CLI script to run on a downloaded file) | 04, 08 | on a real 10–15 min video the script prints 12 candidate windows with plausible peaks; unit tests for normalization and peak picking pass; memory stays under budget |
| M4 | Transcription of candidate spans (+ full mode), model auto-pick, Hinglish path | 04, 10, 11 | word-timestamped transcript for candidate spans; Hinglish sample yields Roman-script words with same count and timing |
| M5 | LLM layer: LiteLLM client, providers, model listing, test, selection call, validation/repair, fallbacks | 07, 10 | with a real key: 5 valid clips with metadata for a real video; bad-JSON and rate-limit paths covered by tests; no key appears in logs |
| M6 | Rendering on one clip: cut, face tracking, layout, 3 caption styles, title, preview and final | 05, 11 | rendered MP4 plays; extracted frames at 1/5/10 s match the layout spec for all 3 styles in both crop modes; A/V sync verified; low-RAM run measured |
| M7 | Job queue and orchestration: runner thread, resume on restart, cancel, SSE, full pipeline through the API | 04, 06, 07, 08 | POST a batch of 3 links → all stages run unattended; kill and restart backend mid-run → it resumes; one failing video does not stop others |
| M8 | Frontend shell: layout, sidebar with history, themes, Home with inspect cards, Configure API, Style, Settings (real API) | 03, 07 | user can configure API, paste links, see cards, start a batch, see it appear in history; light/dark work; no mock data in code |
| M9 | Batch results page with live progress; clip detail with player, metadata copy, editor (trim, captions, style, blur, crop), preview, export, download | 03, 05, 07 | full manual run: edit a clip, preview shows the change, export downloads a playable file; edits survive reload |
| M10 | Storage management, README, `start.bat`/`start.sh`, Docker Compose, THIRD_PARTY_NOTICES, tests and CI, performance notes | 06, 11, 12 | fresh clone on a clean machine follows README and produces shorts; Docker path works; all tests/lint/type checks pass |

## README must contain
What it is, screenshots (real), requirements (Python, Node, FFmpeg with how to install per OS), one-command start (`start.bat` / `./start.sh`), Docker instructions and the low-RAM warning, how to get a Gemini/Claude/OpenAI key, how to use (steps with the 3 videos / 5 clips limits), caption styles, editing and export, where files are stored, troubleshooting (ffmpeg not found, yt-dlp outdated, age-restricted videos, out of memory, Hinglish accuracy), privacy statement (key stays in memory), responsibility note, license (MIT), contributing.

## Later (not in this plan, architecture already allows)
Twitch and Kick sources, cookies support for age-restricted videos, vision-based moment detection, speaker-aware face switching, more caption styles, auto-upload.
