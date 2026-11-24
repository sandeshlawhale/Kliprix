"""Gaming layouts (D-052): panel math, facecam detection math, gaming-video rule, and the
render graph with panels."""

from pathlib import Path

import numpy as np

from app.pipeline.facecam import (
    FaceBox,
    detect_tiled,
    find_facecam,
    guess_cam,
    merge_boxes,
    refine_cam,
    tiles,
)
from app.pipeline.gaming import is_gaming_video
from app.pipeline.screen import ScreenFile
from app.render.compose import RenderJob, filter_graph
from app.render.layout import (
    Rect,
    aspect_crop,
    center_panels,
    compute_layout,
    facecam_panels,
    game_window,
)

W, H = 1920, 1080


def test_caption_stays_30_percent_above_bottom_in_every_layout() -> None:
    for mode in ("face", "full", "facecam", "center"):
        assert compute_layout(mode, W, H).caption_y <= 1920 * 0.7


def test_center_layout_is_middle_3_by_4() -> None:
    layout = compute_layout("center", W, H)
    assert (layout.fg_w, layout.fg_h, layout.fg_top) == (1080, 1440, 240)
    (panel,) = center_panels(W, H)
    assert panel.src == Rect(555, 0, 810, 1080)
    assert (panel.dest_w, panel.dest_h, panel.dest_y) == (1080, 1440, 240)
    assert abs(panel.src.w / panel.src.h - 0.75) < 0.01


def test_facecam_layout_cam_on_top_game_below() -> None:
    layout = compute_layout("facecam", W, H)
    assert (layout.fg_top, layout.fg_h, layout.title_y) == (0, 1920, 576)
    cam = Rect(1534, 121, 378, 227)  # the GTA stream's cam (measured)
    top, game = facecam_panels(W, H, cam, 1715.0, 246.0)
    assert (top.dest_y, top.dest_h, game.dest_y, game.dest_h) == (0, 576, 576, 1344)
    # The top crop stays inside the cam and has the band's shape.
    assert cam.x <= top.src.x and top.src.right <= cam.right
    assert cam.y <= top.src.y and top.src.bottom <= cam.bottom
    assert abs(top.src.w / top.src.h - 1080 / 576) < 0.02
    # The game window is full height, the band's shape, centered (no overlap with the cam).
    assert game.src.h == 1080
    assert abs(game.src.w / game.src.h - 1080 / 1344) < 0.01
    assert game.src.right <= cam.x


def test_game_window_moves_off_an_overlapping_cam() -> None:
    cam = Rect(1100, 0, 400, 300)
    window = game_window(W, H, 0.8, avoid=cam)
    assert window.right <= cam.x or window.x >= cam.right
    # No room on either side: stays centered.
    wide = Rect(200, 0, 1500, 300)
    assert game_window(W, H, 0.8, avoid=wide).x == (W - 864) // 2


def test_aspect_crop_centers_on_focus_and_clamps() -> None:
    box = Rect(100, 100, 400, 400)
    crop = aspect_crop(box, 2.0, 300, 120)
    assert (crop.w, crop.h) == (400, 200)
    assert crop.y == 100  # clamped to the box top


def face(x: float, y: float, w: float = 90, score: float = 0.9) -> FaceBox:
    return FaceBox(x, y, w, w, score)


def test_find_facecam_needs_a_still_small_edge_face() -> None:
    cam_face = face(1667, 198)
    samples = [[cam_face, face(800 + i * 40, 400, 140)] for i in range(10)]
    found = find_facecam(samples, W, H)
    assert found is not None
    box, present = found
    assert (box.x, box.y, present) == (1667, 198, 1.0)


def test_find_facecam_rejects_moving_center_or_rare_faces() -> None:
    moving = [[face(100 + i * 150, 100)] for i in range(10)]
    assert find_facecam(moving, W, H) is None
    center = [[face(915, 495)] for _ in range(10)]  # a still face in the middle (a cutscene)
    assert find_facecam(center, W, H) is None
    rare = [[face(1667, 198)] if i < 4 else [] for i in range(10)]
    assert find_facecam(rare, W, H) is None
    big = [[face(1500, 100, 400)] for _ in range(10)]  # a face too big for a cam
    assert find_facecam(big, W, H) is None
    assert find_facecam([], W, H) is None


