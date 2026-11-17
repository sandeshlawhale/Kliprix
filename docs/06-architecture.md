# 06 — Architecture

## Repo layout
```
Kliprix/
├── frontend/                  # Next.js app (UI only)
│   ├── src/
│   │   ├── app/               # routes (see 03-pages-and-ui.md)
│   │   ├── components/        # ui/ (shadcn) + feature components
│   │   ├── lib/
│   │   │   ├── api.ts         # ONLY place that calls the backend (fetch wrapper)
│   │   │   ├── api-types.ts   # generated from backend OpenAPI, do not edit by hand
│   │   │   └── session.ts     # sessionStorage for LLM config
│   │   └── hooks/             # React Query hooks, SSE hook
│   ├── package.json
│   └── Dockerfile
├── backend/                   # Python FastAPI (all logic)
│   ├── app/
│   │   ├── main.py            # app factory, routers, startup
│   │   ├── config.py          # settings from env, constants, paths
│   │   ├── api/               # routers: videos, batches, clips, llm, settings, system, storage
│   │   ├── core/              # errors, logging, events (pubsub), security helpers
│   │   ├── db/                # engine, models, migrations (alembic), repositories
│   │   ├── jobs/              # queue, runner, stage orchestration
│   │   ├── sources/           # one adapter per platform: base.py, youtube.py (twitch.py, kick.py later)
│   │   ├── pipeline/          # download, chat, audio, motion, candidates, transcribe, select, cut, face_crop
│   │   ├── llm/               # client.py (LiteLLM wrapper), prompts.py, schemas.py
│   │   ├── render/            # layout_constants.py, ass.py, caption_styles/, compose.py
│   │   └── assets/fonts/      # bundled fonts + licenses
│   ├── tests/
│   ├── pyproject.toml
│   ├── requirements.txt       # pinned
│   └── Dockerfile
├── data/                      # runtime data, gitignored (see 08-data-models.md)
├── docs/                      # this knowledge base
├── scripts/                   # start.bat, start.sh, doctor.py
├── docker-compose.yml
├── README.md
├── LICENSE                    # MIT
├── THIRD_PARTY_NOTICES.md
└── .gitignore
```

## Boundary rules (enforced)
1. `frontend/` never imports from `backend/` and vice versa. No shared folders.
2. The contract is `07-api-contract.md`. The backend exposes `/openapi.json`; the frontend generates `api-types.ts` from it (`openapi-typescript`). If the API changes, regenerate types; type errors reveal breakage.
3. Frontend calls the backend only through `lib/api.ts`. Next.js `rewrites` proxy `/api/*` to `BACKEND_URL` so the browser sees one origin (no CORS in normal use). Do **not** put business logic in Next.js route handlers or server actions.
4. Inside the backend, dependency direction: `api → jobs → pipeline/render/llm → db/core`. `pipeline` and `render` never import from `api`. `sources/` adapters hide platform differences behind one interface (`inspect(url)`, `download(url, dir)`, `get_chat(url)`), so adding Twitch or Kick means adding one file plus a host allowlist entry.

## Runtime model
- One backend process (Uvicorn, 1 worker) with an in-process **job runner thread** that picks the next `queued` job from SQLite and runs it. One job at a time. Heavy work runs in subprocesses (ffmpeg, yt-dlp) or in the runner thread (whisper, opencv), never on the async event loop.
- Progress events: runner publishes to an in-memory pubsub; `GET /api/batches/{id}/events` streams them as Server-Sent Events. The DB is the source of truth; events are only hints to refetch.
- LLM session: provider, model, key held in a module-level object in memory. Never persisted.
- Ports: backend `127.0.0.1:8000`, frontend `127.0.0.1:3000`.

## Environment variables (backend)
`DATA_DIR` (default `<repo>/data`), `BACKEND_HOST` (127.0.0.1), `BACKEND_PORT` (8000), `ALLOWED_ORIGIN` (http://localhost:3000), `LOG_LEVEL` (INFO), `FFMPEG_PATH` (optional). Frontend: `BACKEND_URL` (http://127.0.0.1:8000). No secrets in env files. `.env.example` lists these with defaults.

## Docker
`docker-compose.yml` with two services (`backend`, `frontend`). Backend image installs ffmpeg. Volume `./data:/app/data`. Ports published as `127.0.0.1:8000:8000` and `127.0.0.1:3000:3000` (never `0.0.0.0` on the host). Docker is the *optional* path; `scripts/start.bat|sh` is the main path because Docker Desktop costs RAM on low-spec machines. Whisper/MediaPipe models are cached in `data/models` so they download once.

## Scripts
- `scripts/doctor.py`: checks Python 3.11–3.12 and picks the backend interpreter (`--print-python`), Node ≥ 20, ffmpeg and ffprobe on PATH (and libass support), free disk space, RAM; prints fix hints.
- `start.bat` / `start.sh`: run doctor, create the Python venv if missing, install backend and frontend deps if missing or lockfile changed, run DB migrations, start backend and frontend, open the browser, stop both on Ctrl+C.
