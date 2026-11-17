# PROGRESS (handoff file, update at the end of EVERY session)

Any AI or IDE must read this file right after `00-README-FOR-AI.md`, and must update it before stopping, even if it is out of tokens soon. Keep it short and factual. Never claim something is done unless you ran its check.

## Current state
- **Current milestone:** M2 (not started)
- **Status:** M1 done and verified (tag `m1`); M0 tag `m0`
- **Last session:** 2026-11-17, Claude Code (Claude Opus 5.5)
- **Last commit:** see `git log -1` (docs(progress): M1 complete)

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
Start M2: read docs 04 and 07. Add yt-dlp (pinned, logged under "Dependencies added"), then `app/sources/base.py` (adapter interface `inspect(url)`, `download(url, dir)`, `get_chat(url)`) and the URL allowlist using `SUPPORTED_HOSTS` from `app/config.py`. Use `app/core/proc.py` `run_command` for subprocesses and raise `AppError` codes from `app/core/errors.py`. Also implement `POST /api/system/update-downloader` (deferred to M2, AA-014). `ytdlp_version` in `/api/system` will start showing a value once yt-dlp is installed.

## Done but unverified
- Nothing. All M1 checks were run (output below).
- `scripts/doctor.py` was only tested on Windows (no macOS/Linux machine available).

## Known problems / blockers
- None.
- Notes: backend Python is limited to 3.11–3.12 (AA-009); this machine's default is 3.14, and the backend venv uses 3.12. Endpoints not built yet on purpose: `/api/llm/models` and `/api/llm/test` (M5), `/api/system/update-downloader` (M2), `/api/system/open-folder` (optional, AA-014).
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
- Run backend tests/checks: `cd backend && .venv/Scripts/ruff.exe check . && .venv/Scripts/ruff.exe format --check . && .venv/Scripts/mypy.exe && .venv/Scripts/python.exe -m pytest`
- Run frontend checks: `cd frontend && npm run lint && npm run format:check && npm run typecheck`
- Regenerate API types (backend running): `cd frontend && npm run gen:api`

## M1 "Done when" output (2026-11-17)
- Migrations on a fresh DB (`DATA_DIR` = new temp folder with a space): `Running upgrade  -> 0001, Initial schema` → `0001 (head)`; tables `alembic_version, batches, clips, jobs, settings, videos`; `journal_mode: wal`. `alembic check`: "No new upgrade operations detected."
- `/api/system` on a fresh `DATA_DIR`: `{"ffmpeg":{"found":true,"version":"9.0.1-essentials_build-www.gyan.dev","libass":true},"python":"3.12.10","ram_gb":15.7,"gpu":true,"whisper_auto_model":"medium","supported_hosts":["youtube.com","youtu.be"],"disk_free_gb":103.3,"ytdlp_version":null}`. Cross-checked: `ffmpeg -version` 9.0.1, psutil 15.7 GB, `nvidia-smi` GTX 1650 4096 MiB (so `medium`, not `large-v3`).
- pytest: 39 passed; ruff check/format clean; mypy strict clean (33 files).
- OpenAPI: `npm run gen:api` wrote `src/lib/api-types.ts` with `/api/health, /api/system, /api/settings, /api/llm/providers, /api/llm/session`; frontend typecheck, lint, and format checks pass.
- Smoke test: an API key PUT to `/api/llm/session` was found 0 times in the DATA_DIR files (db, logs); `PUT /api/settings` with blur 99 returned `422 VALIDATION_ERROR`.

## Session log (newest first)
| Date | Tool / model | What was done | Result |
|---|---|---|---|
| 2026-11-17 | Claude Code / Claude Opus 5.5 | Python handling: doctor accepts 3.11–3.12, picks the backend interpreter (venv first, then py launcher/python3.x), fails clearly on a wrong-version venv or no supported Python, `--print-python` for start scripts. M1: config (DATA_DIR/LOG_LEVEL/FFMPEG_PATH), AppError + error shape, redacting rotating logs, subprocess helper, SQLite + Alembic (initial schema), settings repo + GET/PUT `/api/settings`, `/api/system`, LLM providers + in-memory session endpoints, regenerated API types | M1 Done-when passed (output above); assumptions AA-009…AA-016 |
| 2026-11-17 | Claude Code / Claude Opus 5.5 | M0: moved KB to root; .gitignore, LICENSE, .env.example; `scripts/doctor.py`; FastAPI app factory + `/api/health` + tests (ruff, mypy strict, pytest); Next.js 16 shell with `/api` rewrite, `lib/api.ts`, generated `api-types.ts`, telemetry disabled | Done-when passed: doctor exit 0 with all OK; backend health 200 `{"status":"ok","version":"0.1.0"}`; same via :3000 proxy; page shows "Backend: ok (version 0.1.0)"; backend-down path shows an error message |
