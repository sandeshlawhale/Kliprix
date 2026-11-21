"""Stage 6, candidates: `candidates.json` with the top windows by combined signal score
(docs/04 "Candidates", docs/08)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import numpy as np
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


class CandidatesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    items: list[Candidate]


def find_candidates(
    signals: Mapping[str, SignalFile],
    duration_s: float,
    weights: Mapping[str, float] = SIGNAL_WEIGHTS,
) -> list[Candidate]:
    """Top `CANDIDATE_COUNT` windows, best first.

    Each signal is smoothed, normalized to 0-1, and combined with `weights` (missing signals
    dropped and the rest renormalized), then multiplied by the agreement factor (D-033).
    Peaks are at least `CANDIDATE_MIN_DISTANCE_S` apart and never in the lead-in or outro;
    each window is peak - 25 s to peak + 35 s, clamped to the video without those edges.
    """
    length = min(len(signal.values) for signal in signals.values())
    smoothed = {
        name: moving_average(np.asarray(signal.values[:length]), SMOOTH_WINDOW_S)
        for name, signal in signals.items()
    }
    normalized: dict[str, FloatArray] = {name: normalize(v) for name, v in smoothed.items()}
    agree = agreement(normalized)
    score = combine(normalized, weights) * agree
    tiebreak = combine({name: positive_z(v) for name, v in smoothed.items()}, weights) * agree
    first, last = edge_limits(
        duration_s, CANDIDATE_EDGE_LEAD_SHARE, CANDIDATE_EDGE_LEAD_MAX_S,
        CANDIDATE_EDGE_TAIL_SHARE, CANDIDATE_EDGE_TAIL_MAX_S,
    )  # fmt: skip
    seconds = np.arange(length)
    score = np.where((seconds >= first) & (seconds < last), score, 0.0)
    items: list[Candidate] = []
    for peak in pick_peaks(score, CANDIDATE_COUNT, CANDIDATE_MIN_DISTANCE_S, tiebreak):
        start = max(first, float(peak - CANDIDATE_BEFORE_S))
        end = min(last, float(peak + CANDIDATE_AFTER_S))
        per_signal = {
            name: round(float(values[peak]), SCORE_DECIMALS) for name, values in normalized.items()
        }
        items.append(
            Candidate(
                peak_t=float(peak),
                start=start,
                end=end,
                score=round(float(score[peak]), SCORE_DECIMALS),
                signals=SignalScores(**per_signal),
            )
        )
    return items


def load_cached(video_dir: Path) -> CandidatesFile | None:
    path = video_dir / CANDIDATES_FILE_NAME
    if not path.is_file():
        return None
    try:
        return CandidatesFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def run_candidates(
    video_dir: Path, signals: Mapping[str, SignalFile], duration_s: float
) -> CandidatesFile:
    """Write `candidates.json` (skipped when a valid copy exists)."""
    cached = load_cached(video_dir)
    if cached is not None:
        return cached
    result = CandidatesFile(items=find_candidates(signals, duration_s))
    write_json_atomic(video_dir / CANDIDATES_FILE_NAME, result)
    return result
