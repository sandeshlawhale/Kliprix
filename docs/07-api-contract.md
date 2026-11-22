# 07 — API contract

Base path `/api`. JSON unless stated. IDs are UUID strings. Times in seconds (float) relative to video or clip start. Timestamps in ISO 8601 UTC. FastAPI + Pydantic v2 models are the source of truth; `/openapi.json` must match this document.

## Error shape (all non-2xx)
```json
{ "error": { "code": "VIDEO_PRIVATE", "message": "This video is private.", "details": {} } }
```
Codes: `INVALID_URL`, `UNSUPPORTED_HOST`, `TOO_MANY_VIDEOS`, `VIDEO_PRIVATE`, `VIDEO_AGE_RESTRICTED`, `VIDEO_UNAVAILABLE`, `GEO_BLOCKED`, `DOWNLOAD_FAILED`, `DOWNLOADER_OUTDATED`, `FFMPEG_MISSING`, `LLM_NOT_CONFIGURED`, `LLM_AUTH_FAILED`, `LLM_RATE_LIMITED`, `LLM_BAD_RESPONSE`, `TRANSCRIBE_FAILED`, `MODEL_DOWNLOAD_FAILED`, `RENDER_FAILED`, `DISK_FULL`, `NOT_FOUND`, `VALIDATION_ERROR`, `CONFLICT`, `INTERNAL`.
Messages are human-readable and never contain keys, tokens, or full file-system paths.

## System
- `GET /api/health` → `{ "status": "ok", "version": "x.y.z" }`
- `GET /api/system` → `{ ffmpeg: {found, version, libass}, python, ram_gb, gpu: bool, whisper_auto_model, supported_hosts: ["youtube.com","youtu.be"], disk_free_gb, ytdlp_version, transcribe_engines }` (`transcribe_engines`: D-030)
- `POST /api/system/update-downloader` → `{ before, after }` (runs the yt-dlp upgrade)
- `POST /api/system/open-folder` body `{ "target": "exports" | "data" }` → opens the folder in the OS file manager (local use only; optional feature)

## LLM session (in memory only)
- `GET /api/llm/providers` → `[{ id, label, needs_base_url }]` (gemini, anthropic, openai, openrouter, openai_compatible)
- `POST /api/llm/models` body `{ provider, api_key, base_url? }` → `{ models: [{id, label}] }` (fetched live from the provider; on failure, `LLM_AUTH_FAILED`)
- `POST /api/llm/test` body `{ provider, model, api_key, base_url? }` → `{ ok: true, latency_ms }`
- `PUT /api/llm/session` body `{ provider, model, api_key, base_url? }` → `{ configured: true, provider, model }`
- `GET /api/llm/session` → `{ configured, provider?, model? }` (never returns the key)
- `DELETE /api/llm/session` → `204`

## Videos
- `POST /api/videos/inspect` body `{ urls: string[] }` (1–3) → `{ results: [{ url, ok, video?: {platform, source_id, title, channel, duration_s, thumbnail_url, has_chat, is_live}, error? }] }`. Live (ongoing) streams are rejected with `VIDEO_UNAVAILABLE` ("Live streams are not supported yet").
- `POST /api/videos/{id}/more-clips` body `{ count }` (1–5) → `202 Batch` (D-034). Up to `count` more clips from the video's saved download: no new download or signals; candidates are found again with the existing clips (± 30 s) blocked; only parts of the new candidate spans that were never transcribed are transcribed; the LLM is told which moments already have clips; new clips follow the existing indexes. Finding nothing new is not a failure (the video's `note` says so). Errors: `NOT_FOUND`, `LLM_NOT_CONFIGURED`, `CONFLICT` (the video is queued/running, or its download was deleted), `VALIDATION_ERROR`.

