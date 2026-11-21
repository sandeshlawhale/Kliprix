"""Backend settings read from environment variables, with documented defaults.

See docs/06-architecture.md ("Environment variables"). No secrets are read here:
API keys live in memory only and never pass through configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "Kliprix"
APP_VERSION = "0.1.0"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8000
DEFAULT_ALLOWED_ORIGIN = "http://localhost:3000"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")

# Hosts accepted for source links (docs/07, D-021: YouTube only in v1).
SUPPORTED_HOSTS = ("youtube.com", "youtu.be")

# Data layout below DATA_DIR (docs/08-data-models.md).
DATA_SUBDIRS = ("db", "downloads", "work", "previews", "thumbnails", "exports", "models", "logs")
DB_FILE_NAME = "app.sqlite"
LOG_FILE_NAME = "app.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# SQLite connection pragma (docs/08): wait up to this long for a locked database.
SQLITE_BUSY_TIMEOUT_MS = 5000

# Timeout for quick tool probes such as `ffmpeg -version` or `nvidia-smi`.
PROBE_TIMEOUT_S = 15.0

BYTES_PER_GB = 1024**3

# Sources (docs/04 "Download", docs/07 "Videos").
MAX_VIDEOS_PER_BATCH = 3
MAX_URL_LENGTH = 2048
# Download height cap: 1080p normally, 720p when total RAM is below this (docs/04).
DOWNLOAD_MAX_HEIGHT = 1080
DOWNLOAD_LOW_RAM_MAX_HEIGHT = 720
DOWNLOAD_LOW_RAM_GB = 6.0
# yt-dlp format sort: highest resolution first, then H.264 over AV1/VP9/HEVC at the same
# resolution because it decodes cheapest on low-spec PCs (D-026). "vcodec:h264" ranks codecs
# that yt-dlp normally prefers over H.264 below it (yt-dlp FormatSorter, limit semantics).
DOWNLOAD_FORMAT_SORT = ("res", "vcodec:h264")
# Seconds yt-dlp waits on an unresponsive connection before retrying.
YTDLP_SOCKET_TIMEOUT_S = 30.0
# JavaScript runtime yt-dlp uses for YouTube's player challenges (needs Node 22+, AA-017).
YTDLP_JS_RUNTIME = "node"
# Disk check before download: estimated size times this factor plus a reserve; when
# YouTube reports no size, require DOWNLOAD_UNKNOWN_SIZE_MIN_FREE_GB (AA-019).
DOWNLOAD_SIZE_FACTOR = 1.1
DOWNLOAD_RESERVE_GB = 0.5
DOWNLOAD_UNKNOWN_SIZE_MIN_FREE_GB = 2.0
# yt-dlp sometimes cannot delete the separate video/audio parts after merging because another
# program (OneDrive sync, antivirus) briefly holds them; we retry this often, this far apart.
LEFTOVER_DELETE_ATTEMPTS = 5
LEFTOVER_DELETE_WAIT_S = 1.0
# `pip install --upgrade yt-dlp yt-dlp-ejs` for "Update downloader".
DOWNLOADER_UPDATE_TIMEOUT_S = 300.0
DOWNLOADER_PACKAGES = ("yt-dlp", "yt-dlp-ejs")


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration for one backend process."""

    backend_host: str
    backend_port: int
    allowed_origin: str
    data_dir: Path
    log_level: str
    ffmpeg_path: str | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / DB_FILE_NAME

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def resolve_data_dir(raw: str) -> Path:
    """Turn a DATA_DIR value into an absolute path.

    Relative paths are relative to the repo root, so `./data` means the same folder
    whether the backend starts from the repo root or from `backend/`.
    """
    if not raw.strip():
        return DEFAULT_DATA_DIR
    path = Path(raw.strip()).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def load_config() -> AppConfig:
    """Build the configuration from the environment.

    Raises:
        ValueError: if BACKEND_PORT is not an integer or LOG_LEVEL is unknown.
    """
    log_level = os.environ.get("LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()
    if log_level not in LOG_LEVELS:
        raise ValueError(f"LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}")
    ffmpeg_path = os.environ.get("FFMPEG_PATH", "").strip()
    return AppConfig(
        backend_host=os.environ.get("BACKEND_HOST", DEFAULT_BACKEND_HOST),
        backend_port=int(os.environ.get("BACKEND_PORT", str(DEFAULT_BACKEND_PORT))),
        allowed_origin=os.environ.get("ALLOWED_ORIGIN", DEFAULT_ALLOWED_ORIGIN),
        data_dir=resolve_data_dir(os.environ.get("DATA_DIR", "")),
        log_level=log_level,
        ffmpeg_path=ffmpeg_path or None,
    )


def ensure_data_dirs(config: AppConfig) -> None:
    """Create DATA_DIR and its documented subfolders if missing."""
    for name in DATA_SUBDIRS:
        (config.data_dir / name).mkdir(parents=True, exist_ok=True)


# Signals and candidates (docs/04 stages 4-6). All signals are on a 1-second grid.
AUDIO_SAMPLE_RATE = 16000
# Audio is analysed in chunks of this many seconds so a long WAV never sits in memory.
AUDIO_CHUNK_S = 60
# RMS window and hop in samples (0.1 s hop, 0.2 s window at 16 kHz).
AUDIO_RMS_FRAME = 3200
AUDIO_RMS_HOP = 1600
# Spectral flux (librosa onset strength) hop in samples.
AUDIO_FLUX_HOP = 512
# Motion: frames sampled per second and the analysis width in pixels (grayscale).
MOTION_FPS = 1
MOTION_WIDTH = 160
# Videos at least this long decode only keyframes for motion (D-033): 14x faster on 10 min of
# a 1080p60 stream (2.9 s vs 41.6 s). Shorter videos keep the exact 1 fps pass.
MOTION_KEYFRAME_MIN_DURATION_S = 10 * 60
# Keyframe mode needs keyframes at most this far apart (median over the first
# MOTION_KEYFRAME_PROBE_S seconds, read from packets without decoding); else a full decode.
MOTION_KEYFRAME_MAX_GAP_S = 10.0
MOTION_KEYFRAME_PROBE_S = 120
# ffmpeg timeouts for the signal stages: a fixed allowance plus this much per second of video.
SIGNAL_TIMEOUT_BASE_S = 120.0
SIGNAL_TIMEOUT_PER_VIDEO_S = 2.0
# Chat reacts after the event, so the chat series is shifted earlier by this many seconds.
CHAT_LAG_S = 10
# Extra weight a chat message gets when it contains a laugh/hype token.
CHAT_HYPE_WEIGHT = 1.0
CHAT_HYPE_TOKENS = ("lol", "lmao", "😂", "pog", "wtf", "clip it", "gg")
# Scoring: moving-average window, robust z-score clip range, default signal weights.
SMOOTH_WINDOW_S = 5
ROBUST_Z_MAX = 6.0
# Median absolute deviation to standard deviation for normally distributed data.
MAD_TO_SIGMA = 1.4826
SIGNAL_WEIGHTS: dict[str, float] = {"chat": 0.40, "audio": 0.35, "motion": 0.25}
CANDIDATE_COUNT = 12
CANDIDATE_MIN_DISTANCE_S = 60
CANDIDATE_BEFORE_S = 25
CANDIDATE_AFTER_S = 35
# Edges (D-033): no peak and no window in the first 3% of the video (at most 3 min) or the last
# 1.5% (at most 2 min). Proportional, so a 10-min video loses 18 s and a long stream its
# "starting soon" screen and goodbyes.
CANDIDATE_EDGE_LEAD_SHARE = 0.03
CANDIDATE_EDGE_LEAD_MAX_S = 180.0
CANDIDATE_EDGE_TAIL_SHARE = 0.015
CANDIDATE_EDGE_TAIL_MAX_S = 120.0
# Agreement (D-033): each second's score is multiplied by a factor from its second-strongest
# normalized signal: 1 at AGREEMENT_LEVEL or above, down to AGREEMENT_FLOOR when one signal
# fires alone (e.g. a chat flood over a silent, still screen).
AGREEMENT_LEVEL = 0.3
AGREEMENT_FLOOR = 0.5

# Transcription (docs/04 stage 7, D-012). Videos up to this length are transcribed whole.
FULL_TRANSCRIBE_MAX_S = 20 * 60
# Seconds of context added before and after each candidate window.
TRANSCRIBE_MARGIN_S = 30
# Longest piece of audio handed to Whisper at once (bounds memory on long spans).
TRANSCRIBE_CHUNK_S = 600
# faster-whisper's own default beam size.
WHISPER_BEAM_SIZE = 5

# Parakeet engine (D-028): NVIDIA Parakeet-TDT-0.6b-v2 int8 through sherpa-onnx, English only.
PARAKEET_MODEL_NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
PARAKEET_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    f"{PARAKEET_MODEL_NAME}.tar.bz2"
)
# Longest piece handed to Parakeet at once. Measured on a 15-min stream: 30 s pieces peak at
# 1.54 GB, 60 s pieces at 2.22 GB with no speed gain.
PARAKEET_PIECE_S = 30.0
# Voice detection (Silero, as Whisper's vad_filter) decides where pieces start and end, so a
# cut never lands inside a word: a pause this long ends a speech region, and padding keeps
# quiet word edges.
PARAKEET_VAD_MIN_SILENCE_MS = 500
PARAKEET_VAD_PAD_MS = 200
# A word's highlight ends at most this long after it starts. Parakeet gives the last word
# before a pause the pause's length (measured up to 4.7 s); real words are below 1.2 s.
PARAKEET_WORD_MAX_S = 1.2
# A new caption segment starts after sentence punctuation or a pause this long.
PARAKEET_SEGMENT_GAP_S = 1.0
# The same word on both sides of a piece boundary within this gap is one word said once.
PARAKEET_DUPLICATE_GAP_S = 0.4
# More threads were slower on a 6-core/12-thread CPU (4: 111 s, 6: 124 s for 15 min).
PARAKEET_MAX_THREADS = 4
# Model download: socket timeout per read and bytes per read.
MODEL_DOWNLOAD_TIMEOUT_S = 30.0
MODEL_DOWNLOAD_BLOCK_BYTES = 1024 * 1024

