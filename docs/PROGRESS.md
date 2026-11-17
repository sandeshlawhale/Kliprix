# PROGRESS (handoff file, update at the end of EVERY session)

Any AI or IDE must read this file right after `00-README-FOR-AI.md`, and must update it before stopping, even if it is out of tokens soon. Keep it short and factual. Never claim something is done unless you ran its check.

## Current state
- **Current milestone:** M1 (not started)
- **Status:** M0 done and verified (tag `m0`)
- **Last session:** 2026-11-17, Claude Code (Claude Opus 5.5)
- **Last commit:** see `git log -1` (docs(progress): M0 complete)

## Milestone checklist
- [x] M0 Repo scaffold
- [ ] M1 Backend foundation
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
Start M1: read docs 07 and 08, then extend `backend/app/config.py` (DATA_DIR, LOG_LEVEL, FFMPEG_PATH) and add `backend/app/core/errors.py` (`AppError` + handler for the error shape). Adding SQLAlchemy/Alembic/psutil needs pinned entries in `backend/requirements.txt` and a line in `14-decisions-log.md` ("Dependencies added").

## Done but unverified
- Nothing. All M0 checks were run (output below).

## Known problems / blockers
- None. Notes: the KB was moved from `kb/` to the repo root (AA-001). The backend venv uses Python 3.12 although the machine default is 3.14 (AA-002).
- `doctor.py` runs with any Python; it reports the interpreter that runs it (3.14 here), not the backend venv's.

## Commands that work (copy from real runs)
- Doctor: `python scripts/doctor.py` (from repo root; exit 0 = ready)
- Create backend venv: `cd backend && py -3.12 -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements-dev.txt`
- Start backend: `cd backend && .venv/Scripts/python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`
- Start frontend: `cd frontend && npm install && npm run dev` (http://127.0.0.1:3000, proxies `/api/*` to `BACKEND_URL`)
- Run backend tests/checks: `cd backend && .venv/Scripts/ruff.exe check . && .venv/Scripts/ruff.exe format --check . && .venv/Scripts/mypy.exe && .venv/Scripts/python.exe -m pytest`
- Run frontend checks: `cd frontend && npm run lint && npm run format:check && npm run typecheck`
- Regenerate API types (backend running): `cd frontend && npm run gen:api`

## Session log (newest first)
| Date | Tool / model | What was done | Result |
|---|---|---|---|
| 2026-11-17 | Claude Code / Claude Opus 5.5 | M0: moved KB to root; .gitignore, LICENSE, .env.example; `scripts/doctor.py`; FastAPI app factory + `/api/health` + tests (ruff, mypy strict, pytest); Next.js 16 shell with `/api` rewrite, `lib/api.ts`, generated `api-types.ts`, telemetry disabled | Done-when passed: doctor exit 0 with all OK; backend health 200 `{"status":"ok","version":"0.1.0"}`; same via :3000 proxy; page shows "Backend: ok (version 0.1.0)"; backend-down path shows an error message |
