"""The `video_pipeline` job: inspect → download → chat → audio + motion (in parallel,
D-033) → candidates → transcribe → select → clips (docs/04). Each stage is skipped when its
output is cached, so a restarted job resumes where it stopped. Progress comes from the work
actually done.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from functools import partial
from typing import Any

from sqlalchemy.orm import Session

from app.config import CLIP_MIN_GAP_S
from app.core.errors import AppError, ErrorCode
from app.core.hardware import MemoryInfo, pick_whisper_model
from app.core.metrics import stage_timer
from app.core.proc import CANCEL_POLL_S, CommandCancelledError
from app.core.settings_schema import Settings
from app.db.models import VideoRow
from app.db.repositories import batches as repo
from app.db.repositories.settings import load_settings
from app.jobs.context import JobContext
from app.llm.client import LlmClient
from app.llm.transliterate import transliterate_words
from app.pipeline.audio import AUDIO_FILE_NAME, ensure_audio_wav, run_audio
from app.pipeline.candidates import find_candidates, run_candidates
from app.pipeline.chat import run_chat
from app.pipeline.download import load_cached as load_download
from app.pipeline.download import max_download_height, run_download
from app.pipeline.hinglish import to_hinglish
from app.pipeline.motion import run_motion
from app.pipeline.select import (
    SelectionClip,
    VideoFacts,
    all_words,
    run_select,
    select_more,
    words_between,
)
from app.pipeline.signals import SignalFile, load_signal, run_chat_signal
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

# More clips (D-034): the video's note ends with the last round's result after this prefix.
MORE_NOTE_PREFIX = "More clips:"
SOURCE_GONE_MESSAGE = (
    "The downloaded video was deleted (storage cleanup), so more clips need a new batch."
)


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
        self._fraction_between(_STAGES[index - 1] if index > 0 else None, stage, fraction)

    def _fraction_between(self, after: str | None, until: str, fraction: float) -> None:
        """Progress `fraction` of the way from the end of stage `after` (None: 0) to the end
        of stage `until`."""
        start = PROGRESS_AT[after] if after is not None else 0.0
        value = round(start + (PROGRESS_AT[until] - start) * max(0.0, min(1.0, fraction)), 4)
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

        self._stage("audio")
        signals = self._audio_and_motion(duration, info.width, info.height, memory)
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
        self._finish_with_clips(selection.clips, all_words(transcript), style, client, None)

    def run_more(self, count: int) -> None:
        """Up to `count` more clips for a video whose download is still on disk (D-034).

        Download, chat and signals are reused; candidates are found again with the clips
        already made blocked out (plus the minimum gap); only the parts of the new
        candidates' spans that were never transcribed are transcribed; the LLM is told which
        moments already have clips. New clips get the next indexes and are rendered like the
        first ones. Finding nothing new is not a failure: the video's note says so.

        Raises:
            AppError: VALIDATION_ERROR when the download is gone; LLM and transcribe errors.
        """
        with self.ctx.session_factory() as session:
            video = repo.get_video(session, self.video_id)
            if video is None:
                return
            batch = repo.get_batch(session, video.batch_id)
            style: dict[str, Any] = json.loads(batch.style_json) if batch else {}
            settings = load_settings(session)
            taken = [(clip.start_s, clip.end_s) for clip in repo.clips_of_video(session, video.id)]
            title, channel = video.title or "", video.channel
            has_chat = bool(video.has_chat)
        memory = self.ctx.memory()
        video_dir = self.ctx.video_dir(self.video_id)
        info = load_download(video_dir, self.ctx.config.ffmpeg_path)
        if info is None:
            raise AppError(ErrorCode.VALIDATION_ERROR, SOURCE_GONE_MESSAGE)
        duration = info.probed_duration_s

        self._stage("candidates")
        # Cached signals load instantly; missing ones are computed again.
        signals = self._audio_and_motion(duration, info.width, info.height, memory)
        chat = load_signal(video_dir, "chat", duration) if has_chat else None
        if chat is not None:
            signals["chat"] = chat
        exclude = [(start - CLIP_MIN_GAP_S, end + CLIP_MIN_GAP_S) for start, end in taken]
        candidates = find_candidates(signals, duration, exclude=exclude)
        logger.info("more clips video=%s taken=%s candidates=%s", self.video_id, len(taken),
                    len(candidates))  # fmt: skip

        chosen: list[SelectionClip] = []
        words: list[Word] = []
        client: LlmClient | None = None
        if candidates:
            self._stage("transcribe")
            language = str(style.get("caption_language", "en"))
            # The WAV may have been removed by a cleanup while the video stayed; rebuild it.
            ensure_audio_wav(video_dir, duration, ffmpeg_path=self.ctx.config.ffmpeg_path,
                             memory=memory, cancel=self.cancel)  # fmt: skip
            spans = plan_spans(
                [(c.start, c.end) for c in candidates], duration, thorough=settings.thorough_mode
            )
            transcript = self._transcribe(settings, language, spans, memory, extend=True)
            self._stage("select")
            client = self._llm_client()
            chosen = select_more(
                VideoFacts(title, channel, duration), candidates, transcript, taken,
                count=count, extended=style.get("clip_length_mode") == "extended",
                client=client,
            )  # fmt: skip
            words = all_words(transcript)
        self._stage("clips")
        found = (
            f"{len(chosen)} more clip{'s' if len(chosen) != 1 else ''} added."
            if chosen
            else "No new moments were found that do not overlap the clips you have."
        )
        self._finish_with_clips(chosen, words, style, client, f"{MORE_NOTE_PREFIX} {found}")

    def _finish_with_clips(
        self,
        chosen: Sequence[SelectionClip],
        words: Sequence[Word],
        style: dict[str, Any],
        client: LlmClient | None,
        more_note: str | None,
    ) -> None:
        """Create clips (after the video's existing ones) with their caption words, queue
        their renders, and mark the video done. The first run creates clips only once, so a
        resumed job never doubles them. `more_note` (D-034) replaces the previous one."""
        language = str(style.get("caption_language", "en"))
        with self.ctx.session_factory() as session:
            video_row = repo.get_video(session, self.video_id)
            if video_row is None:
                return
            existing = repo.clips_of_video(session, self.video_id)
            if more_note is not None or not existing:
                first = max((clip.idx for clip in existing), default=-1) + 1
                for offset, clip in enumerate(chosen):
                    clip_words = [
                        Word(text=w.text, start=round(max(0.0, w.start - clip.start), 2),
                             end=round(max(0.0, w.end - clip.start), 2))
                        for w in words_between(words, clip.start, clip.end)
                    ]  # fmt: skip
                    if language == "hinglish" and client is not None:
                        clip_words = to_hinglish(
                            clip_words, partial(transliterate_words, client)
                        ).words
                    self._create_clip(session, video_row, first + offset, clip, clip_words, style)
            if more_note is not None:
                base = (video_row.note or "").split(MORE_NOTE_PREFIX)[0].strip()
                video_row.note = f"{base} {more_note}".strip()
            video_row.status = "done"
            video_row.stage = "clips"
            video_row.progress = 1.0
            video_row.error_code = None
            video_row.error_message = None
            session.commit()
            batch_id = video_row.batch_id
        self.ctx.publish(batch_id, "video", id=self.video_id, status="done", stage="clips",
                         progress=1.0)  # fmt: skip

    def _audio_and_motion(
        self, duration: float, width: int, height: int, memory: MemoryInfo
    ) -> dict[str, SignalFile]:
        """Audio and motion at the same time (D-033): both only read `source.mp4`, so the
        step takes as long as the slower one. The bar spans both stages and moves with the
        mean of their fractions. A failure in one stops the other and is raised; a cancel
        stops both."""
        video_dir = self.ctx.video_dir(self.video_id)
        ffmpeg = self.ctx.config.ffmpeg_path
        stop = threading.Event()
        lock = threading.Lock()
        fractions = {"audio": 0.0, "motion": 0.0}

        def report(name: str) -> Callable[[float], None]:
            def update(fraction: float) -> None:
                with lock:
                    fractions[name] = fraction
                    self._fraction_between(
                        "chat", "motion", sum(fractions.values()) / len(fractions)
                    )

            return update

        def audio() -> SignalFile:
            with stage_timer("audio", video=self.video_id):
                return run_audio(
                    video_dir, duration, ffmpeg_path=ffmpeg, memory=memory, cancel=stop,
                    on_progress=report("audio"),
                )  # fmt: skip

        def motion() -> SignalFile:
            with stage_timer("motion", video=self.video_id):
                return run_motion(
                    video_dir, duration, width, height, ffmpeg_path=ffmpeg, memory=memory,
                    cancel=stop, on_progress=report("motion"),
                )  # fmt: skip

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="signals") as pool:
            futures = {"audio": pool.submit(audio), "motion": pool.submit(motion)}
            pending: set[Future[SignalFile]] = set(futures.values())
            while pending:
                done, pending = wait(pending, timeout=CANCEL_POLL_S, return_when=FIRST_EXCEPTION)
                if self.cancel.is_set() or any(f.exception() is not None for f in done):
                    stop.set()
        errors = [e for f in futures.values() if (e := f.exception()) is not None]
        # The sibling of a failed stage stops with a cancel error; raise the real failure.
        real = [e for e in errors if not isinstance(e, CommandCancelledError)]
        if real or errors:
            raise (real or errors)[0]
        return {name: future.result() for name, future in futures.items()}

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
        self,
        settings: Settings,
        language: str,
        spans: list[Span],
        memory: MemoryInfo,
        *,
        extend: bool = False,
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
                            engine="parakeet", extend=extend,
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
        return self._transcribe_whisper(settings, caption, spans, reason, extend)

    def _transcribe_whisper(
        self,
        settings: Settings,
        caption: CaptionLanguage,
        spans: list[Span],
        reason: str | None,
        extend: bool = False,
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
                engine="whisper", extend=extend,
            )  # fmt: skip
        return transcript

    def _show_whisper(self, model: str, runner: SpeechRunner) -> None:
        """Name the model and device Whisper really runs on, e.g. "Whisper large-v3-turbo ·
        GPU" (after a GPU failure: the CPU model, D-032)."""
        device = getattr(runner, "device", None)
        label = f"Whisper {getattr(runner, 'model_name', model)}"
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
