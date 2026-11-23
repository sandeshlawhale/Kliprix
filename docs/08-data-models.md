# 08 — Data models and storage

## Data directory (`DATA_DIR`, default `./data`, gitignored, created on startup)
```
data/
├── db/app.sqlite            # SQLite (WAL mode)
├── downloads/<video_id>/    # source.mp4, info.json, chat.json, audio.wav
│   └── signals/             # audio.json, motion.json, chat.json (1-second grids)
│   └── candidates.json, transcript.json, selection.json
├── work/<clip_id>/          # cut.mp4, crop.mp4, captions.ass (temp, deletable)
├── previews/<clip_id>.mp4
├── thumbnails/<clip_id>.jpg
├── exports/<batch_id>/<video_slug>/clip_01_<slug>.mp4
├── models/                  # whisper + mediapipe model cache
└── logs/app.log             # rotating, never contains keys
```
`video_id` is the internal UUID. Folder and file names are slugified (ASCII, no spaces). Paths stored in the DB are **relative to DATA_DIR**.

## SQLite (SQLAlchemy 2.0 + Alembic migrations; every schema change = a migration)
Pragmas on connect: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`.

```
settings(key TEXT PK, value_json TEXT)

batches(id PK, created_at, status, title, style_json)
  status: queued | running | paused | done | partial | failed | cancelled   (paused: D-049)

videos(id PK, batch_id FK, position INT, url, platform, source_id, title, channel,
       duration_s REAL, thumbnail_path, has_chat INT, status, stage, progress REAL,
       error_code, error_message, source_path, created_at, updated_at)
  status: queued | running | done | failed | cancelled
  stage: inspect | download | chat | audio | motion | candidates | transcribe | select | clips

clips(id PK, video_id FK, batch_id FK, idx INT, status, stage, progress REAL,
      start_s REAL, end_s REAL, title, youtube_title, youtube_description,
      hashtags_json, linkedin_post, reason, score REAL,
      caption_style, caption_language, crop_mode, blur INT,
      words_json,                      -- [{text,start,end}] relative to clip start
      blur_boxes_json,                 -- [{start,end,x,y,w,h}] manual blur boxes (D-048, migration 0004)
      edited INT, thumbnail_path, preview_path, export_path,
      error_code, error_message, created_at, updated_at)
  status: queued | running | done | failed | cancelled
  stage: cut | face_crop | captions | render | preview

jobs(id PK, kind, ref_id, status, attempts INT, priority INT, payload_json,
     error_code, error_message, created_at, started_at, finished_at)
  kind: video_pipeline | clip_render | clip_preview
  status: queued | running | paused | done | failed | cancelled   (paused: D-049)
```
Indexes: `videos(batch_id)`, `clips(video_id)`, `clips(batch_id)`, `jobs(status, priority, created_at)`.
Deleting a batch cascades to videos, clips, jobs; files are removed when `delete_files=true`.

## State machines
- Job: `queued → running → done | failed | cancelled`. On startup `running → queued`. Pause (D-049): `queued | running → paused`, resume `paused → queued`.
- Batch pause (D-049): `queued | running → paused → queued | running`; a paused batch stays paused until resume or cancel, even across a restart.
- Video: `queued → running(stage…) → done | failed`. A video is `done` when its clips exist; clips render afterwards as their own jobs.
- Clip: `queued → running(cut→face_crop→captions→render) → done | failed`. Editing a done clip sets `edited=1`; preview/export create new jobs.
- Batch status is derived: all videos done and ≥1 clip done → `done`; some failed → `partial`; all failed → `failed`.
- Queue order: batch creation order, then video position, then clip index. Edit/preview jobs have higher priority than the remaining background clips so the user is not left waiting.

## File formats (JSON, UTF-8, versioned with `"version": 1`)
`signals/<name>.json`: `{ "version":1, "hz":1, "values":[...] }`
`candidates.json`: `{ "version":1, "items":[{ "peak_t":312, "start":287, "end":347, "score":0.82, "signals":{"chat":0.9,"audio":0.7,"motion":0.6} }] }`
`transcript.json`: `{ "version":1, "language":"en", "engine":"parakeet"|"whisper" (D-028; missing = whisper), "segments":[{ "start":290.1, "end":294.8, "text":"...", "words":[{"text":"no","start":290.1,"end":290.3}] }] }`
`selection.json`: `{ "version":1, "clips":[{ "start":288.4, "end":340.2, "title":"...", "youtube_title":"...", "youtube_description":"...", "hashtags":["..."], "linkedin_post":"...", "reason":"...", "score":0.9, "needs_extended":false, "from_llm":true, "moment_type":"clutch", "quality":["setup includes clutch_call (E3)"] }] }` (D-050: `score` is the combined quality score; `moment_type` and `quality`, the context engine's notes, are optional and default to empty in older files)

## Retention
`source_policy`: `delete` removes `downloads/<video_id>/source.mp4` and `audio.wav` after all clips of the video finish (keeps signals, transcript, selection so edits still work) **and keeps each clip's `work/<clip_id>/cut.mp4`** (which includes 5 s handles before and after). Trimming beyond those handles needs the source, so with `delete` the editor limits trim to the handles and says so (`source_bounds` in the API reflects this). `keep` leaves everything. `ask` shows a dialog when a batch finishes. Show the warning banner when total size > `storage_warn_gb`. Never delete anything without a user action or an explicit policy.
