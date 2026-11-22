# 04 — Processing pipeline (backend)

Order per video. Each stage reads and writes files under `data/` (see `08-data-models.md`). A stage is skipped when its output file already exists and is valid (caching). Progress is real: percent comes from actual work done, never faked.

| # | Stage | Input | Output | Tool |
|---|---|---|---|---|
| 1 | inspect | URL | metadata (title, duration, thumbnail, chat availability) | yt-dlp (no download) |
| 2 | download | URL | `source.mp4`, `info.json` | yt-dlp + ffmpeg |
| 3 | chat | URL / info | `chat.json` (list of `{t, text}`) or none | yt-dlp live_chat subtitle; chat-downloader as fallback |
| 4 | audio | `source.mp4` | `audio.wav` (16 kHz mono), `signals/audio.json` | ffmpeg + librosa |
| 5 | motion | `source.mp4` | `signals/motion.json` | ffmpeg (1 fps, 160 px gray) + numpy |
| 6 | candidates | signals | `candidates.json` (top 12 windows) | numpy / pandas |
| 7 | transcribe | candidate spans + audio | `transcript.json` (word timestamps) | faster-whisper |
| 8 | select | candidates + transcript | `selection.json` (≤5 clips + metadata) | LLM via LiteLLM |
| 9 | per clip: cut | source + times | `work/<clip>/cut.mp4` | ffmpeg |
| 10 | per clip: face crop | cut.mp4 | `work/<clip>/crop.mp4` | MediaPipe + OpenCV + ffmpeg pipe |
| 11 | per clip: captions | words + style | `work/<clip>/captions.ass` | own ASS generator |
| 12 | per clip: render | crop + ass + title | `exports/.../clip_N.mp4` | ffmpeg |

## Key design decision: candidate-first transcription
Transcribing a 2-hour stream on a 4 GB CPU is too slow. So we find candidate moments from cheap signals first (stages 3–6), then transcribe **only candidate spans** (candidate ± 30 s, overlapping spans merged). If the video is ≤ 20 minutes, or the user enables "Thorough mode", transcribe the whole video.

## Stage details

### 2. Download
Format: best video up to 1080p + best audio, merged to MP4 (`bestvideo[height<=1080]+bestaudio/best[height<=1080]`). If detected RAM < 6 GB use a 720p cap. Never run yt-dlp through a shell string; use the Python API or an args list. Map errors to codes in `07-api-contract.md` (private, age-restricted, geo-blocked, removed, network, ffmpeg-missing).

### 3. Chat replay
Only exists for past live streams / premieres. Try; if absent set `has_chat=false` and continue. Chat message timestamps are in seconds from video start.