## Batches (history)
- `POST /api/batches` body `{ urls: string[] (1–3), style?: StyleOverrides }` → `201 { id }`. Requires LLM session, else `LLM_NOT_CONFIGURED`. Snapshots the style into the batch.
- `GET /api/batches?cursor=&limit=` → `{ items: [BatchSummary], next_cursor }` where `BatchSummary = { id, created_at, status, title, video_count, clip_count }`
- `GET /api/batches/{id}` → `Batch = { id, created_at, status, style, videos: [Video] }`
  - `Video = { id, url, platform, title, channel, duration_s, thumbnail_url, has_chat, status, stage, progress, error?, note?, can_add_clips, add_clips_blocked?, checkpoints: [Checkpoint], clips: [ClipSummary] }` (`can_add_clips` / `add_clips_blocked`: whether more clips can start now, else why, D-034)
  - `Checkpoint = { key: "download"|"chat"|"signals"|"candidates"|"transcribe"|"select"|"edit", label, state: "pending"|"running"|"done"|"skipped"|"failed", detail?: string, engine?: string }` (`engine`: Transcribe step only, the engine that runs, D-030 temporary) (`detail` examples: "skipped, no chat replay", "2/5", or the error message). Always returned in the fixed order above.
  - `ClipSummary = { id, index, status, stage, progress, queue_position?, title, start_s, end_s, duration_s, thumbnail_url?, error?, edited: bool, has_preview: bool, has_export: bool }`
- `DELETE /api/batches/{id}?delete_files=true` → `204`
- `POST /api/batches/{id}/cancel` → `Batch`
- `GET /api/batches/{id}/events` → `text/event-stream`. Events: `video` and `clip` with `{id, status, stage, progress}`; `batch` with `{status}`; heartbeat comment every 15 s. Treat events as hints; refetch for truth.

## Clips
- `GET /api/clips/{id}` → `Clip = ClipSummary + { video_id, batch_id, metadata: Metadata, edit: EditState, words: [Word], source_bounds: {min_start_s, max_end_s} }`
  - `Metadata = { title, youtube_title, youtube_description, hashtags: string[], linkedin_post, reason }`
  - `EditState = { caption_style: "pop"|"karaoke"|"boxed", caption_language: "en"|"hinglish", crop_mode: "auto"|"face"|"full", blur: 0-60, start_s, end_s, blur_boxes: BlurBox[] }`; `BlurBox = { start, end, x, y, w, h }`: clip seconds and shares (0-1) of the 1080x1920 canvas, at most 20 (D-048)
  - `Word = { text, start, end }` (relative to clip start)
- `PATCH /api/clips/{id}` body: any subset of `{ metadata fields, edit fields, words, blur_boxes }` → `Clip`. Text metadata edits do not mark the clip edited. Video-affecting fields set `edited=true` and clear `has_preview`/`has_export`. Validates: title ≤ 40 chars, trim inside `source_bounds`, resulting length 5–90 s (warning field `length_warning` if outside 25–90).
- `POST /api/clips/{id}/preview` → `202` (queues a preview render)
- `POST /api/clips/{id}/render` → `202` (queues the full-quality export)
- `POST /api/clips/{id}/retry` → `202`
- `GET /api/clips/{id}/media/{kind}` where kind ∈ `preview | export | thumbnail` → video/image with HTTP Range support. `404 NOT_FOUND` if not rendered.
- `GET /api/clips/{id}/download` → the export file with `Content-Disposition: attachment; filename="<safe title>.mp4"`. `409 CONFLICT` if no current export.

## Settings
- `GET /api/settings` → `Settings`
- `PUT /api/settings` body partial `Settings` → `Settings`
  - `Settings = { theme: "light"|"dark"|"system", style_defaults: StyleOverrides, whisper_model: "auto"|"tiny"|"base"|"small"|"medium", transcribe_engine: "parakeet"|"whisper", source_policy: "ask"|"delete"|"keep", storage_warn_gb, thorough_mode: bool }`
  - `StyleOverrides = { caption_style, caption_language, clip_length_mode: "standard"|"extended", clips_per_video: 1-5, blur, crop_mode, show_title: bool }`

## Storage
- `GET /api/storage` → `{ total_bytes, folders: {downloads, exports, previews, work, models}, warn: bool }`
- `POST /api/storage/cleanup` body `{ scope: "source_videos" | "previews" | "work" | "batch", batch_id? }` → `{ freed_bytes }`

## Rules
- Pagination with opaque cursors for list endpoints.
- Media endpoints serve only files recorded in the DB under `DATA_DIR`; reject anything else (path traversal).
- Request/response validation by Pydantic; unknown fields rejected.
- All times validated; invalid → `VALIDATION_ERROR` with `details`.
