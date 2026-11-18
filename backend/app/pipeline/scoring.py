"""Pure scoring math for candidates (docs/04 stage 6): smoothing, robust normalization,
weighted sum, and peak picking. No I/O here so it is easy to test."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import numpy.typing as npt

from app.config import MAD_TO_SIGMA, ROBUST_Z_MAX

FloatArray = npt.NDArray[np.float64]


def fit_length(values: FloatArray, length: int) -> FloatArray:
    """Pad with the last value (or zeros) or cut `values` to exactly `length` items."""
    if len(values) >= length:
        return values[:length].astype(np.float64)
    fill = float(values[-1]) if len(values) else 0.0
    return np.concatenate([values.astype(np.float64), np.full(length - len(values), fill)])


def moving_average(values: FloatArray, window: int) -> FloatArray:
    """Centered moving average over `window` samples; edges average what exists."""
    if window <= 1 or len(values) == 0:
        return values.astype(np.float64)
    kernel = np.ones(window)
    sums = np.convolve(values, kernel, mode="same")
    counts = np.convolve(np.ones(len(values)), kernel, mode="same")
    return np.asarray(sums / counts, dtype=np.float64)


def robust_z(values: FloatArray) -> FloatArray:
    """(x - median) / (1.4826 * MAD). Falls back to the mean absolute deviation when the MAD is
    0 (a mostly constant signal with a few spikes); a fully constant signal gives zeros."""
    if len(values) == 0:
        return values.astype(np.float64)
    median = float(np.median(values))
    deviation = np.abs(values - median)
    scale = MAD_TO_SIGMA * float(np.median(deviation))
    if scale <= 0:
        scale = float(np.mean(deviation))
    if scale <= 0:
        return np.zeros(len(values))
    return np.asarray((values - median) / scale, dtype=np.float64)


def positive_z(values: FloatArray) -> FloatArray:
    """Robust z-score with negatives set to 0 but no upper clip (used to break ties)."""
    return np.maximum(robust_z(values), 0.0)


def normalize(values: FloatArray) -> FloatArray:
    """Robust z-score clipped to [0, ROBUST_Z_MAX] and scaled to 0-1 (docs/04 step 2)."""
    return np.clip(robust_z(values), 0.0, ROBUST_Z_MAX) / ROBUST_Z_MAX


def combine(signals: Mapping[str, FloatArray], weights: Mapping[str, float]) -> FloatArray:
    """Weighted sum of normalized signals; weights of missing signals are dropped and the rest
    renormalized to sum to 1 (docs/04 step 3).

    Raises:
        ValueError: no signal has a positive weight, or lengths differ.
    """
    present = {name: weights.get(name, 0.0) for name in signals if weights.get(name, 0.0) > 0}
    total = sum(present.values())
    if total <= 0:
        raise ValueError("no signal with a positive weight")
    lengths = {len(signals[name]) for name in present}
    if len(lengths) != 1:
        raise ValueError("signals have different lengths")
    result = np.zeros(lengths.pop())
    for name, weight in present.items():
        result += signals[name] * (weight / total)
    return result


def pick_peaks(
    score: FloatArray, count: int, min_distance: int, tiebreak: FloatArray | None = None
) -> list[int]:
    """Indices of the highest local maxima at least `min_distance` apart, best first.

    Greedy: take the best remaining second, then exclude everything closer than
    `min_distance`. Equal scores (a plateau where the clipped z-score saturates) are ordered
    by `tiebreak`, so the pick lands on the real maximum instead of the plateau's first
    second. Seconds with a score of 0 are never picked.
    """
    secondary = np.zeros(len(score)) if tiebreak is None else tiebreak
    # np.lexsort sorts by the last key first.
    order = np.lexsort((-secondary, -score))
    picked: list[int] = []
    for index in order:
        position = int(index)
        if score[position] <= 0 or len(picked) >= count:
            break
        if all(abs(position - other) >= min_distance for other in picked):
            picked.append(position)
    return picked
