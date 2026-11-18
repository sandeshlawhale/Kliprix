# PROGRESS (handoff file, update at the end of EVERY session)

Any AI or IDE must read this file right after `00-README-FOR-AI.md`, and must update it before stopping, even if it is out of tokens soon. Keep it short and factual. Never claim something is done unless you ran its check.

## Current state
- **Current milestone:** M10 (next; continuous run M3→M10, D-027)
- **Status:** M2 done and verified (tag `m2`); M1 tag `m1`; M0 tag `m0`
- **Last session:** 2026-11-18, Claude Code (Claude Opus 5.5)
- **Last commit:** see `git log -1`

## Milestone checklist
- [x] M0 Repo scaffold
- [x] M1 Backend foundation
- [x] M2 Sources (inspect, download, chat)
- [x] M3 Signals and candidates
- [x] M4 Transcription and Hinglish (Hinglish sample check open, owner)
- [x] M5 LLM layer and selection (real-key check open, owner)
- [x] M6 Rendering (crop, layout, captions) (owner visual review open)
- [x] M7 Job queue and orchestration (real 3-link run open: needs the owner's key)
- [x] M8 Frontend shell
- [x] M9 Results page and clip editor (checked with a test-only fake LLM; real-key run open)
- [ ] M10 Storage, README, scripts, Docker, tests

## In progress (exact next step)
M10: README, `scripts/start.bat` + `scripts/start.sh`, Docker Compose + Dockerfiles, THIRD_PARTY_NOTICES, CI workflow, performance notes. Storage endpoints are already built (AA-054).
M3–M9 built (see "M3–M9 output" below, AA-025…AA-057, proposal P-001).

## Done but unverified
- `VIDEO_PRIVATE` on a real private video: the owner's "private" link (`aaaaaaaaaaa`) is answered by YouTube with "This video is unavailable", so it maps to `VIDEO_UNAVAILABLE`. The private mapping is covered by a unit test with YouTube's "Private video" reason only.
- Leftover part cleanup (`remove_leftover_parts`) is unit-tested; a real run where yt-dlp leaves a part behind has not happened again since the fix.
- `POST /api/system/update-downloader` was only tested with pip mocked (a real run would change the installed yt-dlp version).
- `scripts/doctor.py` was only tested on Windows (no macOS/Linux machine available).

## Known problems / blockers
- None.
- Node 22+ is required (D-025); doctor FAILs below 22.
- `DATA_DIR` inside a OneDrive folder: OneDrive/antivirus can briefly lock big files (seen once: yt-dlp could not delete a 2.9 GB part file). The download now retries the cleanup; the M10 README should advise a `DATA_DIR` outside synced folders.
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

## M3–M9 output (2026-11-18, real runs)
- **M3** `python -m app.cli.find_candidates ../data/work/m3-15min` (15-min slice of VnQ4VcAqauk with its 47 chat messages, AA-029): 12 candidates, e.g. `1. peak 0:11:17 window 0:10:52-0:11:52 score 0.707 (chat=1.00 audio=0.16 motion=1.00)`; audio 8.2 s, motion 111.9 s; **peak memory 0.20 GB**. Full 101-min stream: 12 candidates, audio 62.5 s, motion 645 s, peak 0.20 GB. 4.5-min video zsCGS-Yzjnw: 4 candidates (short video), no chat (weights renormalized).
- **M4** `transcribe_candidates ../data/work/m3-15min` (≤ 20 min → whole video): Auto = `medium`; GPU failed (`cublas64_12.dll` missing) → CPU; 207 segments, 2101 words, language en, 560 s, peak 2.03 GB. Full stream with `--model base`: 11 merged candidate spans (1400 s of 6076 s), 3232 words, 192 s, **peak 0.66 GB** (budget 1.5 GB at `base`).
- **M5** no real key here: covered by tests (bad JSON → repair → `LLM_BAD_RESPONSE`, 429 backoff 2/6/15 s → `LLM_RATE_LIMITED`, auth → `LLM_AUTH_FAILED`, key never in logs or responses, select repair/fallback). Owner check: `cd backend; .\.venv\Scripts\python.exe -m app.cli.select_clips ..\data\work\m3-15min --provider gemini --model <id>` (key asked hidden).
- **M6** `render_clip ../data/work/m3-15min --start 652 --end 700 --title "Clutch round at the last second"` (real gaming stream): pop/auto → Auto chose full frame (faces in 35% of samples < 40%); karaoke/face (forced) → crop follows the streamer's webcam; boxed/full/blur 60 as 540x960 preview. All: 1080x1920 (preview 540x960) @ 30/1, video 48.000 s, audio 48.0 s, **A/V difference 0 ms**; final render 61–84 s; frames at 1/5/10 s in `data/work/m6/frames/` (checked: title above the foreground, captions below, active word highlighted, blur visible, no overlap). Low-RAM run (`--low-ram`, ffmpeg thread limit, face crop): 88 s, **peak 0.89 GB backend + ffmpeg**.
- **M7** integration tests on synthetic ffmpeg videos with a fake source, fake Whisper and fake LLM (tests only): a 3-link batch runs unattended (2 videos done with 2 exported clips each, 1 `VIDEO_PRIVATE` → batch `partial`, the others unaffected); all 7 checkpoints done, edit `2/2`; cancel during a download stops it and marks the batch `cancelled`; **stopping the app mid-download and starting it again resumes and finishes the batch**; edit → preview (HTTP 206 Range) → export → download with `filename="Fake LLM pick.mp4"`; edits survive reload. Real server: `POST /api/batches` without a key → `409 LLM_NOT_CONFIGURED`. The real 3-link run needs the owner's key.
- **M8/M9** browser run (built-in browser, 1280x860) against a **test-only** backend started from a scratch script with a fake LLM reply (no key here; real YouTube download, real Whisper `base`, real renders; not in the repo): Configure API (manual model id) → "Configured: Google Gemini · test-model", key not shown; Home: inspect card for zsCGS-Yzjnw, draft kept across `/style`; Start → `/history/<id>` with live checkpoints, batch in the sidebar under "Today"; batch done → checkpoint list hidden, 2 clip cards with real thumbnails (one auto face crop), "Ask" source dialog shown once; clip page: export plays, metadata with Copy; Edit → Boxed → Apply → "Edited — preview needed" → Preview (17 s) → Export with live "Processing · Rendering 75%" → Done; download through the proxy: `filename="TEST fake LLM title.mp4"`, h264 1080x1920 53.13 s + aac 53.14 s; edits survive reload. Bugs found and fixed: SSE held by the dev proxy's gzip (AA-057), batch badge "Queued" while running, hydration mismatches (AA-050).
- pytest 221 passed; ruff/format/mypy strict clean; frontend lint/format/typecheck clean; vitest 11 passed.

## M1 "Done when" output (2026-11-17)
- Migrations on a fresh DB (`DATA_DIR` = new temp folder with a space): `Running upgrade  -> 0001, Initial schema` → `0001 (head)`; tables `alembic_version, batches, clips, jobs, settings, videos`; `journal_mode: wal`. `alembic check`: "No new upgrade operations detected."
- `/api/system` on a fresh `DATA_DIR`: `{"ffmpeg":{"found":true,"version":"9.0.1-essentials_build-www.gyan.dev","libass":true},"python":"3.12.10","ram_gb":15.7,"gpu":true,"whisper_auto_model":"medium","supported_hosts":["youtube.com","youtu.be"],"disk_free_gb":103.3,"ytdlp_version":null}`. Cross-checked: `ffmpeg -version` 9.0.1, psutil 15.7 GB, `nvidia-smi` GTX 1650 4096 MiB (so `medium`, not `large-v3`).
- pytest: 39 passed; ruff check/format clean; mypy strict clean (33 files).
- OpenAPI: `npm run gen:api` wrote `src/lib/api-types.ts` with `/api/health, /api/system, /api/settings, /api/llm/providers, /api/llm/session`; frontend typecheck, lint, and format checks pass.
- Smoke test: an API key PUT to `/api/llm/session` was found 0 times in the DATA_DIR files (db, logs); `PUT /api/settings` with blur 99 returned `422 VALIDATION_ERROR`.

## M2 "Done when" output (2026-11-18, real runs), PASSED
- **Stream with chat replay** `https://www.youtube.com/live/VnQ4VcAqauk?si=…`, CLI exit 0: inspect `{"source_id":"VnQ4VcAqauk","title":"Team Elektros vs P7 Esports & Assasin Crew vs Repotted Gaming","channel":"CarbonFin Gaming","duration_s":6076.0,"has_chat":true,"is_live":false}`; progress 0% → 10% … 90% → 100%; `source.mp4 (2948619678 bytes, 1920x1080, 6076.1 s)`; ffprobe: h264 1920x1080 60 fps + opus, 6076.054 s (H.264 thanks to D-026; it was AV1 before). `chat: 178 messages`; first `t=30.4 s`, last `t=6069.8 s` (video 6076.1 s); all sorted and inside the video; per 1000 s: 30, 21, 18, 49, 30, 27, 3. Samples: `:face-blue-wide-eyes::hand-pink-waving:`, `rpg first or later?`, `Bro u look like u slept for 30 mins`, `hello brother 👋👋👋`, `hi carbon its 4am here in the philippines hehe still watchin`.
- **Owner's "private" link** `https://www.youtube.com/watch?v=aaaaaaaaaaa`: CLI `ERROR VIDEO_UNAVAILABLE: This video is unavailable (removed, deleted, or blocked).` exit 1 (yt-dlp: `[youtube] aaaaaaaaaaa: This video is unavailable`).
- **API** `POST /api/videos/inspect` with both: HTTP 200; `ok: true` (VnQ4VcAqauk, `has_chat: true`), `ok: false` `VIDEO_UNAVAILABLE`.
- Bugs found by this run and fixed (36ad571): yt-dlp left a 2.9 GB `source.f299.mp4` ("Unable to delete file", deletable a moment later) → download now removes leftover parts with retry; the CLI crashed printing an emoji chat line on the cp1252 console → prints UTF-8.
- Final checks: pytest 118 passed; ruff/format/mypy strict clean; frontend lint/typecheck/format clean; doctor "Ready. 0 warning(s)".

### Earlier M2 runs (same day)
- **Link 1, public video** `https://youtu.be/zsCGS-Yzjnw?si=…`, CLI exit 0: `url: https://www.youtube.com/watch?v=zsCGS-Yzjnw`; inspect `{"platform":"youtube","source_id":"zsCGS-Yzjnw","title":"UI Libraries for Claude Code (Ban the AI Look)","channel":"Griffin Wooldridge","duration_s":267.0,…,"has_chat":false,"is_live":false}`; progress 1% → 10% … 97% → 100%; `source.mp4 (41684638 bytes, 1920x1080, 266.8 s)`; ffprobe: vp9 1920x1080 + opus, 266.754 s; chat: "no chat replay for this video (has_chat=false); nothing written". A second run logs `download cached`.
- **Link 2, past stream** `https://www.youtube.com/live/o0UeucVxYCA?si=…`, CLI exit 0: inspect `{"source_id":"o0UeucVxYCA","title":"I JUST MET WITH PRESIDENT OBAMA","channel":"IShowSpeed","duration_s":10300.0,"has_chat":false,"is_live":false}`; progress 0% → … → 100%; `source.mp4 (2992228637 bytes, 1920x1080, 10300.3 s)`; ffprobe vp9 + opus, 10300.299 s; chat cleanly absent. yt-dlp: `live_status=was_live`, `subtitles: []`; YouTube's watch page says "Live chat replay is not available for this video." So **chat replay was not tested** (open check 1).
- **Link 3**: not provided. Stand-in (not the owner's link) `https://www.youtube.com/watch?v=zzzzzzzzzz0`: CLI `ERROR VIDEO_UNAVAILABLE: This video is unavailable (removed, deleted, or blocked).` exit 1. CLI `https://vimeo.com/123` → `ERROR UNSUPPORTED_HOST`, `https://www.youtube.com/@somechannel` → `ERROR INVALID_URL`.
- **API** `POST /api/videos/inspect` (running backend) with links 1, 2 and the stand-in: HTTP 200, results `ok: true` (zsCGS-Yzjnw), `ok: true` (o0UeucVxYCA, `has_chat:false`), `ok:false` with code `VIDEO_UNAVAILABLE` after the fix (it was `DOWNLOAD_FAILED` before, see below). 4 links → HTTP 400 `TOO_MANY_VIDEOS`. `/api/system` `ytdlp_version: "2026.10.26"`.
- Bugs found by the real runs and fixed: progress jumped (first event 50%, then went backwards) because HLS `total_bytes_estimate` swings 712 B → 72 MB, now uses fragment counts and never goes down (0c42d9c); "This video is unavailable" mapped to `DOWNLOAD_FAILED`, now `VIDEO_UNAVAILABLE` (ca6dc88). Both have regression tests.
- Checks: pytest 114 passed; ruff/format/mypy strict clean. Doctor with a fake Node 20 → `[FAIL] Node.js v20.11.0`, exit 1; real Node 24 → OK, exit 0.

## Session log (newest first)
| Date | Tool / model | What was done | Result |
|---|---|---|---|
| 2026-11-18 | Claude Code / Claude Opus 5.5 | M2 finished: real stream with chat replay (178 messages), owner's "private" link, H.264 preference (D-026), leftover-part cleanup, UTF-8 CLI output; chat-downloader confirmed not needed (AA-024) | M2 Done-when passed; tagged `m2`; stopped before M3 |
| 2026-11-18 | Claude Code / Claude Opus 5.5 | M2 Done-when on owner links 1–2 + stand-in for 3; fixed progress jitter and "This video is unavailable" mapping; Node 22+ required (D-025); chat-downloader not added (AA-024) | Links 1–2 pass; chat replay untested (link 2 has none); link 3 missing; `m2` not tagged |
| 2026-11-18 | Claude Code / Claude Opus 5.5 | Fixed and tested start commands (backend needs `--factory`). M2 code: URL allowlist, source adapter interface, YouTube adapter (yt-dlp 2026.10.26 + yt-dlp-ejs 0.8.0, Node runtime), error-code mapping, download/chat stages with caching and ffprobe check, fetch_source CLI, `/api/videos/inspect`, `/api/system/update-downloader`; doctor warns below Node 22; API types regenerated. Re-verified M0 claims (httpx2, FastAPI telemetry) in installed source | pytest 112 passed; ruff/format/mypy strict clean; frontend typecheck/format clean. Done-when pending real links |
| 2026-11-17 | Claude Code / Claude Opus 5.5 | Python handling: doctor accepts 3.11–3.12, picks the backend interpreter (venv first, then py launcher/python3.x), fails clearly on a wrong-version venv or no supported Python, `--print-python` for start scripts. M1: config (DATA_DIR/LOG_LEVEL/FFMPEG_PATH), AppError + error shape, redacting rotating logs, subprocess helper, SQLite + Alembic (initial schema), settings repo + GET/PUT `/api/settings`, `/api/system`, LLM providers + in-memory session endpoints, regenerated API types | M1 Done-when passed (output above); assumptions AA-009…AA-016 |
| 2026-11-17 | Claude Code / Claude Opus 5.5 | M0: moved KB to root; .gitignore, LICENSE, .env.example; `scripts/doctor.py`; FastAPI app factory + `/api/health` + tests (ruff, mypy strict, pytest); Next.js 16 shell with `/api` rewrite, `lib/api.ts`, generated `api-types.ts`, telemetry disabled | Done-when passed: doctor exit 0 with all OK; backend health 200 `{"status":"ok","version":"0.1.0"}`; same via :3000 proxy; page shows "Backend: ok (version 0.1.0)"; backend-down path shows an error message |