### 4–5. Signals
All signals are resampled to a **1-second grid**.
- **audio:** RMS loudness + spectral flux. Compute in chunks (never load a full 2-hour WAV into RAM at once).
- **motion:** mean absolute frame difference at 1 fps, 160 px wide grayscale, streamed from ffmpeg.
- **sound events (D-041):** `audio.wav` in 2 s windows through CED-mini audio tagging (AudioSet's 527 classes; sherpa-onnx, int8, 47 MB downloaded at first use). Classes are grouped (gunfire, explosion, shouting, laughter, cheering, crash, vehicle; speech and music as facts only); the `sound` signal is the weighted sum of the action groups per second, and `signals/sound_events.json` keeps each window's group values (≥ 0.1) for the LLM and the chat delay. Runs right after the audio signal in the same thread; optional like the screen type.
- **screen type (D-040):** one frame every 5 s (10 s over 30 min; keyframes only on long videos) through OpenAI CLIP ViT-B/32 (int8 ONNX, downloaded at first use, 89 MB), compared zero-shot with fixed labels grouped as gameplay, menu (main menu, lobby, character/agent select, loading, scoreboard, store, inventory), waiting (black screen), talking, other. Saved as `signals/screen.json` (per frame, the share of each group). Runs at the same time as audio and motion; optional: if it fails, the video goes on without it and its note says so.
- **chat:** messages per second; a reaction message (D-042 groups: laugh, hype, shock, fail; regexes in `CHAT_GROUP_PATTERNS`, very common greetings like "bro"/"bhai" left out) counts double. The series is shifted earlier by the chat delay: measured per stream by correlating reaction messages with the `sound` signal (lags 3-30 s, middle 80% of the stream), used only when the curve has a clear peak (≥ 0.1 and ≥ 0.05 above its median); otherwise the default 10 s (`CHAT_LAG_S`).
- **chat-led windows (D-042):** when chat is the strongest normalized signal at a peak, the window looks back: chat reacts at peak + delay (`reaction_t`), the likely cause is the strongest sound event (else loudness) in the 30 s before it (`cause_t`, when ≥ 0.2), and the window runs from 15 s before the cause (40 s before the peak without one) to 10 s after the reaction, at most 90 s.

### 6. Candidates (scoring)
1. Smooth each signal with a 5 s moving average.
2. Normalize with a robust z-score (median / MAD), clip to [0, 6], then scale to 0–1.
3. Weighted sum. Default weights (D-041): chat 0.35, sound 0.35, audio 0.15, motion 0.15. If a signal is missing, drop it and renormalize the rest. Weights live in `config`, not hard-coded in logic.
4. Pick local maxima with minimum distance 60 s, take the top **12**.
5. Each candidate: `{peak_t, start, end, score, signals:{chat,audio,motion}}` where start = peak − 25 s, end = peak + 35 s (clamped to video bounds).

**D-033:** audio and motion run at the same time; motion reads only keyframes on videos ≥ 10 min with keyframes ≤ 10 s apart. The weighted sum is multiplied by an agreement factor (second-strongest signal: 1 at ≥ 0.3, down to 0.5 when one signal fires alone). No peak or window in the first 3% (max 3 min) or the last 1.5% (max 2 min) of the video.

**D-040:** the per-second score (and tiebreak) is multiplied by a screen factor: the menu+waiting share smoothed over 30 s, 1 up to 0.45, down to 0.15 from 0.65. Coverage windows are only placed where the factor is 1. Each candidate stores the mean screen shares of its window (`screen`), shown to the LLM as "On screen: gameplay 80%, menu/lobby/loading 20%"; a window that is ≥ 45% menu/waiting is never used as a fallback clip.

### 7. Transcribe
faster-whisper, int8 on CPU (CUDA if available), `word_timestamps=True`, `vad_filter=True`. Model chosen from RAM (see `11-constraints.md`). Language: auto-detect, unless the style says Hinglish.
**Parakeet engine (D-028):** setting `transcribe_engine` (`parakeet` default, `whisper`). Parakeet (sherpa-onnx, English-only model) runs when the PC reaches the 8 GB tier; else Whisper. The Transcribe checkpoint carries the running `engine` (D-030 debug badge; Whisper adds its device, e.g. `Whisper medium · GPU`). Hinglish always uses Whisper (D-031); Whisper uses the GPU when NVIDIA's libraries are installed (`requirements-gpu.txt`). Inside each span Silero VAD finds speech, silent parts are skipped, and pieces of at most 30 s are cut only in pauses; word ends are capped at 1.2 s after the start; a word heard on both sides of a cut is kept once. Any Parakeet failure falls back to Whisper for that video (logged, shown in the video's `note`). `transcript.json` records `engine`; a cached transcript is reused only by the same engine.
**Hinglish handling:** Hinglish means Hindi written with English letters, e.g. "Namaste kaise ho aap?" (never Devanagari). Whisper has no Hinglish mode and often outputs Hindi in Devanagari. Steps: (a) pass an `initial_prompt` written in Roman Hinglish to bias toward Roman letters; (b) if the output still contains Devanagari characters, transliterate to Roman script. Transliteration runs only on the words of the selected clips, through the LLM, using the strict 1:1 word-array format in `10-llm-prompts.md` so timestamps stay aligned; validate array length. If the LLM fails, keep Devanagari and use the bundled Noto Sans Devanagari font for those captions.

