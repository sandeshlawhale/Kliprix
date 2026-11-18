"""Scoring math (docs/04 stage 6): normalization, combination, and peak picking."""

import numpy as np
import pytest

from app.pipeline.candidates import find_candidates
from app.pipeline.scoring import (
    combine,
    fit_length,
    moving_average,
    normalize,
    pick_peaks,
    robust_z,
)
from app.pipeline.signals import SignalFile, chat_series, message_weight
from app.sources.base import ChatMessage


def test_robust_z_uses_median_and_mad() -> None:
    values = np.array([1.0, 2.0, 3.0, 4.0, 100.0])
    z = robust_z(values)
    # median 3, MAD 1 -> scale 1.4826
    assert z[2] == 0.0
    assert z[4] == pytest.approx(97 / 1.4826)


def test_robust_z_falls_back_when_mad_is_zero() -> None:
    values = np.array([0.0] * 9 + [10.0])
    z = robust_z(values)
    assert z[-1] > 0
    assert np.all(z[:-1] == 0)


def test_robust_z_of_constant_signal_is_zero() -> None:
    assert np.all(robust_z(np.full(20, 5.0)) == 0)


def test_normalize_clips_to_unit_range() -> None:
    values = np.array([0.0, 1.0, 2.0, 1.0, 0.0, 1.0, 1000.0, -1000.0])
    result = normalize(values)
    assert result.min() == 0.0
    assert result.max() == 1.0
    assert np.all((result >= 0) & (result <= 1))


def test_moving_average_keeps_length_and_edges() -> None:
    values = np.array([0.0, 0.0, 5.0, 0.0, 0.0])
    smooth = moving_average(values, 5)
    assert len(smooth) == 5
    assert smooth[2] == pytest.approx(1.0)
    # The first bin averages only the 3 values that exist.
    assert smooth[0] == pytest.approx(5.0 / 3)


def test_combine_renormalizes_missing_signals() -> None:
    audio = np.array([1.0, 0.0])
    motion = np.array([0.0, 1.0])
    weights = {"chat": 0.4, "audio": 0.35, "motion": 0.25}
    result = combine({"audio": audio, "motion": motion}, weights)
    assert result[0] == pytest.approx(0.35 / 0.6)
    assert result[1] == pytest.approx(0.25 / 0.6)


def test_combine_rejects_no_weighted_signal() -> None:
    with pytest.raises(ValueError, match="positive weight"):
        combine({"other": np.ones(3)}, {"chat": 1.0})


def test_pick_peaks_respects_min_distance_and_count() -> None:
    score = np.zeros(400)
    score[[50, 70, 200, 300, 330]] = [0.9, 1.0, 0.5, 0.8, 0.7]
    assert pick_peaks(score, 12, 60) == [70, 300, 200]
    assert pick_peaks(score, 2, 60) == [70, 300]


def test_pick_peaks_breaks_plateau_ties_with_tiebreak() -> None:
    score = np.zeros(100)
    score[40:50] = 1.0
    tiebreak = np.zeros(100)
    tiebreak[40:50] = [1, 2, 3, 4, 9, 4, 3, 2, 1, 0]
    assert pick_peaks(score, 1, 60) == [40]
    assert pick_peaks(score, 1, 60, tiebreak) == [44]


def test_pick_peaks_ignores_zero_scores() -> None:
    assert pick_peaks(np.zeros(100), 12, 60) == []


def test_fit_length_pads_and_cuts() -> None:
    assert fit_length(np.array([1.0, 2.0]), 4).tolist() == [1.0, 2.0, 2.0, 2.0]
    assert fit_length(np.array([1.0, 2.0, 3.0]), 2).tolist() == [1.0, 2.0]
    assert fit_length(np.array([]), 2).tolist() == [0.0, 0.0]


def test_chat_series_counts_hype_and_shifts_earlier() -> None:
    messages = [
        ChatMessage(t=15.2, text="hello"),
        ChatMessage(t=15.9, text="LMAO \U0001f602"),
        ChatMessage(t=3.0, text="gg"),
        ChatMessage(t=99.0, text="after the end"),
    ]
    values = chat_series(messages, 20.0)
    assert len(values) == 20
    assert values[5] == pytest.approx(1.0 + 2.0)
    # Before the start after the lag shift -> second 0.
    assert values[0] == pytest.approx(2.0)
    assert sum(values) == pytest.approx(5.0)


def test_message_weight_tokens() -> None:
    assert message_weight("clip it now") == 2.0
    assert message_weight("nice") == 1.0


def test_find_candidates_windows_and_bounds() -> None:
    duration = 600.0
    audio = [0.0] * 600
    motion = [0.0] * 600
    for peak in (100, 400, 590):
        # Triangular peaks so the 5 s smoothing keeps the maximum on the peak second.
        for offset in range(-4, 5):
            if 0 <= peak + offset < 600:
                audio[peak + offset] = motion[peak + offset] = 10.0 - 2 * abs(offset)
    signals = {"audio": SignalFile(values=audio), "motion": SignalFile(values=motion)}
    items = find_candidates(signals, duration)
    peaks = sorted(item.peak_t for item in items)
    assert peaks[:3] == [100.0, 400.0, 590.0]
    by_peak = {item.peak_t: item for item in items}
    assert (by_peak[100.0].start, by_peak[100.0].end) == (75.0, 135.0)
    assert by_peak[590.0].end == duration
    assert all(0 <= item.score <= 1 for item in items)
    assert by_peak[400.0].signals.chat is None
    for a in items:
        for b in items:
            if a is not b:
                assert abs(a.peak_t - b.peak_t) >= 60


def test_find_candidates_at_most_twelve() -> None:
    rng = np.random.default_rng(1)
    values = rng.random(3600).tolist()
    items = find_candidates({"audio": SignalFile(values=values)}, 3600.0)
    assert len(items) == 12
    scores = [item.score for item in items]
    assert scores == sorted(scores, reverse=True)
