# 11 — Constraints

## Hardware targets
| Tier | RAM | Whisper model (Auto) | Download cap | Notes |
|---|---|---|---|---|
| Minimum | 4 GB | `base` int8 (`tiny` if free RAM < 1.5 GB) | 720p | Slow but must work |
| Normal | 8 GB | `small` int8 | 1080p | |
| Good | 16 GB+ or NVIDIA GPU | `medium` (NVIDIA GPU with VRAM ≥ 4 GB: `large-v3-turbo` on the GPU, D-032) | 1080p | |
The user can override the model in Settings. Detect RAM with psutil at startup; show what Auto chose.

## Memory and CPU rules
- Backend target peak memory ≤ 1.5 GB at the `base` model during processing.
- Stream video: read frames one by one, write to ffmpeg through a pipe. Process audio signals in chunks. Never load a full video or a full long WAV into memory.
- Only **one** heavy job at a time. Never run whisper and ffmpeg encoding at once.
- ffmpeg: `-threads` limited to `max(1, cpu_count // 2)` on low-RAM machines; `-preset veryfast`.
- Free big objects (models) after use on low-RAM machines (unload Whisper after transcription).
- Measure and log stage durations and peak memory to `logs/app.log` to help tuning.

## Platforms
Windows 10/11 first; macOS and Linux supported. Therefore: use `pathlib`, never hard-code `/` or `\`; test paths with spaces; subprocess arg lists; no bash-only commands in Python; handle Windows' ffmpeg filter path escaping (see `05-video-layout-spec.md`); `start.bat` and `start.sh` both maintained.

## Network
Allowed: video platform (download, chat), user's LLM provider, package and model downloads during setup. Nothing else. No analytics, no update pings. The only auto-check is none; updating yt-dlp is a manual button.

## Security and privacy
- Backend listens on `127.0.0.1` only. CORS allows only `ALLOWED_ORIGIN`.
- URL allowlist: only hosts in `supported_hosts`. Reject everything else before calling yt-dlp (yt-dlp can fetch arbitrary sites).
- No `shell=True`; no string-built commands; validate numbers passed to ffmpeg.
- Media endpoints serve only DB-known files inside `DATA_DIR`.
- API key: memory only unless the user clicks **Save** (then encrypted at rest, D-053); redact in logs (`***`); never in URLs; never returned by the API; sent only in request bodies over localhost.
- Render LLM/user text as plain text in the UI.
- Add a short README note: users are responsible for having rights to the videos they clip and for following the platform's terms.

## Performance expectations (to measure, not promise)
Report in the README measured times for a 15-minute video on one low-spec and one normal machine after M10.

## Reliability
- Atomic file writes (write temp then rename) for JSON outputs.
- A stage's cached output is trusted only if it parses and passes a quick sanity check.
- Every subprocess has a timeout and is killed on cancel.
- Disk-space check before download and before render (`DISK_FULL`).
