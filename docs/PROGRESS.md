# PROGRESS (handoff file, update at the end of EVERY session)

Any AI or IDE must read this file right after `00-README-FOR-AI.md`, and must update it before stopping, even if it is out of tokens soon. Keep it short and factual. Never claim something is done unless you ran its check.

## Current state
- **Current milestone:** M2 (code done; "Done when" partly passed, 2 checks open, NOT tagged)
- **Status:** M1 done and verified (tag `m1`); M0 tag `m0`; `m2` not tagged until the open checks pass
- **Last session:** 2026-11-18, Claude Code (Claude Opus 5.5)
- **Last commit:** see `git log -1`

## Milestone checklist
- [x] M0 Repo scaffold
- [x] M1 Backend foundation
- [ ] M2 Sources (inspect, download, chat)
- [ ] M3 Signals and candidates
- [ ] M4 Transcription and Hinglish
- [ ] M5 LLM layer and selection
- [ ] M6 Rendering (crop, layout, captions)
- [ ] M7 Job queue and orchestration
- [ ] M8 Frontend shell
- [ ] M9 Results page and clip editor
- [ ] M10 Storage, README, scripts, Docker, tests

## In progress (exact next step)
Finish the M2 "Done when", then tag `m2` and stop before M3. Two checks are open:
1. **Chat fetched for a stream with replay.** The owner's link 2 has no replay on YouTube's side (see AA-024), so this path is untested on real data. Needs a past stream whose page shows "Show chat replay". Run `cd backend; .\.venv\Scripts\python.exe -m app.cli.fetch_source <url> --out <temp dir>` and check the `chat: N messages` line and `chat.json`; if it fails, revisit the chat-downloader decision (AA-024).
2. **Owner's private/deleted link (link 3).** It was empty in the owner's message. A stand-in nonexistent id already maps to `VIDEO_UNAVAILABLE`; a private link should give `VIDEO_PRIVATE`.
Built in M2: `app/sources/` (allowlist `urls.py`, interface `base.py`, `registry.py`, `youtube.py` on the yt-dlp Python API, `ytdlp_errors.py`, `youtube_chat.py`), `app/pipeline/download.py` + `chat.py`, `app/cli/fetch_source.py`, `POST /api/videos/inspect`, `POST /api/system/update-downloader`. Assumptions AA-017…AA-024, decision D-025 (Node 22+).

