# 00 — READ THIS FIRST (for AI coding agents)

Project: **Kliprix** (working name, see decisions D-001). A local-only app that turns up to 3 video links into up to 5 vertical shorts each, with blurred background, face-cropped main video, short title on top and animated captions below.

## Reading order
0. `PROGRESS.md` → where the project stands and the exact next step (read it right after this file; update it before you stop)
1. `01-product-spec.md` → what we build and what we do NOT build
2. `14-decisions-log.md` → final decisions. **If any doc disagrees with this file, this file wins.**
3. `11-constraints.md` and `12-coding-rules.md` → always apply
4. `13-build-plan.md` → find the current milestone, read only the docs it lists
5. Docs 02–10 → the detailed specs for the milestone you are building

## Hard rules (never break)
1. **No gimmicks.** No fake progress bars, simulated results, hard-coded sample clips shown as real output, lorem ipsum, "coming soon" buttons that look functional, or silent `except: pass`. If a feature is not built, it is not shown.
2. **Stay in scope.** Build only what the specs describe. Do not add features, pages, or dependencies that are not documented. If you think something is missing, write it in `14-decisions-log.md` under "Agent proposals" and continue with the documented behavior.
3. **Local only, free only.** No hosting code, no accounts, no paid services, no telemetry. The only network calls: downloading the source video, fetching chat replay, the user's own LLM provider, model/package downloads at setup, and the yt-dlp release check while Settings is open (D-068).
4. **API keys live in memory only**, except the one the user saves with **Save** on Configure API, which is stored encrypted (D-053). Never write a key in plain text anywhere: disk, logs, database, env files, or error messages.
5. **Frontend and backend are separate.** They talk only over HTTP using the contract in `07-api-contract.md`. No shared code, no cross imports.
6. **Do not guess library APIs from memory.** Libraries change (MediaPipe, LiteLLM, Next.js, shadcn, yt-dlp). Read the installed version's docs or source before using an API. Pin versions in lockfiles.
7. **Everything must run on 4 GB RAM** (see `11-constraints.md`). Stream video frames, never load whole videos into memory.
8. **Real verification.** Every milestone has a "Done when" check in `13-build-plan.md`. Run it. Do not claim done without running it and showing the output.

## Working protocol
- **Start of session:** read `PROGRESS.md`, run `git status` and `git log -5`, confirm the repo matches the file.
- **End of session (or when you sense you are running out of context/tokens):** make small commits, then update `PROGRESS.md` (current milestone, exact next step, unverified items, working commands, session log row). Another AI will continue from it.
- Work in small steps (one file or feature at a time) and commit after each working step, so a cut-off never loses work.
- Work one milestone at a time, in order.
- Before coding: list the files you will create/change.
- After coding: run lint, type-check, tests, and the milestone's "Done when" check.
- If a spec is ambiguous: pick the documented default, record it under "Agent assumptions" in `14-decisions-log.md`, and continue. Ask the human only if you are truly blocked.
- If you must deviate from a spec: stop, log the reason in the decisions log, and tell the human.
- Commit messages: `type(scope): summary` (feat, fix, docs, refactor, test, chore).

## Definition of done (every task)
Code compiles, lints clean, type-checks, has tests where logic exists, handles errors with the documented error codes, has no TODO placeholders, and the behavior matches the spec text.
