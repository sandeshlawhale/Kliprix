# 05 — Video layout and caption spec

Canvas: **1080x1920**, 30 fps, MP4 (H.264 yuv420p, AAC 128 kbps, `+faststart`). Final: `-crf 20 -preset veryfast`. Preview: 540x960, `-crf 28 -preset ultrafast`.
All numbers below are constants in ONE file (`backend/app/render/layout_constants.py`). Nothing is hard-coded elsewhere. Values are tunable defaults; verify visually in milestone M6.

## Layers (bottom to top)
1. **Background:** the same clip scaled to *cover* 1080x1920 (crop the overflow), `boxblur` luma radius 30 (user blur slider maps 0–60), slightly darkened (`eq=brightness=-0.1`).
2. **Foreground:** the sharp main video, centered horizontally.
3. **Title** (top) and **captions** (below the foreground), both drawn from one ASS file.

## Foreground by crop mode
| Mode | Foreground size | Source |
|---|---|---|
| Face crop (4:5) | 1080x1350 | face-following crop of the source |
| Full frame | 1080 wide, height by aspect (16:9 → 1080x608) | whole source frame, no crop |

Foreground top `fg_top = 240` for 4:5 (bottom = 1590). For full frame: center vertically at y=900, so `fg_top = 900 − h/2`.

### Gaming layouts (D-052)
Auto uses these for a gaming video (a D-050 game strategy from the title/channel, or game screens ≥ 85% of the video); face/full stay for everything else.
| Mode | Canvas | Source |
|---|---|---|
| `facecam` | cam 1080x576 at y 0, game 1080x1344 at y 576, no blur | the streamer's cam (found by tiled face detection, snapped to its border, trimmed 2%) cropped around the face; the game full height, centered, moved off the cam if they overlap |
| `center` | 1080x1440 at y 240 over the blurred background | the middle 3:4 of the game |
Title: on the seam (y 576) for `facecam`; as usual for `center`. A forced `facecam` without a cam falls back to `center` with a warning.

## Auto crop-mode rule
Sample every 3rd frame; if a face is detected in ≥ **40%** of samples → Face crop, else Full frame. The user can override per clip (Auto / Face / Full). If Face is forced but no face is found, fall back to a center crop and show a warning on the clip.

## Face tracking rules
- Detector: MediaPipe Face Detector (Tasks API, short-range model), confidence ≥ 0.5. Check the installed version's docs; the older `mp.solutions` API is deprecated. Fallback detector if MediaPipe cannot be installed: OpenCV YuNet.
- Several faces: pick the largest; switch target only if another face is ≥ 1.4x larger for ≥ 1 s (hysteresis).
- No face for a stretch: hold the last position (fade toward center after 3 s).
- Smoothing: exponential or Savitzky–Golay on crop center x, plus a dead zone (ignore movement < 3% of frame width), plus a max pan speed. Reset smoothing at scene cuts.
- Process frames in a stream: decode → crop → write raw frames to ffmpeg stdin. Never keep frames in a list.
- Crop window is 4:5 of the source height (`crop_w = src_h * 4/5`), clamped inside the frame. If the source is narrower than that, scale up.

## Title
- Text from `clip.title` (max 7 words, max 40 characters, set by the LLM; the editor enforces it).
- Position: horizontally centered, vertical center `max(150, fg_top − 110)`.
- Font: Anton (OFL), 88 px, uppercase, white, black outline 6 px, soft shadow. Max 2 lines, auto-shrink to fit width 960 px (minimum 60 px).
- Animation: 250 ms fade-in at clip start; stays for the whole clip.

## Captions
- Position: horizontally centered, vertical center `min(1344, fg_bottom + 130)`: always at least 30% above the bottom (owner, D-052). Keep inside the bottom safe margin (bottom 160 px stays empty for app UI overlays).
- Max line width 900 px; 1–2 lines; show 2–4 words at a time (split by punctuation, pauses > 0.35 s, and max 18 characters per line).
- Font: Montserrat ExtraBold (OFL), 72 px, white, black outline 5 px. Devanagari fallback: Noto Sans Devanagari (OFL).
- Words come from `captions_json` of the clip (editable by the user). Times are relative to the clip start.
- Fonts are bundled in `backend/assets/fonts/` with their license files; ffmpeg is called with `fontsdir`.

### The 3 styles (selectable; names are API values)
| Name | Behavior |
|---|---|
| `pop` | One chunk (1–3 words) at a time. Each chunk scales 70% → 100% over 120 ms with a small overshoot. Active chunk is white; the current word is yellow `#FFD60A`. |
| `karaoke` | A 2-line phrase (up to ~4 words) stays on screen; the word being spoken switches to yellow while spoken words stay white and upcoming words are slightly dimmed (70% opacity). |
| `boxed` | Phrase on screen like karaoke, but the active word sits on a filled rounded box (accent color token, white text). Implementation: per-word `Dialogue` events with a thick outline in the box color applied only to the active word; if libass rendering looks wrong, fall back to `BorderStyle=3` boxing the whole phrase. Verify visually. |

Implementation rule: generate **one `Dialogue` event per word-time slice** with inline override tags. Do not rely on `\k` tags (not enough control). PlayResX/PlayResY = 1080/1920 so pixel values map 1:1.

## ffmpeg graph (reference shape, adapt, do not copy blindly)
```
[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5,eq=brightness=-0.1[bg];
[0:v]scale=1080:-2[fg];
[bg][fg]overlay=(W-w)/2:{fg_top}[v];
[v]ass=captions.ass:fontsdir=assets/fonts[out]
```
Audio is mapped from the cut clip. All subprocess calls use arg lists, never `shell=True`. Paths with spaces (Windows) must work; escape ASS/filter paths correctly (a classic Windows bug: backslashes and the drive colon in the `ass=` filter path; run ffmpeg with `cwd` set to the work folder and use relative filenames).

## Quality checks (run in M6)
- Extract frames at 1 s, 5 s, 10 s from a rendered clip and view them: title not clipped, captions not overlapping foreground, face centered, blur visible.
- Audio and video in sync (duration difference < 100 ms).
- Verify with a text-only fixture (generated by ffmpeg `testsrc`, no copyrighted media in the repo).