### 8. Select
One LLM call per video (see `10-llm-prompts.md`). The LLM receives the candidate list with transcript text and returns up to N clips with final start/end snapped to sentence boundaries or silence, plus metadata. Backend then **validates and repairs**: length 25–90 s (target 30–60), no overlap between clips, minimum 30 s gap, times inside the video, no duplicates. If the LLM returns fewer valid clips than requested, fill from remaining top candidates with auto-generated boundaries and a fallback title from the transcript.
**D-033:** before the call, candidates are re-ranked by words per second from the transcript (a `speech` signal; skipped for videos with little speech), and windows with < 0.3 words/s are never used as fallback clips. Every clip edge snaps to the nearest sentence start/end within 6 s (sentence end = . ? ! । … or a ≥ 0.5 s pause), with 0.15 s / 0.25 s of room that never reaches a neighbouring word.
**D-046:** with the video at hand, selection runs in two rounds: the LLM drafts 2× the clips wanted (max 10); each draft is analysed from 20 s before to 20 s after it (screen type, sounds, on-screen text, speech and scene ends every 2 s, plus 5 frames); a second LLM call keeps, fixes or drops each draft and picks the best; the backend keeps every clip inside one scene. About 2.5 minutes for 10 drafts on a 25-min 1080p60 video.
**D-045:** right before the LLM call, RapidOCR reads 3 frames around each of the first 16 candidates (stream overlays dropped) and each candidate gets an `On-screen text: …` line. Optional; cached in `signals/screen_text.json`.
**D-044:** the LLM sees the transcript from 90 s before each candidate and may start a clip that early; every clip whose first word continues something ("so", "and", "exactly", "toh", …) starts at the previous sentence instead (up to twice, within 25 s, within the maximum length).
If a video has no speech at all, the LLM gets candidates with signal scores only and writes generic metadata; clips still produce.

### 9. Cut
Cut from `source.mp4` with a few seconds of handle for the editor (store `handle_before=5`, `handle_after=5` so trim can extend). Re-encode (not stream copy) to ensure accurate frame cuts.

### 10. Face crop (details in `05-video-layout-spec.md`)
Sample every 3rd frame at ≤480 px width. Decide crop mode automatically (face present in ≥ 40% of samples → face crop, else full frame). Smooth and limit camera movement. Stream frames to ffmpeg through a pipe; never hold the clip in memory.

### 11–12. Captions and render
See `05-video-layout-spec.md`. One ffmpeg `filter_complex` pass produces the final file. Preview uses the same code with `scale=540:960`, `-preset ultrafast`, `-crf 28`.

## Resumability
A `jobs` row per unit of work (video-level and clip-level). On restart, jobs in `running` go back to `queued`; stages whose output files exist are skipped.

## Cancellation
User can cancel a batch: running ffmpeg/yt-dlp subprocess is terminated, queued jobs are marked `cancelled`, partial files in `work/` are removed.

**Pause (D-049):** the running subprocess is terminated as on a shutdown, but nothing is removed: the batch's jobs become `paused` and are skipped by the runner; resume queues them again and the stopped stage starts over, skipping stages whose outputs are saved.

## Checkpoints shown in the UI
The UI checkpoint list is derived from real pipeline state. Mapping (stage → checkpoint):

| Checkpoint key | Label | Done when | Skipped when |
|---|---|---|---|
| `download` | Download video | `source.mp4` exists and validates | never |
| `chat` | Fetch chat replay | `chat.json` saved | video has no chat replay |
| `signals` | Analyze audio and motion | audio and motion signal files saved | never |
| `candidates` | Find highlights | `candidates.json` saved | never |
| `transcribe` | Transcribe | transcript for all candidate spans saved | never (a silent video still completes with an empty transcript) |
| `select` | Pick clips | `selection.json` saved and clips created | never |
| `edit` | Edit clips | all clips of the video are Done (progress `done/total`) | never |

States per checkpoint: `pending`, `running`, `done`, `skipped`, `failed`. The backend computes them from stage outputs plus the current stage, so they stay correct after a restart (cached outputs count as done).
