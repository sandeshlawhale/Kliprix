"""Face tracking math (docs/05 "Face tracking rules"), separate from detection and video I/O.

Input: per sampled frame, the detected faces (center x as 0-1 of the width, and area) and
whether a scene cut happened. Output: a smoothed crop center per sample.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.render import layout_constants as lc

CENTER = 0.5


@dataclass(frozen=True)
class Face:
    cx: float  # center x, 0-1 of the frame width
    area: float  # box area, 0-1 of the frame area


@dataclass(frozen=True)
class Sample:
    faces: list[Face]
    scene_cut: bool


def face_ratio(samples: Sequence[Sample]) -> float:
    """Share of samples with at least one face."""
    if not samples:
        return 0.0
    return sum(1 for sample in samples if sample.faces) / len(samples)


def choose_targets(samples: Sequence[Sample], sample_dt: float) -> list[float | None]:
    """The face to follow per sample: the largest face, switching to another only when it is
    at least 1.4x larger for at least 1 s (hysteresis). None where no face is seen."""
    hold_samples = max(1, round(lc.FACE_SWITCH_HOLD_S / sample_dt))
    targets: list[float | None] = []
    current: float | None = None
    challenger_count = 0
    for sample in samples:
        if not sample.faces:
            targets.append(None)
            challenger_count = 0
            continue
        largest = max(sample.faces, key=lambda face: face.area)
        if current is None or sample.scene_cut:
            current = largest.cx
            challenger_count = 0
            targets.append(current)
            continue
        anchor = current
        tracked = min(sample.faces, key=lambda face: abs(face.cx - anchor))
        if largest is not tracked and largest.area >= lc.FACE_SWITCH_RATIO * tracked.area:
            challenger_count += 1
            if challenger_count >= hold_samples:
                tracked = largest
                challenger_count = 0
        else:
            challenger_count = 0
        current = tracked.cx
        targets.append(current)
    return targets


def fill_gaps(targets: Sequence[float | None], sample_dt: float) -> list[float]:
    """Positions for samples without a face: hold the last position for 3 s, then drift to
    the center over 2 s. Before the first face, use the first face's position."""
    first = next((t for t in targets if t is not None), CENTER)
    hold = lc.FACE_HOLD_S
    filled: list[float] = []
    last = first
    missing_for = 0.0
    for target in targets:
        if target is not None:
            last = target
            missing_for = 0.0
            filled.append(target)
            continue
        missing_for += sample_dt
        if missing_for <= hold:
            filled.append(last)
        else:
            progress = min(1.0, (missing_for - hold) / lc.FACE_RECENTER_S)
            filled.append(last + (CENTER - last) * progress)
    return filled


def smooth_path(
    positions: Sequence[float], scene_cuts: Sequence[bool], sample_dt: float
) -> list[float]:
    """Exponential smoothing with a dead zone and a maximum pan speed; jumps at scene cuts."""
    max_step = lc.FACE_MAX_PAN_PER_S * sample_dt
    path: list[float] = []
    current: float | None = None
    for position, cut in zip(positions, scene_cuts, strict=True):
        if current is None or cut:
            current = position
        elif abs(position - current) >= lc.FACE_DEAD_ZONE:
            step = (position - current) * lc.FACE_SMOOTHING
            current += max(-max_step, min(max_step, step))
        path.append(current)
    return path


def track(samples: Sequence[Sample], sample_dt: float) -> list[float]:
    """Crop center (0-1) per sample."""
    targets = choose_targets(samples, sample_dt)
    positions = fill_gaps(targets, sample_dt)
    return smooth_path(positions, [s.scene_cut for s in samples], sample_dt)


def center_at(path: Sequence[float], frame: int, every: int) -> float:
    """Crop center for any frame, linearly interpolated between sampled frames."""
    if not path:
        return CENTER
    position = frame / every
    low = int(position)
    if low >= len(path) - 1:
        return path[-1]
    fraction = position - low
    return path[low] + (path[low + 1] - path[low]) * fraction
