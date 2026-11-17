# 12 — Coding rules

## General
- Small files with one responsibility. Functions do one thing. Prefer clear code over clever code.
- Comments explain **why**, not what. Public functions have docstrings stating inputs, outputs, errors.
- No dead code, no commented-out code, no TODO/FIXME left in merged work (log real follow-ups in the decisions log instead).
- No magic numbers: constants live in `config.py` or `render/layout_constants.py` with names and units.
- No hidden global state except the documented LLM session and the job runner.

## Backend (Python)
- Type hints everywhere; `mypy --strict` (or pyright strict) passes on `app/`. `ruff check` and `ruff format` clean.
- Errors: raise `AppError(code, message, details)` from `core/errors.py`; one handler turns it into the documented error shape. Catch specific exceptions only; never bare `except`; never swallow errors silently. Log with context (ids, stage), no secrets.
- Long work off the event loop. Subprocess calls through one helper that enforces timeouts, captures stderr, kills on cancel, and uses arg lists.
- DB access only through repositories in `db/`. Every schema change = an Alembic migration.
- Pydantic models for every API request/response and every JSON file format; these models are the schema source.
- Logging: structured, rotating file in `data/logs`, INFO default.

## Frontend (TypeScript)
- `strict: true`; no `any` without a comment. ESLint and Prettier clean; `tsc --noEmit` passes.
- Server data through TanStack Query hooks; local UI state with React state. No global store unless needed (the LLM session helper uses `sessionStorage`).
- All backend calls through `lib/api.ts`. Use generated types.
- Components: feature folders, small, accessible (labels, focus, aria). Use shadcn primitives; do not hand-roll what shadcn provides.
- No inline hard-coded colors; use theme tokens.
- Every list/page handles loading, empty, error.

## Testing
- Backend (pytest): unit tests for scoring/candidate picking, ASS generation (golden files), layout math, LLM schema validation and repair, URL allowlist, state transitions; API tests with FastAPI TestClient and a temp DATA_DIR; pipeline integration test on a short synthetic video generated with ffmpeg (`testsrc` + `sine`), with the LLM mocked **in tests only**.
- No copyrighted media in the repo.
- Frontend: type-check, lint, and component tests for forms and clip editor logic; optional Playwright smoke test (Home → batch page) against a running backend.
- A bug fix includes a regression test where practical.

## Git and docs
- Small commits, conventional messages. `main` always runs.
- Update the README and the relevant doc in `docs/` in the same change that alters behavior.
- `.gitignore`: `data/`, `.venv/`, `node_modules/`, `.next/`, `__pycache__/`, `.env`.

## Anti-gimmick checklist (review before finishing any milestone)
- [ ] Every number and status shown in the UI comes from the backend.
- [ ] No placeholder text, fake data, or disabled "coming soon" controls.
- [ ] Every error path shows a human-readable message.
- [ ] No unused dependencies, files, or endpoints.
- [ ] The feature works after a backend restart.
- [ ] I ran it, not just wrote it.
