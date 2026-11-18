"""Every layout, caption, and face-tracking number from docs/05 in one place.

Values are tunable starting points (A-007); nothing else in the code hard-codes them.
Colors are "#RRGGBB"; sizes are pixels on the 1080x1920 canvas.
"""

from __future__ import annotations

# Canvas and encoding.
CANVAS_W = 1080
CANVAS_H = 1920
FPS = 30
PREVIEW_W = 540
PREVIEW_H = 960
FINAL_CRF = 20
FINAL_PRESET = "veryfast"
PREVIEW_CRF = 28
PREVIEW_PRESET = "ultrafast"
AUDIO_BITRATE = "128k"
# Intermediate files (cut.mp4, crop.mp4) are encoded near-lossless so the final pass decides.
INTERMEDIATE_CRF = 18
INTERMEDIATE_PRESET = "veryfast"
INTERMEDIATE_AUDIO_BITRATE = "192k"
THUMBNAIL_W = 360
THUMBNAIL_AT_S = 1.0
THUMBNAIL_QUALITY = 3

# Background: the clip scaled to cover the canvas, blurred, darkened. The blur runs on a
# frame this many times smaller (same look, far less CPU), so the radius is divided too.
BG_BRIGHTNESS = -0.1
BLUR_DEFAULT = 30
BLUR_MAX = 60
BG_BLUR_DOWNSCALE = 4
BG_BLUR_POWER = 2

# Foreground.
FACE_FG_W = 1080
FACE_FG_H = 1350
FACE_FG_TOP = 240
FULL_FG_W = 1080
FULL_FG_CENTER_Y = 900
# Face crop window aspect (width:height = 4:5).
FACE_CROP_ASPECT = 4 / 5

# Title.
TITLE_FONT = "Anton"
TITLE_FONT_FILE = "Anton-Regular.ttf"
TITLE_SIZE = 88
TITLE_MIN_SIZE = 60
TITLE_SIZE_STEP = 2
TITLE_MAX_W = 960
TITLE_MAX_LINES = 2
TITLE_OUTLINE = 6
TITLE_SHADOW = 3
TITLE_FADE_MS = 250
TITLE_MIN_CENTER_Y = 150
TITLE_GAP = 110
# Line height as a multiple of the font size (for placing a 2-line title).
LINE_HEIGHT = 1.15

# Captions.
CAPTION_FONT = "Montserrat ExtraBold"
CAPTION_FONT_FILE = "Montserrat-ExtraBold.ttf"
DEVANAGARI_FONT = "Noto Sans Devanagari"
DEVANAGARI_FONT_FILE = "NotoSansDevanagari-Bold.ttf"
CAPTION_SIZE = 72
CAPTION_OUTLINE = 5
CAPTION_MAX_W = 900
CAPTION_MAX_CHARS_PER_LINE = 18
CAPTION_MAX_LINES = 2
CAPTION_MAX_WORDS = 4
POP_MAX_WORDS = 3
# A pause longer than this between two words starts a new caption chunk.
CAPTION_PAUSE_S = 0.35
# A chunk stays this long after its last word unless the next chunk starts earlier.
CAPTION_LINGER_S = 0.3
CAPTION_MAX_CENTER_Y = 1700
CAPTION_GAP = 130
BOTTOM_SAFE = 160

# Colors.
TEXT_COLOR = "#FFFFFF"
OUTLINE_COLOR = "#000000"
SHADOW_COLOR = "#000000"
ACTIVE_COLOR = "#FFD60A"
# Placeholder accent until branding is decided (A-003).
BOX_COLOR = "#2563EB"
BOX_BORDER = 14
# Upcoming karaoke words are drawn at 70% opacity.
UPCOMING_OPACITY = 0.7
SHADOW_OPACITY = 0.6

# Pop animation: scale from 70% to an overshoot, then settle at 100% after 120 ms.
POP_START_SCALE = 70
POP_OVERSHOOT_SCALE = 108
POP_OVERSHOOT_MS = 84
POP_TOTAL_MS = 120

# Face tracking (docs/05 "Face tracking rules").
FACE_SAMPLE_EVERY = 3
FACE_ANALYSIS_MAX_W = 480
FACE_MIN_CONFIDENCE = 0.5
FACE_PRESENT_RATIO = 0.4
FACE_SWITCH_RATIO = 1.4
FACE_SWITCH_HOLD_S = 1.0
FACE_HOLD_S = 3.0
# After FACE_HOLD_S without a face, the crop drifts back to the center over this long.
FACE_RECENTER_S = 2.0
# Movement smaller than this fraction of the frame width is ignored.
FACE_DEAD_ZONE = 0.03
# Exponential smoothing factor per sample (0-1, higher follows faster).
FACE_SMOOTHING = 0.25
# The crop center moves at most this fraction of the frame width per second.
FACE_MAX_PAN_PER_S = 0.6
# Mean absolute difference (0-255) between sampled frames that counts as a scene cut.
SCENE_CUT_DIFF = 40.0
SCENE_CUT_THUMB_W = 64

# Cut handles kept around each clip so trim can extend (docs/04 "Cut").
HANDLE_BEFORE_S = 5.0
HANDLE_AFTER_S = 5.0