def test_merge_boxes_keeps_one_box_per_face() -> None:
    merged = merge_boxes([face(100, 100, 90, 0.8), face(104, 98, 92, 0.95), face(600, 100)])
    assert len(merged) == 2
    assert merged[0].score == 0.95


def test_tiles_cover_the_frame_with_overlap() -> None:
    grid = tiles(W, H)
    assert len(grid) == 49
    assert grid[0] == Rect(0, 0, 480, 270)
    assert grid[-1].right == W and grid[-1].bottom == H


class FakeDetector:
    """Finds a face wherever the tile contains the white square."""

    def detect_boxes(self, rgb: np.ndarray) -> list[FaceBox]:
        ys, xs = np.nonzero(rgb[:, :, 0] > 200)
        if len(xs) < 50 * 50:
            return []
        return [FaceBox(float(xs.min()), float(ys.min()), 60.0, 60.0, 0.9)]


def test_detect_tiled_returns_frame_coordinates() -> None:
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    frame[200:260, 1700:1760] = 255
    boxes = detect_tiled(frame, FakeDetector())
    assert len(boxes) == 1
    assert (boxes[0].x, boxes[0].y) == (1700, 200)


def test_refine_cam_snaps_to_the_border() -> None:
    cam_face = face(1667, 198, 96)
    guess = guess_cam(cam_face, W, 1070)
    assert guess.w == 384
    grad_x = np.full((1070, W), 1.0, dtype=np.float32)
    grad_y = np.full((1070, W), 1.0, dtype=np.float32)
    grad_x[:, 1534] = grad_x[:, 1911] = 20.0  # the cam's left and right border
    grad_y[121, :] = grad_y[347, :] = 20.0  # its top and bottom border
    assert refine_cam(guess, grad_x, grad_y, cam_face) == Rect(1534, 121, 378, 227)
    # No border anywhere: the guess stays.
    flat = np.ones((1070, W), dtype=np.float32)
    assert refine_cam(guess, flat, flat, cam_face) == guess


def screen(shares: list[list[float]]) -> ScreenFile:
    groups = ["gameplay", "menu", "waiting", "talking", "other"]
    return ScreenFile(revision="r", step_s=2.0, groups=groups, shares=shares)


def test_gaming_video_rule() -> None:
    assert is_gaming_video("MY FIRST DAY IN VICE CITY | GTA 6 GAMEPLAY #1", None, None)
    assert is_gaming_video("Jett to Rank 1 begins...", None, None)
    assert not is_gaming_video("Tvs Ronin Pros or Cons", None, None)
    game = screen([[0.75, 0.2, 0.0, 0.03, 0.02]] * 10)
    bike = screen([[0.65, 0.05, 0.01, 0.04, 0.25]] * 10)
    assert is_gaming_video("Some new indie game", None, game)
    assert not is_gaming_video("Tvs Ronin Pros or Cons", None, bike)


def test_filter_graph_with_panels() -> None:
    top, game = facecam_panels(W, H, Rect(1534, 121, 378, 227), 1715.0, 246.0)
    job = RenderJob(
        work_dir=Path("."), fg_file="cut.mp4", offset_s=0.0, duration_s=10.0,
        layout=compute_layout("facecam", W, H), blur=30, style="pop",
        words=[], title=None, panels=(top, game),
    )  # fmt: skip
    graph = filter_graph(job, "final")
    assert "[1:v]split=2[src0][src1]" in graph
    assert f"crop={top.src.w}:{top.src.h}:{top.src.x}:{top.src.y},scale=1080:576" in graph
    assert "overlay=(W-w)/2:576[over]" in graph
    assert graph.endswith("[v]")
