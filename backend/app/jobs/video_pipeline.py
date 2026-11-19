"""The `video_pipeline` job: inspect → download → chat → audio → motion → candidates →
transcribe → select → clips (docs/04). Each stage is skipped when its output is cached, so a
restarted job resumes where it stopped. Progress comes from the work actually done.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.hardware import MemoryInfo, pick_whisper_model
from app.core.metrics import stage_timer
from app.core.settings_schema import Settings
from app.db.models import VideoRow
from app.db.repositories import batches as repo
from app.db.repositories.settings import load_settings
from app.jobs.context import JobContext
from app.llm.client import LlmClient
from app.llm.transliterate import transliterate_words
from app.pipeline.audio import AUDIO_FILE_NAME, run_audio
from app.pipeline.candidates import run_candidates
from app.pipeline.chat import run_chat
from app.pipeline.download import max_download_height, run_download
from app.pipeline.hinglish import to_hinglish
from app.pipeline.motion import run_motion
from app.pipeline.select import VideoFacts, all_words, run_select, words_between
from app.pipeline.signals import SignalFile, run_chat_signal
from app.pipeline.stt_engine import choose_engine
from app.pipeline.transcribe import (
    CaptionLanguage,
    Span,
    SpeechRunner,
    TranscriptFile,
    Word,
    plan_spans,
    run_transcribe,
)
from app.sources.registry import resolve

logger = logging.getLogger(__name__)

# Share of the video's progress bar for each stage (end of stage), from measured stage costs.
PROGRESS_AT = {
    "inspect": 0.01,
    "download": 0.35,
    "chat": 0.37,
    "audio": 0.42,
    "motion": 0.55,
    "candidates": 0.56,
    "transcribe": 0.92,
    "select": 0.99,
    "clips": 1.0,
}
_STAGES = list(PROGRESS_AT)
# Smallest change of the video's progress (0-1) worth saving and sending to the UI.
PROGRESS_STEP = 0.005

# The video's note says which speech-to-text engine ran and why (D-028).
PARAKEET_NOTE = "Speech-to-text: Parakeet."
# Shown on the Transcribe step (D-030, temporary debug display).
PARAKEET_LABEL = "Parakeet"
PARAKEET_MISSING_NOTE = "Parakeet is not available in this app, so Whisper was used."
MODEL_DOWNLOAD_NOTE = "Downloading the Parakeet speech model (first use, 480 MB):"
# Parakeet errors that fall back to Whisper; anything else (e.g. cancel) is not a fallback.
PARAKEET_FALLBACK_CODES = frozenset(
    {ErrorCode.TRANSCRIBE_FAILED, ErrorCode.MODEL_DOWNLOAD_FAILED, ErrorCode.DISK_FULL}
)


WHISPER_DEVICE_NAMES = {"cuda": "GPU", "cpu": "CPU"}


def _whisper_note(model: str, reason: str | None) -> str:
    used = f"Speech-to-text: Whisper {model}."
    return f"{reason} {used}" if reason else used


class VideoPipeline:
    def __init__(self, ctx: JobContext, video_id: str, cancel: threading.Event) -> None:
        self.ctx = ctx
        self.video_id = video_id
        self.cancel = cancel
        self._last_progress = -1.0
        self._download_percent = -1
        self._engine_label: str | None = None

    # ----- state updates (each in its own short transaction) -----

    def _update(self, **fields: Any) -> VideoRow:
        with self.ctx.session_factory() as session:
            video = repo.get_video(session, self.video_id)
            if video is None:
                raise AppError(ErrorCode.NOT_FOUND, "The video was deleted.")
            for key, value in fields.items():
                setattr(video, key, value)
            session.commit()
            self.ctx.publish(
                video.batch_id, "video", id=video.id, status=video.status,
                stage=video.stage, progress=video.progress,
            )  # fmt: skip
            return video

    def _stage(self, stage: str) -> None:
        index = _STAGES.index(stage)
        start = PROGRESS_AT[_STAGES[index - 1]] if index > 0 else 0.0
        self._update(status="running", stage=stage, progress=round(start, 4))

    def _fraction(self, stage: str, fraction: float) -> None:
        index = _STAGES.index(stage)
        start = PROGRESS_AT[_STAGES[index - 1]] if index > 0 else 0.0
        value = round(start + (PROGRESS_AT[stage] - start) * max(0.0, min(1.0, fraction)), 4)
        # Frame-level callbacks are frequent; write (and publish) only visible changes.
        if abs(value - self._last_progress) < PROGRESS_STEP and fraction < 1.0:
            return
        self._last_progress = value
        self._update(progress=value)

    # ----- the job -----

    def run(self) -> None:
        with self.ctx.session_factory() as session:
            video = repo.get_video(session, self.video_id)
            if video is None:
                return
            batch = repo.get_batch(session, video.batch_id)
            style: dict[str, Any] = json.loads(batch.style_json) if batch else {}
            settings = load_settings(session)
            url = video.url
        memory = self.ctx.memory()
        ffmpeg = self.ctx.config.ffmpeg_path
        video_dir = self.ctx.video_dir(self.video_id)

        self._stage("inspect")
        adapter, canonical = resolve(url, self.ctx.adapters)
        meta = adapter.inspect(canonical)
        self._update(
            platform=meta.platform, source_id=meta.source_id, title=meta.title,
            channel=meta.channel, duration_s=meta.duration_s, has_chat=int(meta.has_chat),
            thumbnail_path=meta.thumbnail_url,
        )  # fmt: skip

        self._stage("download")
        with stage_timer("download", video=self.video_id):
            info = run_download(
                adapter, canonical, video_dir, ffmpeg_path=ffmpeg,
                max_height=max_download_height(memory), cancel=self.cancel,
                on_progress=lambda f: self._fraction("download", f),
            )  # fmt: skip
        duration = info.probed_duration_s
        self._update(source_path=self.ctx.relative(video_dir / "source.mp4"))

        self._stage("chat")
        chat = (
            run_chat(adapter, canonical, video_dir, cancel=self.cancel) if meta.has_chat else None
        )
        self._update(has_chat=int(chat is not None))

        signals: dict[str, SignalFile] = {}
        self._stage("audio")
        with stage_timer("audio", video=self.video_id):
            signals["audio"] = run_audio(
                video_dir, duration, ffmpeg_path=ffmpeg, memory=memory, cancel=self.cancel,
                on_progress=lambda f: self._fraction("audio", f),
            )  # fmt: skip
        self._stage("motion")
        with stage_timer("motion", video=self.video_id):
            signals["motion"] = run_motion(
                video_dir, duration, info.width, info.height,
                ffmpeg_path=ffmpeg, memory=memory, cancel=self.cancel,
                on_progress=lambda f: self._fraction("motion", f),
            )  # fmt: skip
        if chat is not None:
            signals["chat"] = run_chat_signal(video_dir, chat.items, duration)

        self._stage("candidates")
        candidates = run_candidates(video_dir, signals, duration)

        self._stage("transcribe")
        language = str(style.get("caption_language", "en"))
        spans = plan_spans(
            [(c.start, c.end) for c in candidates.items], duration, thorough=settings.thorough_mode
        )
        transcript = self._transcribe(settings, language, spans, memory)

        self._stage("select")
        client = self._llm_client()
        count = int(style.get("clips_per_video", 5))
        extended = style.get("clip_length_mode") == "extended"
        facts = VideoFacts(meta.title, meta.channel, duration)
        selection = run_select(
            video_dir, facts, candidates.items, transcript,
            count=count, extended=extended, client=client,
        )  # fmt: skip

        self._stage("clips")
        words = all_words(transcript)
        with self.ctx.session_factory() as session:
            video_row = repo.get_video(session, self.video_id)
            if video_row is None:
                return
            if not repo.clips_of_video(session, self.video_id):
                for index, chosen in enumerate(selection.clips):
                    clip_words = [
                        Word(text=w.text, start=round(max(0.0, w.start - chosen.start), 2),
                             end=round(max(0.0, w.end - chosen.start), 2))
                        for w in words_between(words, chosen.start, chosen.end)
                    ]  # fmt: skip
                    if language == "hinglish":
                        clip_words = to_hinglish(
                            clip_words, lambda texts: transliterate_words(client, texts)
                        ).words
                    self._create_clip(session, video_row, index, chosen, clip_words, style)
            video_row.status = "done"
            video_row.stage = "clips"
            video_row.progress = 1.0
            session.commit()
            batch_id = video_row.batch_id
        self.ctx.publish(batch_id, "video", id=self.video_id, status="done", stage="clips",
                         progress=1.0)  # fmt: skip

    def _create_clip(
        self,
        session: Session,
        video: VideoRow,
        index: int,
        chosen: Any,
        words: list[Word],
        style: dict[str, Any],
    ) -> None:
        clip = repo.add_clip(
            session,
            video_id=video.id, batch_id=video.batch_id, idx=index, status="queued",
            stage=None, progress=0.0, start_s=chosen.start, end_s=chosen.end,
            title=chosen.title, youtube_title=chosen.youtube_title,
            youtube_description=chosen.youtube_description,
            hashtags_json=json.dumps(chosen.hashtags), linkedin_post=chosen.linkedin_post,
            reason=chosen.reason, score=chosen.score,
            caption_style=style.get("caption_style", "pop"),
            caption_language=style.get("caption_language", "en"),
            crop_mode=style.get("crop_mode", "auto"), blur=int(style.get("blur", 30)),
            words_json=json.dumps([w.model_dump() for w in words]), edited=0,
        )  # fmt: skip
        repo.add_job(session, "clip_render", clip.id, repo.PRIORITY_BACKGROUND)

    def _transcribe(
        self, settings: Settings, language: str, spans: list[Span], memory: MemoryInfo
    ) -> TranscriptFile:
        """Transcribe with the engine `choose_engine` picks (D-028): Parakeet for English,
        Whisper for Hinglish. A Parakeet failure (package or model missing, download or
        runtime error) falls back to Whisper for this video, logged and written to the
        video's note; cancel is never a failure. The engine that actually runs (and for
        Whisper, GPU or CPU) is saved on the video for the Transcribe step (D-030)."""
        video_dir = self.ctx.video_dir(self.video_id)
        wav = video_dir / AUDIO_FILE_NAME
        caption: CaptionLanguage = "hinglish" if language == "hinglish" else "en"
        choice = choose_engine(settings.transcribe_engine, caption, memory)
        reason = choice.reason
        if choice.engine == "parakeet":
            if self.ctx.parakeet_factory is None:
                reason = PARAKEET_MISSING_NOTE
            else:
                factory = self.ctx.parakeet_factory(self.cancel, self._model_download)
                # Shown while Parakeet runs (a first-use download replaces the note with its
                # progress), so the engine is visible before the step ends.
                self._update(note=PARAKEET_NOTE, transcribe_engine=PARAKEET_LABEL)
                try:
                    with stage_timer("transcribe", video=self.video_id, engine="parakeet"):
                        transcript = run_transcribe(
                            video_dir, wav, spans, caption_language=caption,
                            runner_factory=factory, cancel=self.cancel,
                            on_progress=lambda f: self._fraction("transcribe", f),
                            engine="parakeet",
                        )  # fmt: skip
                    self._update(note=PARAKEET_NOTE)
                    return transcript
                except AppError as exc:
                    if exc.code not in PARAKEET_FALLBACK_CODES:
                        raise
                    logger.warning(
                        "parakeet failed video=%s code=%s: %s; using whisper",
                        self.video_id, exc.code, exc.message,
                    )  # fmt: skip
                    reason = f"Parakeet failed ({exc.message}), so Whisper was used."
        return self._transcribe_whisper(settings, caption, spans, reason)

    def _transcribe_whisper(
        self, settings: Settings, caption: CaptionLanguage, spans: list[Span], reason: str | None
    ) -> TranscriptFile:
        video_dir = self.ctx.video_dir(self.video_id)
        model = self._whisper_model(settings)
        base = self.ctx.whisper_factory(model, self.ctx.gpu_vram() is not None)
        runners: list[SpeechRunner] = []

        def factory() -> SpeechRunner:
            runner = base()
            runners.append(runner)
            self._show_whisper(model, runner)
            return runner

        def progress(fraction: float) -> None:
            # The runner may switch from GPU to CPU at its first inference (AA-030).
            if runners:
                self._show_whisper(model, runners[0])
            self._fraction("transcribe", fraction)

        # Shown while Whisper runs, so a fallback is visible right away, not only at the end.
        self._update(note=_whisper_note(model, reason), transcribe_engine=f"Whisper {model}")
        with stage_timer("transcribe", video=self.video_id, engine="whisper", model=model):
            transcript = run_transcribe(
                video_dir, video_dir / AUDIO_FILE_NAME, spans, caption_language=caption,
                runner_factory=factory, cancel=self.cancel, on_progress=progress,
                engine="whisper",
            )  # fmt: skip
        return transcript

    def _show_whisper(self, model: str, runner: SpeechRunner) -> None:
        """Name the device Whisper really runs on, e.g. "Whisper medium · GPU"."""
        device = getattr(runner, "device", None)
        label = f"Whisper {model}"
        if isinstance(device, str):
            label += f" · {WHISPER_DEVICE_NAMES.get(device, device)}"
        if label != self._engine_label:
            self._engine_label = label
            self._update(transcribe_engine=label)

    def _model_download(self, fraction: float) -> None:
        percent = int(fraction * 100)
        if percent != self._download_percent:
            self._download_percent = percent
            self._update(note=f"{MODEL_DOWNLOAD_NOTE} {percent}%")

    def _whisper_model(self, settings: Settings) -> str:
        if settings.whisper_model != "auto":
            return settings.whisper_model
        return pick_whisper_model(self.ctx.memory(), self.ctx.gpu_vram())

    def _llm_client(self) -> LlmClient:
        session = self.ctx.llm.get()
        if session is None:
            raise AppError(
                ErrorCode.LLM_NOT_CONFIGURED,
                "The AI model is not configured (the backend restarted). Open Configure API, "
                "then retry this video.",
            )
        return LlmClient(session, completion=self.ctx.completion)
