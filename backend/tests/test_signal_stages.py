"""Audio and motion stages and the streaming helper on synthetic ffmpeg media."""

import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.core.hardware import MemoryInfo
from app.core.proc import (
    CommandCancelledError,
    CommandFailedError,
    CommandNotFoundError,
    CommandTimeoutError,
    StreamingCommand,
)
from app.pipeline.audio import AUDIO_FILE_NAME, run_audio, wav_duration_s
from app.pipeline.motion import (
    analysis_height,
    keyframe_gap_s,
    keyframe_mode_gap,
    motion_series,
    run_motion,
    spread_repeats,
)
from app.pipeline.signals import load_signal

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
MEMORY = MemoryInfo(total_gb=8.0, available_gb=4.0)
DURATION = 12
PY = sys.executable


@pytest.fixture(scope="module")
def video_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """12 s video: still for 6 s, then a moving test pattern; a beep from 8 s."""
    assert FFMPEG is not None
    folder = tmp_path_factory.mktemp("video dir")
    graph = "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a]concat=n=2:v=0:a=1[a]"
    subprocess.run(  # noqa: S603 - fixed test command
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=6",
            "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=6",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=8",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100:d=4",
            "-filter_complex", graph,
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(folder / "source.mp4"),
        ],
        check=True,
    )  # fmt: skip
    return folder


@needs_ffmpeg
def test_audio_stage_writes_wav_and_signal(video_dir: Path) -> None:
    signal = run_audio(video_dir, DURATION, ffmpeg_path=None, memory=MEMORY)
    assert wav_duration_s(video_dir / AUDIO_FILE_NAME) == pytest.approx(DURATION, abs=0.1)
    assert len(signal.values) == DURATION
    # The beep (8-12 s) is louder than the silence before it.
    assert min(signal.values[8:11]) > max(signal.values[1:7])
    assert load_signal(video_dir, "audio", DURATION) == signal


@needs_ffmpeg
def test_motion_stage_sees_movement(video_dir: Path) -> None:
    signal = run_motion(video_dir, DURATION, 320, 180, ffmpeg_path=None, memory=MEMORY)
    assert len(signal.values) == DURATION
    assert max(signal.values[1:6]) == 0.0
    assert min(signal.values[7:11]) > 0.0


@needs_ffmpeg
def test_audio_stage_without_audio_track(tmp_path: Path) -> None:
    assert FFMPEG is not None
    subprocess.run(  # noqa: S603 - fixed test command
        [
            FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "source.mp4"),
        ],
        check=True,
    )  # fmt: skip
    signal = run_audio(tmp_path, 4.0, ffmpeg_path=None, memory=MEMORY)
    assert signal.values == [0.0] * 4


def test_analysis_height() -> None:
    assert analysis_height(1920, 1080) == 90
    assert analysis_height(1080, 1920) == 284


def test_streaming_command_reads_exact_chunks() -> None:
    script = "import sys; sys.stdout.write('abcdefg')"
    with StreamingCommand([PY, "-c", script], 10) as cmd:
        assert cmd.read_exact(3) == b"abc"
        assert cmd.read_exact(3) == b"def"
        assert cmd.read_exact(3) == b"g"
        assert cmd.read_exact(3) is None


def test_streaming_command_failure_has_stderr_tail() -> None:
    script = "import sys; sys.stderr.write('boom'); sys.exit(3)"
    with pytest.raises(CommandFailedError) as info, StreamingCommand([PY, "-c", script], 10):
        pass
    assert "boom" in info.value.stderr_tail


def test_streaming_command_timeout_and_cancel() -> None:
    sleeper = [PY, "-c", "import time; time.sleep(30)"]
    with pytest.raises(CommandTimeoutError), StreamingCommand(sleeper, 0.5) as cmd:
        cmd.read_exact(10)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(CommandCancelledError), StreamingCommand(sleeper, 30, cancel) as cmd:
        cmd.read_exact(10)


def test_streaming_command_stdin_pipe() -> None:
    script = "import sys; data = sys.stdin.buffer.read(); sys.stdout.buffer.write(data[::-1])"
    with StreamingCommand([PY, "-c", script], 10, stdin_pipe=True) as cmd:
        cmd.write(b"xyz")
        cmd.close_stdin()
        assert cmd.read_exact(3) == b"zyx"


def test_streaming_command_missing_program(tmp_path: Path) -> None:
    with pytest.raises(CommandNotFoundError):
        StreamingCommand([str(tmp_path / "nope.exe")], 5)


def test_spread_repeats_shares_a_keyframe_change_over_its_run() -> None:
    # Keyframes at 0, 3 and 5 s: the fps filter repeats them, so changes arrive at 3 and 5.
    assert spread_repeats([0.0, 0.0, 0.0, 6.0, 0.0, 4.0], 10) == [0.0, 2.0, 2.0, 2.0, 2.0, 2.0]
    # A run longer than max_run keeps zeros before it; index 0 is never part of a run.
    assert spread_repeats([5.0, 0.0, 0.0, 0.0, 9.0], 3) == [5.0, 0.0, 3.0, 3.0, 3.0]
    # Trailing zeros (no later keyframe) and real values stay as they are.
    assert spread_repeats([0.0, 1.0, 2.0, 0.0], 10) == [0.0, 1.0, 2.0, 0.0]


@pytest.fixture(scope="module")
def keyframed_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """12 s video with a keyframe every 2 s: still for 6 s, then a moving test pattern."""
    assert FFMPEG is not None
    folder = tmp_path_factory.mktemp("keyframed")
    subprocess.run(  # noqa: S603 - fixed test command
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=6",
            "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=6",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
            "-c:v", "libx264", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
            "-pix_fmt", "yuv420p", str(folder / "source.mp4"),
        ],
        check=True,
    )  # fmt: skip
    return folder


@needs_ffmpeg
def test_keyframe_gap_is_read_from_packets(keyframed_dir: Path) -> None:
    assert keyframe_gap_s(keyframed_dir / "source.mp4", None) == pytest.approx(2.0)


@needs_ffmpeg
def test_keyframe_motion_sees_movement(keyframed_dir: Path) -> None:
    values = motion_series(
        keyframed_dir, DURATION, 320, 180, ffmpeg_path=None, memory=MEMORY, keyframe_gap=2.0
    )
    assert len(values) == DURATION
    # The change at the 6 s keyframe is shared with second 5 only (one keyframe gap).
    assert max(values[1:5]) == 0.0
    # The last keyframe (10 s) has no length, so the fps filter drops it; the tail is padded.
    assert min(values[5:9]) > 0.0


@needs_ffmpeg
def test_short_videos_keep_the_full_decode(keyframed_dir: Path) -> None:
    assert keyframe_mode_gap(keyframed_dir / "source.mp4", DURATION, None) is None


@needs_ffmpeg
def test_motion_and_audio_report_progress(video_dir: Path, tmp_path: Path) -> None:
    from app.pipeline.audio import audio_series
    from app.pipeline.motion import motion_series

    seen: list[float] = []
    motion_series(video_dir, DURATION, 320, 180, ffmpeg_path=None, memory=MEMORY,
                  on_progress=seen.append)  # fmt: skip
    assert seen == sorted(seen)
    assert seen[-1] == 1.0
    audio_seen: list[float] = []
    audio_series(video_dir / AUDIO_FILE_NAME, DURATION, on_progress=audio_seen.append)
    assert audio_seen[-1] == 1.0
