"""Stage 6, candidates: `candidates.json` with the top windows by combined signal score
(docs/04 "Candidates", docs/08)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import (
    CANDIDATE_AFTER_S,
    CANDIDATE_BEFORE_S,
    CANDIDATE_COUNT,
    CANDIDATE_EDGE_LEAD_MAX_S,
    CANDIDATE_EDGE_LEAD_SHARE,
    CANDIDATE_EDGE_TAIL_MAX_S,
    CANDIDATE_EDGE_TAIL_SHARE,
    CANDIDATE_MIN_DISTANCE_S,
    CHAT_EDGE_LEAD_MAX_S,
    CHAT_EDGE_LEAD_SHARE,
    CHAT_EDGE_TAIL_MAX_S,
    CHAT_EDGE_TAIL_SHARE,
    COVERAGE_LONG_COUNT,
    COVERAGE_SHORT_MAX,
    FULL_TRANSCRIBE_MAX_S,
    SIGNAL_WEIGHTS,
    SMOOTH_WINDOW_S,
)
from app.core.files import write_json_atomic
from app.pipeline.scoring import (
    FloatArray,
    agreement,
    combine,
    edge_limits,
    moving_average,
    normalize,
    pick_peaks,
    positive_z,
)
from app.pipeline.screen import ScreenFile, dull_series, screen_factor, window_shares
from app.pipeline.signals import SignalFile

CANDIDATES_FILE_NAME = "candidates.json"
SCORE_DECIMALS = 3


class SignalScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat: float | None = None
    audio: float | None = None
    motion: float | None = None
    # Set at selection from the transcript (D-033); never in candidates.json.
    speech: float | None = None


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    peak_t: float = Field(ge=0)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    score: float = Field(ge=0, le=1)
    signals: SignalScores
    # "signals": a peak of the combined score; "coverage": an evenly spread window that lets
    # the LLM judge content the signals missed (D-035).
    source: Literal["signals", "coverage"] = "signals"
    # Mean share of each screen type in the window (D-040), e.g. {"gameplay": 0.8, ...}.
    screen: dict[str, float] | None = None


class CandidatesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    items: list[Candidate]


def find_candidates(
    signals: Mapping[str, SignalFile],
    duration_s: float,
    weights: Mapping[str, float] = SIGNAL_WEIGHTS,
    exclude: Sequence[tuple[float, float]] = (),
    screen: ScreenFile | None = None,
) -> list[Candidate]:
    """Top `CANDIDATE_COUNT` signal windows, best first, then a few coverage windows spread
    over the rest of the video (D-035) so the LLM can judge content the signals missed.

    Each signal is smoothed, normalized to 0-1, and combined with `weights` (missing signals
    dropped and the rest renormalized), then multiplied by the agreement factor (D-033).
    Chat is left out near the stream's start and end (D-035).
    Peaks are at least `CANDIDATE_MIN_DISTANCE_S` apart and never in the lead-in or outro;
    each window is peak - 25 s to peak + 35 s, clamped to the video without those edges.
    No peak lies inside an `exclude` range (clips already made, D-034).
    With `screen` (D-040), seconds showing menus, lobbies, loading or black screens lose most
    of their score and get no coverage window.
    """
    length = min(len(signal.values) for signal in signals.values())
    smoothed = {
        name: moving_average(np.asarray(signal.values[:length]), SMOOTH_WINDOW_S)
        for name, signal in signals.items()
    }
    normalized: dict[str, FloatArray] = {name: normalize(v) for name, v in smoothed.items()}
    seconds = np.arange(length)
    score, tiebreak, chat_on = _scores(normalized, smoothed, weights, seconds, duration_s)
    dull = dull_series(screen, length) if screen is not None else np.zeros(length)
    factor = screen_factor(dull)
    score = score * factor
    tiebreak = tiebreak * factor
    first, last = edge_limits(
        duration_s, CANDIDATE_EDGE_LEAD_SHARE, CANDIDATE_EDGE_LEAD_MAX_S,
        CANDIDATE_EDGE_TAIL_SHARE, CANDIDATE_EDGE_TAIL_MAX_S,
    )  # fmt: skip
    usable = (seconds >= first) & (seconds < last)
    for low, high in exclude:
        usable &= (seconds < low) | (seconds > high)
    score = np.where(usable, score, 0.0)
    # Coverage windows only where the screen is not a menu or a waiting screen.
    covered = usable & (factor >= 1.0)

    def make(peak: int, source: Literal["signals", "coverage"]) -> Candidate:
        per_signal = {
            name: round(float(values[peak]), SCORE_DECIMALS)
            for name, values in normalized.items()
            # Chat near the stream's edges is greetings and goodbyes: not shown (D-035).
            if name != "chat" or chat_on[peak]
        }
        start = max(first, float(peak - CANDIDATE_BEFORE_S))
        end = min(last, float(peak + CANDIDATE_AFTER_S))
        return Candidate(
            peak_t=float(peak),
            start=start,
            end=end,
            score=round(float(score[peak]), SCORE_DECIMALS),
            signals=SignalScores(**per_signal),
            source=source,
            screen=window_shares(screen, start, end) or None if screen is not None else None,
        )

    peaks = pick_peaks(score, CANDIDATE_COUNT, CANDIDATE_MIN_DISTANCE_S, tiebreak)
    items = [make(peak, "signals") for peak in peaks]
    wanted = COVERAGE_SHORT_MAX if duration_s <= FULL_TRANSCRIBE_MAX_S else COVERAGE_LONG_COUNT
    for spot in coverage_spots(covered, peaks, wanted, CANDIDATE_MIN_DISTANCE_S):
        items.append(make(spot, "coverage"))
    return items


def _scores(
    normalized: Mapping[str, FloatArray],
    smoothed: Mapping[str, FloatArray],
    weights: Mapping[str, float],
    seconds: npt.NDArray[np.int_],
    duration_s: float,
) -> tuple[FloatArray, FloatArray, npt.NDArray[np.bool_]]:
    """Combined score and tiebreak per second, times the agreement factor (D-033). Near the
    stream's edges chat is left out and the other signals carry the score (D-035). Also
    returns where chat counts."""

    def scored(names: Sequence[str]) -> tuple[FloatArray, FloatArray]:
        part = {name: normalized[name] for name in names}
        agree = agreement(part)
        raw = {name: positive_z(smoothed[name]) for name in names}
        return combine(part, weights) * agree, combine(raw, weights) * agree

    names = list(normalized)
    score, tiebreak = scored(names)
    chat_from, chat_to = edge_limits(
        duration_s, CHAT_EDGE_LEAD_SHARE, CHAT_EDGE_LEAD_MAX_S,
        CHAT_EDGE_TAIL_SHARE, CHAT_EDGE_TAIL_MAX_S,
    )  # fmt: skip
    chat_on = (seconds >= chat_from) & (seconds < chat_to)
    others = [name for name in names if name != "chat"]
    if "chat" in normalized and any(weights.get(name, 0.0) > 0 for name in others):
        edge_score, edge_tiebreak = scored(others)
        score = np.where(chat_on, score, edge_score)
        tiebreak = np.where(chat_on, tiebreak, edge_tiebreak)
    else:
        chat_on = np.ones(len(seconds), dtype=bool)
    return score, tiebreak, chat_on


def coverage_spots(
    usable: npt.NDArray[np.bool_], peaks: Sequence[int], count: int, min_distance: int
) -> list[int]:
    """Up to `count` seconds spread evenly over the usable part of the video, each at least
    `min_distance` from the signal peaks and from each other (D-035)."""
    allowed = np.flatnonzero(usable)
    if count <= 0 or len(allowed) == 0:
        return []
    chosen: list[int] = []
    taken = list(peaks)
    # Twice as many evenly spaced tries as wanted; the ones too close to a peak are skipped.
    for spot in np.linspace(allowed[0], allowed[-1], num=count * 2 + 2)[1:-1]:
        position = int(allowed[np.abs(allowed - spot).argmin()])
        if all(abs(position - other) >= min_distance for other in taken):
            chosen.append(position)
            taken.append(position)
    if len(chosen) <= count:
        return chosen
    # Keep an even spread when more fit than wanted.
    keep = np.linspace(0, len(chosen) - 1, num=count).round().astype(int)
    return [chosen[i] for i in keep]


def load_cached(video_dir: Path) -> CandidatesFile | None:
    path = video_dir / CANDIDATES_FILE_NAME
    if not path.is_file():
        return None
    try:
        return CandidatesFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def run_candidates(
    video_dir: Path,
    signals: Mapping[str, SignalFile],
    duration_s: float,
    screen: ScreenFile | None = None,
) -> CandidatesFile:
    """Write `candidates.json` (skipped when a valid copy exists)."""
    cached = load_cached(video_dir)
    if cached is not None:
        return cached
    result = CandidatesFile(items=find_candidates(signals, duration_s, screen=screen))
    write_json_atomic(video_dir / CANDIDATES_FILE_NAME, result)
    return result