# LLM (docs/10). Timeout per call, temperatures, backoff waits on 429/5xx before giving up.
LLM_TIMEOUT_S = 90.0
LLM_SELECT_TEMPERATURE = 0.3
LLM_TRANSLITERATE_TEMPERATURE = 0.2
LLM_BACKOFF_S = (2.0, 6.0, 15.0)
# Listing models and the connection test: the provider must answer within this time.
LLM_LIST_TIMEOUT_S = 20.0
# The "Test connection" button gives up after this long (batches keep LLM_TIMEOUT_S).
LLM_TEST_TIMEOUT_S = 20.0
# Prompt hygiene: each transcript/chat line is capped, and so is the whole prompt.
LLM_LINE_MAX_CHARS = 300
LLM_PROMPT_MAX_CHARS = 60000

# Clip length rules (docs/04 "Select", D-017): hard range 25-90 s, target 30-60 s, up to
# 90 s only when the LLM flags needs_extended and the style allows it.
CLIP_MIN_S = 25.0
CLIP_TARGET_MIN_S = 30.0
CLIP_STANDARD_MAX_S = 60.0
CLIP_EXTENDED_MAX_S = 90.0
CLIP_MIN_GAP_S = 30.0
# Sentence edges (D-033): a sentence also ends at a pause this long (Hinglish transcripts have
# little punctuation; on the owner's 4-hour stream 90% of pauses between words are < 0.64 s).
SENTENCE_PAUSE_S = 0.5
# Clip edges move to the nearest sentence start / end within this many seconds.
SENTENCE_SNAP_S = 6.0
# Room kept before the first word and after the last word, never into a neighbouring word.
CLIP_LEAD_PAD_S = 0.15
CLIP_TAIL_PAD_S = 0.25
# Speech check (D-033), after transcription: words per second in each candidate window. Full
# credit at SPEECH_FULL_WPS; a silent window keeps SPEECH_FLOOR of its score. Skipped when the
# windows together have under SPEECH_VIDEO_MIN_WPS (music, gameplay without commentary).
# Clips filled without the LLM need at least SPEECH_FALLBACK_MIN_WPS.
SPEECH_FULL_WPS = 1.0
SPEECH_FLOOR = 0.3
SPEECH_VIDEO_MIN_WPS = 0.5
SPEECH_FALLBACK_MIN_WPS = 0.3
# Clip times must stay within the candidate region widened by this much.
CLIP_REGION_MARGIN_S = 30.0

# Rendering (docs/05, 11): ffmpeg timeout = base + this many seconds per second of clip;
# minimum free disk space before a render starts.
RENDER_TIMEOUT_BASE_S = 300.0
RENDER_TIMEOUT_PER_CLIP_S = 30.0
RENDER_MIN_FREE_GB = 1.0
