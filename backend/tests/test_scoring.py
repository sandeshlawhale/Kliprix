"""Scoring math (docs/04 stage 6): normalization, combination, and peak picking."""

import numpy as np
import pytest

from app.config import CHAT_FALLBACK_BEFORE_S, CHAT_LEAD_S, CHAT_TAIL_S
from app.pipeline.candidates import chat_window, find_candidates
from app.pipeline.scoring import (
    agreement,
    combine,
    edge_limits,
    fit_length,
    moving_average,
    normalize,
    pick_peaks,
    robust_z,
)
from app.pipeline.signals import (
    SignalFile,
    chat_series,
    estimate_lag,
    message_groups,
    message_weight,
    reaction_counts,
)
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


def test_message_groups() -> None:
    assert message_groups("LMAOOO") == ["laugh"]
    assert message_groups("W ACE") == ["hype"]
    assert message_groups("bro what??") == ["shock"]
    assert message_groups("L noob") == ["fail"]
    # Common words are not reactions: "bhai", "bro", "low", "ward", "glass".
    assert message_groups("bhai hello bro") == []
    assert message_groups("low ward glass") == []


def test_chat_series_uses_the_given_lag() -> None:
    messages = [ChatMessage(t=30.0, text="hello")]
    assert chat_series(messages, 40.0, lag=22)[8] == 1.0


def _bursts(length: int, at: list[int]) -> np.ndarray:
    values = np.zeros(length)
    for second in at:
        values[second : second + 3] = 1.0
    return values


def test_lag_is_measured_when_chat_clearly_follows_the_sound() -> None:
    rng = np.random.default_rng(3)
    at = sorted(rng.choice(np.arange(400, 3200), size=40, replace=False).tolist())
    events = _bursts(3600, at)
    reactions = _bursts(3600, [second + 17 for second in at]) * 5
    assert estimate_lag(reactions, events) == 17


def test_lag_is_none_when_chat_does_not_follow_the_sound() -> None:
    rng = np.random.default_rng(4)
    events = _bursts(3600, rng.choice(np.arange(400, 3200), size=40).tolist())
    reactions = rng.random(3600)
    assert estimate_lag(reactions, events) is None
    assert estimate_lag(np.zeros(100), np.zeros(100)) is None


def test_reaction_counts_are_not_shifted() -> None:
    messages = [ChatMessage(t=5.5, text="lol"), ChatMessage(t=5.9, text="hi")]
    counts = reaction_counts(messages, 10)
    assert counts[5] == 1.0
    assert counts.sum() == 1.0


def test_chat_led_window_looks_back_to_the_cause() -> None:
    events = np.zeros(600)
    events[280:284] = 0.9  # a gunfight 20 s before chat reacts
    reaction, cause, start, end = chat_window(290, 10, events)
    assert reaction == 300.0
    assert cause == 280.0
    assert start == 280.0 - CHAT_LEAD_S
    assert end == 300.0 + CHAT_TAIL_S
    # No clear cause: a fixed look-back from the peak.
    _, none, start, _ = chat_window(290, 10, np.zeros(600))
    assert none is None
    assert start == 290.0 - CHAT_FALLBACK_BEFORE_S


def test_find_candidates_marks_chat_led_peaks() -> None:
    duration = 3600
    chat = [0.0] * duration
    sound = [0.0] * duration
    _spike(chat, 1000, 10.0)  # chat leads; the sound event 20 s before the reaction
    _spike(sound, 985, 3.0)
    _spike(sound, 2000, 10.0)  # sound leads
    signals = {"chat": SignalFile(values=chat), "sound": SignalFile(values=sound)}
    items = find_candidates(signals, float(duration))
    led = next(item for item in items if abs(item.peak_t - 1000) <= 10)
    assert led.reaction_t == led.peak_t + 10
    # The onset of the sound burst (981-989, widened by smoothing).
    assert led.cause_t is not None and 975 <= led.cause_t <= 989
    assert led.start < led.cause_t < led.end
    sound_led = next(item for item in items if item.peak_t == 2000.0)
    assert sound_led.reaction_t is None


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
    everything = find_candidates(signals, duration)
    items = [item for item in everything if item.source == "signals"]
    peaks = sorted(item.peak_t for item in items)
    assert peaks[:3] == [100.0, 400.0, 590.0]
    by_peak = {item.peak_t: item for item in items}
    assert (by_peak[100.0].start, by_peak[100.0].end) == (75.0, 135.0)
    # The outro (1.5% of 600 s = the last 9 s) is never part of a window.
    assert by_peak[590.0].end == 591.0
    assert all(0 <= item.score <= 1 for item in items)
    assert by_peak[400.0].signals.chat is None
    # Coverage windows (D-035) fill the quiet stretches, never near a peak or each other.
    assert any(item.source == "coverage" for item in everything)
    for a in everything:
        for b in everything:
            if a is not b:
                assert abs(a.peak_t - b.peak_t) >= 60


