"""End-to-end clip render on a synthetic ffmpeg video (testsrc + sine, no copyrighted media)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.hardware import MemoryInfo
from app.pipeline.clip_render import ClipSpec, SourceVideo, render_clip
from app.pipeline.cut import CUT_FILE_NAME, cut_range, run_cut
from app.pipeline.face_crop import CROP_FILE_NAME, decide, render_crop
from app.pipeline.face_track import Sample
from app.pipeline.transcribe import Word
from app.render.compose import make_thumbnail, probe_streams

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
MEMORY = MemoryInfo(total_gb=8.0, available_gb=4.0)
SRC_W, SRC_H, LENGTH = 640, 360, 20.0


@pytest.fixture(scope="module")
def source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    assert FFMPEG is not None
    folder = tmp_path_factory.mktemp("source dir")
    path = folder / "source.mp4"
    subprocess.run(  # noqa: S603 - fixed test command
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=s={SRC_W}x{SRC_H}:r=25:d={LENGTH}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:d={LENGTH}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


WORDS = [Word(text=t, start=i * 0.5, end=i * 0.5 + 0.4) for i, t in enumerate(
    ["Synthetic", "test", "clip", "with", "captions", "only."])]  # fmt: skip


def spec(crop: str, style: str = "pop") -> ClipSpec:
    return ClipSpec(
        start_s=6.0, end_s=12.0, title="Synthetic test", show_title=True,
        style=style, crop=crop, blur=30, words=WORDS,  # type: ignore[arg-type]
    )  # fmt: skip


def test_cut_range_clamps_handles() -> None:
    assert cut_range(6.0, 12.0, 20.0) == (1.0, 17.0)
    assert cut_range(2.0, 18.0, 20.0) == (0.0, 20.0)


@needs_ffmpeg
@pytest.mark.parametrize(("quality", "size"), [("final", (1080, 1920)), ("preview", (540, 960))])
def test_render_full_frame(
    source: Path, tmp_path: Path, quality: str, size: tuple[int, int]
) -> None:
    work = tmp_path / "work dir"
    out = tmp_path / "out dir" / "clip.mp4"
    video = SourceVideo(source, LENGTH, SRC_W, SRC_H)
    result = render_clip(
        video, work, spec("full"), out, quality,  # type: ignore[arg-type]
        models_dir=tmp_path / "models", ffmpeg_path=None, memory=MEMORY,
    )  # fmt: skip
    assert result.crop.mode == "full"
    streams = probe_streams(out, None)
    assert (streams.width, streams.height) == size
    assert streams.fps == "30/1"
    assert streams.video_s == pytest.approx(6.0, abs=0.05)
    assert streams.audio_s is not None
    assert abs(streams.video_s - streams.audio_s) < 0.1
    assert (work / "captions.ass").is_file()
    thumb = make_thumbnail(out, tmp_path / "thumb.jpg", ffmpeg_path=None)
    assert thumb.stat().st_size > 0


@needs_ffmpeg
def test_face_crop_stream_and_edit_inside_handles(source: Path, tmp_path: Path) -> None:
    work = tmp_path / "work"
    info = run_cut(source, work, 1.0, 17.0, SRC_W, SRC_H, ffmpeg_path=None, memory=MEMORY)
    # No face in testsrc: forcing Face gives a centered crop and a warning (docs/05).
    decision = decide([Sample(faces=[], scene_cut=False)] * 10, "face", 30)
    assert decision.mode == "face"
    assert decision.warning is not None
    crop = render_crop(
        work / CUT_FILE_NAME, work / CROP_FILE_NAME, info.width, info.height, 16.0,
        decision.path, ffmpeg_path=None, memory=MEMORY,
    )  # fmt: skip
    streams = probe_streams(crop, None)
    assert (streams.width, streams.height) == (1080, 1350)
    assert streams.video_s == pytest.approx(16.0, abs=0.1)
    # A trim inside the handles reuses the cut (no source needed).
    no_source = SourceVideo(None, LENGTH, SRC_W, SRC_H)
    trimmed = ClipSpec(**{**spec("full").__dict__, "start_s": 3.0, "end_s": 15.0})
    out = tmp_path / "trimmed.mp4"
    render_clip(
        no_source, work, trimmed, out, "preview",
        models_dir=tmp_path / "models", ffmpeg_path=None, memory=MEMORY,
    )  # fmt: skip
    assert probe_streams(out, None).video_s == pytest.approx(12.0, abs=0.05)


def _dominant_hz(path: Path, start: float, end: float) -> float:
    """The loudest frequency of the rendered audio between start and end (seconds)."""
    import numpy as np

    assert FFMPEG is not None
    raw = subprocess.run(  # noqa: S603 - fixed test command
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", str(start), "-t",
            str(end - start), "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-",
        ],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    spectrum = np.abs(np.fft.rfft(samples))
    return float(np.fft.rfftfreq(len(samples), 1 / 8000)[int(np.argmax(spectrum))])


@needs_ffmpeg
def test_bad_words_are_bleeped(source: Path, tmp_path: Path) -> None:
    words = [
        Word(text="okay", start=0.0, end=1.0),
        Word(text="fuck", start=2.0, end=3.0),
        Word(text="bhai", start=3.5, end=4.0),
    ]
    clip = ClipSpec(
        start_s=6.0, end_s=12.0, title="Bleep test", show_title=True,
        style="pop", crop="full", blur=30, words=words,
    )  # fmt: skip
    out = tmp_path / "bleep.mp4"
    render_clip(
        SourceVideo(source, LENGTH, SRC_W, SRC_H), tmp_path / "work", clip, out, "preview",
        models_dir=tmp_path / "models", ffmpeg_path=None, memory=MEMORY,
    )  # fmt: skip
    assert _dominant_hz(out, 2.2, 2.8) == pytest.approx(1000, abs=20)
    assert _dominant_hz(out, 4.2, 5.5) == pytest.approx(440, abs=20)
    streams = probe_streams(out, None)
    assert streams.audio_s is not None
    assert abs(streams.video_s - streams.audio_s) < 0.1


def _detail(path: Path, at_s: float, x: int, y: int, w: int, h: int) -> float:
    """Share of sharp edges (a jump of more than 40 between neighbor pixels) in a region."""
    import numpy as np

    assert FFMPEG is not None
    raw = subprocess.run(  # noqa: S603 - fixed test command
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", str(at_s), "-i", str(path),
            "-frames:v", "1", "-vf", f"crop={w}:{h}:{x}:{y},format=gray", "-f", "rawvideo", "-",
        ],
        check=True, capture_output=True,
    ).stdout  # fmt: skip
    image = np.frombuffer(raw, dtype=np.uint8).reshape(h, w).astype(np.float64)
    return float((np.abs(np.diff(image, axis=1)) > 40).mean())


@needs_ffmpeg
def test_blur_box_blurs_only_its_area_and_time(source: Path, tmp_path: Path) -> None:
    from app.render.censor import BlurBox

    box = BlurBox(start=1.0, end=3.0, x=0.1, y=0.3, w=0.8, h=0.25)
    clip = ClipSpec(
        start_s=6.0, end_s=12.0, title="", show_title=False, style="pop", crop="full",
        blur=30, words=[], blur_boxes=[box],
    )  # fmt: skip
    out = tmp_path / "boxed.mp4"
    render_clip(
        SourceVideo(source, LENGTH, SRC_W, SRC_H), tmp_path / "work", clip, out, "final",
        models_dir=tmp_path / "models", ffmpeg_path=None, memory=MEMORY,
    )  # fmt: skip
    region = (200, 700, 600, 300)  # inside the box (canvas 1080x1920) and the foreground
    inside = _detail(out, 2.0, *region)
    before = _detail(out, 0.5, *region)
    after = _detail(out, 4.0, *region)
    assert before > 0.005 and after > 0.005
    assert inside < before / 5
    assert inside < after / 5


@needs_ffmpeg
def test_montage_plays_only_its_parts_and_reuses_its_cut(source: Path, tmp_path: Path) -> None:
    """D-051: the parts are cut with handles only at the outer edges, joined, and rendered
    at the parts' total length; the same parts reuse the cut without the source."""
    from app.core.errors import AppError
    from app.pipeline.cut import load_cut

    work = tmp_path / "work"
    parts = ((4.0, 7.0), (10.0, 12.0), (15.0, 17.5))
    montage = ClipSpec(**{**spec("full").__dict__, "start_s": 4.0, "end_s": 17.5, "parts": parts})
    assert montage.length_s == pytest.approx(7.5)
    video = SourceVideo(source, LENGTH, SRC_W, SRC_H)
    out = tmp_path / "montage.mp4"
    render_clip(
        video, work, montage, out, "preview",
        models_dir=tmp_path / "models", ffmpeg_path=None, memory=MEMORY,
    )  # fmt: skip
    streams = probe_streams(out, None)
    assert streams.video_s == pytest.approx(7.5, abs=0.1)
    assert streams.audio_s is not None and abs(streams.video_s - streams.audio_s) < 0.15
    cut = load_cut(work)
    assert cut is not None and cut.parts is not None
    assert cut.parts[0] == (0.0, 7.0) and cut.parts[-1] == (15.0, 20.0)
    no_source = SourceVideo(None, LENGTH, SRC_W, SRC_H)
    again = tmp_path / "again.mp4"
    render_clip(
        no_source, work, montage, again, "preview",
        models_dir=tmp_path / "models", ffmpeg_path=None, memory=MEMORY,
    )  # fmt: skip
    assert probe_streams(again, None).video_s == pytest.approx(7.5, abs=0.1)
    other = ClipSpec(**{**montage.__dict__, "parts": ((4.0, 7.0), (12.0, 17.5))})
    with pytest.raises(AppError):
        render_clip(
            no_source, work, other, tmp_path / "other.mp4", "preview",
            models_dir=tmp_path / "models", ffmpeg_path=None, memory=MEMORY,
        )  # fmt: skip