## Done but unverified
- Chat replay fetch + parsing on a real stream with a replay (parser is tested on hand-written lines in yt-dlp's format only).
- `POST /api/system/update-downloader` was only tested with pip mocked (a real run would change the installed yt-dlp version).
- `scripts/doctor.py` was only tested on Windows (no macOS/Linux machine available).

## Known problems / blockers
- None.
- Node 22+ is required (D-025); doctor FAILs below 22.
- No README yet (M10). Its requirements section must say Node 22+ (D-025) and "restart after Update downloader" (AA-021).
- Notes: backend Python is limited to 3.11–3.12 (AA-009); this machine's default is 3.14, and the backend venv uses 3.12. Endpoints not built yet on purpose: `/api/llm/models` and `/api/llm/test` (M5), `/api/system/open-folder` (optional, AA-014).
- Start scripts (`start.bat`/`start.sh`) don't exist yet (M10). They should get the interpreter from `python scripts/doctor.py --print-python`.

## Commands that work (copy from real runs)
- Doctor: `python scripts/doctor.py` (from repo root; exit 0 = ready; any Python 3 can run it)
- Backend interpreter for scripts: `python scripts/doctor.py --print-python` (prints a path, exit 0; or reason on stderr, exit 1)
- Create backend venv: `cd backend && py -3.12 -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements-dev.txt`
- Start backend (the module has no `app` object, only the `create_app` factory, so `uvicorn app.main:app` fails with "Attribute app not found"; `--factory` is required). Tested 2026-11-17, `GET /api/health` → 200 `{"status":"ok","version":"0.1.0"}`:
  - PowerShell: `cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`
  - Git Bash / macOS / Linux: `cd backend && .venv/Scripts/python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000` (on macOS/Linux the venv path is `.venv/bin/python`)
  - Creates `DATA_DIR` folders and applies migrations on start.
- Migrations by hand: `cd backend && .venv/Scripts/alembic.exe upgrade head` (uses `DATA_DIR`); drift check: `.venv/Scripts/alembic.exe check`
- New migration: `cd backend && DATA_DIR=<temp dir> .venv/Scripts/alembic.exe revision --autogenerate -m "..."`, then replace `app.db.models.UtcDateTime()` with `sa.DateTime()` in the generated file
- Start frontend (second terminal, backend running): `cd frontend; npm install; npm run dev` → http://127.0.0.1:3000, proxies `/api/*` to `BACKEND_URL` (default http://127.0.0.1:8000). Tested 2026-11-17: `GET http://127.0.0.1:3000/api/health` → `{"status":"ok","version":"0.1.0"}`, `/` → 200.
- Source stages for one link (dev CLI, AA-020): `cd backend; .\.venv\Scripts\python.exe -m app.cli.fetch_source <url> [--out DIR] [--inspect-only]`
- Install/refresh backend deps after a pull: `cd backend; .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`
- Run backend tests/checks: `cd backend && .venv/Scripts/ruff.exe check . && .venv/Scripts/ruff.exe format --check . && .venv/Scripts/mypy.exe && .venv/Scripts/python.exe -m pytest`
- Run frontend checks: `cd frontend && npm run lint && npm run format:check && npm run typecheck`
- Regenerate API types (backend running): `cd frontend && npm run gen:api`

## M1 "Done when" output (2026-11-17)
- Migrations on a fresh DB (`DATA_DIR` = new temp folder with a space): `Running upgrade  -> 0001, Initial schema` → `0001 (head)`; tables `alembic_version, batches, clips, jobs, settings, videos`; `journal_mode: wal`. `alembic check`: "No new upgrade operations detected."
- `/api/system` on a fresh `DATA_DIR`: `{"ffmpeg":{"found":true,"version":"9.0.1-essentials_build-www.gyan.dev","libass":true},"python":"3.12.10","ram_gb":15.7,"gpu":true,"whisper_auto_model":"medium","supported_hosts":["youtube.com","youtu.be"],"disk_free_gb":103.3,"ytdlp_version":null}`. Cross-checked: `ffmpeg -version` 9.0.1, psutil 15.7 GB, `nvidia-smi` GTX 1650 4096 MiB (so `medium`, not `large-v3`).
- pytest: 39 passed; ruff check/format clean; mypy strict clean (33 files).
- OpenAPI: `npm run gen:api` wrote `src/lib/api-types.ts` with `/api/health, /api/system, /api/settings, /api/llm/providers, /api/llm/session`; frontend typecheck, lint, and format checks pass.
- Smoke test: an API key PUT to `/api/llm/session` was found 0 times in the DATA_DIR files (db, logs); `PUT /api/settings` with blur 99 returned `422 VALIDATION_ERROR`.

## M2 "Done when" output (2026-11-18, real runs)
- **Link 1, public video** `https://youtu.be/zsCGS-Yzjnw?si=…`, CLI exit 0: `url: https://www.youtube.com/watch?v=zsCGS-Yzjnw`; inspect `{"platform":"youtube","source_id":"zsCGS-Yzjnw","title":"UI Libraries for Claude Code (Ban the AI Look)","channel":"Griffin Wooldridge","duration_s":267.0,…,"has_chat":false,"is_live":false}`; progress 1% → 10% … 97% → 100%; `source.mp4 (41684638 bytes, 1920x1080, 266.8 s)`; ffprobe: vp9 1920x1080 + opus, 266.754 s; chat: "no chat replay for this video (has_chat=false); nothing written". A second run logs `download cached`.
- **Link 2, past stream** `https://www.youtube.com/live/o0UeucVxYCA?si=…`, CLI exit 0: inspect `{"source_id":"o0UeucVxYCA","title":"I JUST MET WITH PRESIDENT OBAMA","channel":"IShowSpeed","duration_s":10300.0,"has_chat":false,"is_live":false}`; progress 0% → … → 100%; `source.mp4 (2992228637 bytes, 1920x1080, 10300.3 s)`; ffprobe vp9 + opus, 10300.299 s; chat cleanly absent. yt-dlp: `live_status=was_live`, `subtitles: []`; YouTube's watch page says "Live chat replay is not available for this video." So **chat replay was not tested** (open check 1).
- **Link 3**: not provided. Stand-in (not the owner's link) `https://www.youtube.com/watch?v=zzzzzzzzzz0`: CLI `ERROR VIDEO_UNAVAILABLE: This video is unavailable (removed, deleted, or blocked).` exit 1. CLI `https://vimeo.com/123` → `ERROR UNSUPPORTED_HOST`, `https://www.youtube.com/@somechannel` → `ERROR INVALID_URL`.
- **API** `POST /api/videos/inspect` (running backend) with links 1, 2 and the stand-in: HTTP 200, results `ok: true` (zsCGS-Yzjnw), `ok: true` (o0UeucVxYCA, `has_chat:false`), `ok:false` with code `VIDEO_UNAVAILABLE` after the fix (it was `DOWNLOAD_FAILED` before, see below). 4 links → HTTP 400 `TOO_MANY_VIDEOS`. `/api/system` `ytdlp_version: "2026.10.26"`.
- Bugs found by the real runs and fixed: progress jumped (first event 50%, then went backwards) because HLS `total_bytes_estimate` swings 712 B → 72 MB, now uses fragment counts and never goes down (0c42d9c); "This video is unavailable" mapped to `DOWNLOAD_FAILED`, now `VIDEO_UNAVAILABLE` (ca6dc88). Both have regression tests.
- Checks: pytest 114 passed; ruff/format/mypy strict clean. Doctor with a fake Node 20 → `[FAIL] Node.js v20.11.0`, exit 1; real Node 24 → OK, exit 0.

## Session log (newest first)
| Date | Tool / model | What was done | Result |
|---|---|---|---|
| 2026-11-18 | Claude Code / Claude Opus 5.5 | M2 Done-when on owner links 1–2 + stand-in for 3; fixed progress jitter and "This video is unavailable" mapping; Node 22+ required (D-025); chat-downloader not added (AA-024) | Links 1–2 pass; chat replay untested (link 2 has none); link 3 missing; `m2` not tagged |
| 2026-11-18 | Claude Code / Claude Opus 5.5 | Fixed and tested start commands (backend needs `--factory`). M2 code: URL allowlist, source adapter interface, YouTube adapter (yt-dlp 2026.10.26 + yt-dlp-ejs 0.8.0, Node runtime), error-code mapping, download/chat stages with caching and ffprobe check, fetch_source CLI, `/api/videos/inspect`, `/api/system/update-downloader`; doctor warns below Node 22; API types regenerated. Re-verified M0 claims (httpx2, FastAPI telemetry) in installed source | pytest 112 passed; ruff/format/mypy strict clean; frontend typecheck/format clean. Done-when pending real links |
| 2026-11-17 | Claude Code / Claude Opus 5.5 | Python handling: doctor accepts 3.11–3.12, picks the backend interpreter (venv first, then py launcher/python3.x), fails clearly on a wrong-version venv or no supported Python, `--print-python` for start scripts. M1: config (DATA_DIR/LOG_LEVEL/FFMPEG_PATH), AppError + error shape, redacting rotating logs, subprocess helper, SQLite + Alembic (initial schema), settings repo + GET/PUT `/api/settings`, `/api/system`, LLM providers + in-memory session endpoints, regenerated API types | M1 Done-when passed (output above); assumptions AA-009…AA-016 |
| 2026-11-17 | Claude Code / Claude Opus 5.5 | M0: moved KB to root; .gitignore, LICENSE, .env.example; `scripts/doctor.py`; FastAPI app factory + `/api/health` + tests (ruff, mypy strict, pytest); Next.js 16 shell with `/api` rewrite, `lib/api.ts`, generated `api-types.ts`, telemetry disabled | Done-when passed: doctor exit 0 with all OK; backend health 200 `{"status":"ok","version":"0.1.0"}`; same via :3000 proxy; page shows "Backend: ok (version 0.1.0)"; backend-down path shows an error message |