def test_agreement_factor() -> None:
    chat = np.array([1.0, 1.0, 1.0, 0.0])
    audio = np.array([0.0, 0.15, 0.6, 0.0])
    motion = np.array([0.0, 0.0, 0.0, 0.0])
    factor = agreement({"chat": chat, "audio": audio, "motion": motion})
    # Alone -> floor 0.5; second signal at half the level -> 0.75; at/above the level -> 1.
    assert factor.tolist() == pytest.approx([0.5, 0.75, 1.0, 0.5])
    assert agreement({"audio": audio}).tolist() == [1.0] * 4


def test_edge_limits_scale_with_duration() -> None:
    args = (0.03, 180.0, 0.015, 120.0)
    assert edge_limits(4 * 3600.0, *args) == (180.0, 4 * 3600.0 - 120.0)
    assert edge_limits(600.0, *args) == pytest.approx((18.0, 591.0))


def _spike(values: list[float], at: int, height: float) -> None:
    for offset in range(-4, 5):
        values[at + offset] = height - 2 * abs(offset) * height / 10


def test_find_candidates_skips_the_intro_and_prefers_agreeing_signals() -> None:
    duration = 3600.0
    chat = [0.0] * 3600
    audio = [0.0] * 3600
    _spike(chat, 60, 10.0)  # chat flood in the lead-in (first 108 s): never picked
    _spike(chat, 1000, 10.0)  # chat alone
    _spike(chat, 2000, 8.0)  # weaker chat, but the audio agrees
    _spike(audio, 2000, 8.0)
    items = find_candidates(
        {"chat": SignalFile(values=chat), "audio": SignalFile(values=audio)}, duration
    )
    peaks = [item.peak_t for item in items]
    assert all(peak >= 108 for peak in peaks)
    assert all(item.start >= 108 for item in items)
    assert peaks[:2] == [2000.0, 1000.0]


def test_find_candidates_excludes_clips_already_made() -> None:
    chat = [0.0] * 3600
    audio = [0.0] * 3600
    for at in (1000, 2000):
        _spike(chat, at, 10.0)
        _spike(audio, at, 10.0)
    signals = {"chat": SignalFile(values=chat), "audio": SignalFile(values=audio)}
    items = find_candidates(signals, 3600.0, exclude=[(950.0, 1100.0)])
    peaks = [item.peak_t for item in items]
    assert peaks[0] == 2000.0
    assert all(not 950 <= peak <= 1100 for peak in peaks)


def test_find_candidates_at_most_twelve() -> None:
    rng = np.random.default_rng(1)
    values = rng.random(3600).tolist()
    everything = find_candidates({"audio": SignalFile(values=values)}, 3600.0)
    items = [item for item in everything if item.source == "signals"]
    assert len(items) == 12
    # A long video gets only a few coverage windows (each costs transcription time).
    assert len(everything) - len(items) <= 4
    scores = [item.score for item in items]
    assert scores == sorted(scores, reverse=True)


def test_chat_is_ignored_near_the_stream_start() -> None:
    """D-035: a chat flood right after the intro (greetings) does not make a candidate; the
    same flood mid-stream does."""
    duration = 3600
    rng = np.random.default_rng(7)
    audio = (1.0 + rng.random(duration)).tolist()
    chat = rng.random(duration).tolist()
    for peak in (400, 1800):  # 400 s is inside the first 15%; 1800 s is mid-stream
        for offset in range(-4, 5):
            chat[peak + offset] = 20.0 - 2 * abs(offset)
            audio[peak + offset] = 2.0 + 0.1 * (4 - abs(offset))
    signals = {"chat": SignalFile(values=chat), "audio": SignalFile(values=audio)}
    items = [i for i in find_candidates(signals, float(duration)) if i.source == "signals"]
    by_peak = {item.peak_t: item for item in items}
    assert by_peak[1800.0].score > by_peak[400.0].score
    assert by_peak[1800.0].signals.chat is not None
    assert by_peak[400.0].signals.chat is None
