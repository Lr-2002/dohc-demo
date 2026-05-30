#!/usr/bin/env python3
"""Generate deterministic LR-193 fake Rerun data and two native layouts."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

APP_ID = "dohc_lr193_rerun_dual_screen"
RECORDING_ID = "lr193_fake_backend_v1"
RECORDING = "default_lr193.rrd"
SCREEN1_LAYOUT = "screen1.rbl"
SCREEN2_LAYOUT = "screen2.rbl"
DEFAULT_LOGO_VIDEO = Path("dohc-two-monitor-demo/assets/08e875350fba3add6ecebe0de7d26021.mp4")
DEFAULT_OUT = Path("dohc-two-monitor-demo/out")
DEFAULT_SUMMARY = DEFAULT_OUT / "fake_lr193_summary.json"
LIGHT_BACKGROUND = [255, 255, 255]
TELEMETRY_BLUE = [85, 190, 255]
TELEMETRY_GREEN = [76, 214, 141]
TELEMETRY_YELLOW = [255, 204, 92]
POSITION_TRAJECTORY_ALPHA_FLOOR = 0.2
POSITION_TRAJECTORY_FADE_WINDOW_FRAMES = 120
LOGO_VIDEO_WIDTH = 752
LOGO_VIDEO_HEIGHT = 1280
LOGO_VIDEO_FRAME_OFFSET = 80


@dataclass(frozen=True)
class FakeBackendData:
    frames: np.ndarray
    velocity_xyz: np.ndarray
    angular_velocity_xyz: np.ndarray
    position_xyz: np.ndarray
    euler_rpy: np.ndarray


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def rgba(color: list[int], alpha: int) -> list[int]:
    return [color[0], color[1], color[2], alpha]


def draw_label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill: tuple[int, int, int]) -> None:
    font = ImageFont.load_default()
    width, height = text_size(draw, text, font)
    x, y = xy
    draw.rounded_rectangle((x - 10, y - 6, x + width + 10, y + height + 8), radius=6, fill=(8, 12, 18))
    draw.text((x, y), text, fill=fill, font=font)


def generate_backend_data(frame_count: int, fps: float) -> FakeBackendData:
    frames = np.arange(frame_count, dtype=np.int64)
    seconds = frames.astype(np.float64) / fps

    velocity_xyz = np.column_stack([
        0.42 * np.sin(seconds * 0.72) + 0.06 * np.sin(seconds * 2.3),
        0.32 * np.cos(seconds * 0.63 + 0.4),
        0.18 * np.sin(seconds * 0.37 + 1.2),
    ])
    position_xyz = np.cumsum(velocity_xyz, axis=0) / fps
    euler_rpy = np.column_stack([
        0.07 * np.sin(seconds * 0.25),
        0.05 * np.cos(seconds * 0.31),
        0.14 * np.sin(seconds * 0.18 + 0.7),
    ])
    angular_velocity_xyz = np.gradient(euler_rpy, seconds, axis=0)

    return FakeBackendData(
        frames=frames,
        velocity_xyz=velocity_xyz,
        angular_velocity_xyz=angular_velocity_xyz,
        position_xyz=position_xyz,
        euler_rpy=euler_rpy,
    )


def camera_image(camera: str, frame: int, seed: int, width: int = 1280, height: int = 720) -> Image.Image:
    rng = np.random.default_rng(seed + frame + (0 if camera == "cam0" else 1000))
    y, x = np.mgrid[0:height, 0:width]
    wave = np.sin((x + frame * 2) / 38.0) + np.cos((y - frame) / 47.0)
    noise = rng.normal(0, 4, size=(height, width))

    if camera == "cam0":
        red = 34 + 18 * wave + noise
        green = 64 + 32 * np.sin((x + frame) / 160.0)
        blue = 86 + 38 * np.cos((y + frame) / 120.0)
        accent = (85, 190, 255)
    else:
        red = 78 + 35 * np.cos((x - frame) / 140.0)
        green = 52 + 24 * wave + noise
        blue = 44 + 15 * np.sin((y + frame) / 80.0)
        accent = (76, 214, 141)

    array = np.clip(np.dstack([red, green, blue]), 0, 255).astype(np.uint8)
    image = Image.fromarray(array, mode="RGB")
    draw = ImageDraw.Draw(image)

    for offset in range(-height, width, 96):
        draw.line((offset, height, offset + width // 2, height // 2), fill=(22, 26, 31), width=2)
    for row in range(height // 2, height, 72):
        draw.line((0, row, width, row), fill=(26, 31, 36), width=2)

    draw.line((width // 2 - 70, height // 2, width // 2 - 210, height), fill=accent, width=4)
    draw.line((width // 2 + 70, height // 2, width // 2 + 210, height), fill=accent, width=4)
    draw.ellipse((width // 2 - 42, height // 2 - 42, width // 2 + 42, height // 2 + 42), outline=accent, width=3)
    draw.line((width // 2 - 90, height // 2, width // 2 + 90, height // 2), fill=accent, width=2)
    draw.line((width // 2, height // 2 - 90, width // 2, height // 2 + 90), fill=accent, width=2)
    draw_label(draw, (32, 32), f"LR-193 FAKE {camera.upper()} FRAME {frame}", accent)
    draw_label(draw, (32, 68), "backend replay placeholder", (229, 233, 238))
    return image


def deck_indicator(state: str, frame: int, width: int = 900, height: int = 720) -> Image.Image:
    ready = state == "ready"
    bg = (13, 18, 24) if ready else (30, 14, 14)
    accent = (76, 214, 141) if ready else (245, 88, 78)
    image = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    for radius in range(80, 320, 42):
        draw.ellipse(
            (width // 2 - radius, height // 2 - radius, width // 2 + radius, height // 2 + radius),
            outline=(36, 44, 52),
            width=2,
        )

    draw.rounded_rectangle((80, 80, width - 80, height - 80), radius=18, outline=accent, width=6)
    title = "DOHC DECK"
    subtitle = "READY" if ready else "INTERLOCK CHECK"
    tw, th = text_size(draw, title, font)
    sw, _ = text_size(draw, subtitle, font)
    draw.text(((width - tw) // 2, 250), title, fill=(244, 246, 248), font=font)
    draw.text(((width - sw) // 2, 250 + th + 34), subtitle, fill=accent, font=font)
    draw_label(draw, (120, 116), f"fake backend frame {frame}", (229, 233, 238))
    for index, label in enumerate(["pose", "vision", "sync"]):
        x = 170 + index * 215
        draw.rounded_rectangle((x, 510, x + 120, 590), radius=12, fill=(20, 27, 34), outline=accent, width=3)
        lw, lh = text_size(draw, label.upper(), font)
        draw.text((x + (120 - lw) // 2, 510 + (80 - lh) // 2), label.upper(), fill=accent, font=font)
    return image


def t265_image(frame: int, width: int = 960, height: int = 720) -> Image.Image:
    image = Image.new("RGB", (width, height), (10, 13, 17))
    draw = ImageDraw.Draw(image)
    accent = (255, 204, 92)
    for x in range(0, width, 64):
        draw.line((x, 0, x, height), fill=(31, 36, 42), width=1)
    for y in range(0, height, 64):
        draw.line((0, y, width, y), fill=(31, 36, 42), width=1)
    points = []
    for index in range(180):
        angle = index * 0.19
        radius = 18 + index * 2.1
        x = width // 2 + int(math.cos(angle) * radius)
        y = height // 2 + int(math.sin(angle) * radius * 0.62)
        points.append((x, y))
    draw.line(points, fill=accent, width=4)
    for x, y in points[::18]:
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(85, 190, 255))
    draw_label(draw, (32, 32), f"T265 FAKE POSE FRAME {frame}", accent)
    return image


def position_xy_grid_lines(x_range: list[float], y_range: list[float]) -> list[list[list[float]]]:
    verticals = [[[float(x), y_range[0]], [float(x), y_range[1]]] for x in np.linspace(x_range[0], x_range[1], 7)]
    horizontals = [[[x_range[0], float(y)], [x_range[1], float(y)]] for y in np.linspace(y_range[0], y_range[1], 7)]
    return verticals + horizontals


def position_xy_faded_segments(
    position_xy: np.ndarray, current_index: int
) -> tuple[list[list[list[float]]], list[list[int]]]:
    segment_count = min(current_index, len(position_xy) - 1)
    if segment_count <= 0:
        return [], []

    strips: list[list[list[float]]] = []
    colors: list[list[int]] = []
    for segment_index in range(segment_count):
        strips.append([
            position_xy[segment_index].astype(float).tolist(),
            position_xy[segment_index + 1].astype(float).tolist(),
        ])
        age = segment_count - 1 - segment_index
        alpha_fraction = max(
            POSITION_TRAJECTORY_ALPHA_FLOOR,
            1.0 - (age / POSITION_TRAJECTORY_FADE_WINDOW_FRAMES),
        )
        colors.append(rgba(TELEMETRY_BLUE, round(alpha_fraction * 255)))

    return strips, colors


def logo_video_frames(logo_video_path: Path, rr: Any) -> list[Any]:
    import av

    images = []
    with av.open(str(logo_video_path)) as container:
        video_stream = container.streams.video[0]
        for frame in container.decode(video_stream):
            images.append(rr.Image(frame.to_ndarray(format="rgb24")).compress(jpeg_quality=86))
    if not images:
        raise ValueError(f"Logo video has no readable frames: {logo_video_path}")
    return images


def logo_video_frame_index(frame: int, frame_count: int) -> int:
    return (frame + LOGO_VIDEO_FRAME_OFFSET) % frame_count


def log_recording(data: FakeBackendData, logo_video_path: Path, out_dir: Path, seed: int) -> dict[str, Any]:
    import rerun as rr

    out_dir.mkdir(parents=True, exist_ok=True)
    recording_path = out_dir / RECORDING
    representative_frame = int(data.frames[len(data.frames) // 2])
    final_frame = int(data.frames[-1])

    def rgb_image(image: Image.Image):
        return rr.Image(np.asarray(image.convert("RGB"))).compress(jpeg_quality=88)

    chart_series = {
        "/screen2/charts/velocity_xyz/vx": ("vx", TELEMETRY_BLUE, (-0.65, 0.65)),
        "/screen2/charts/velocity_xyz/vy": ("vy", TELEMETRY_GREEN, (-0.65, 0.65)),
        "/screen2/charts/velocity_xyz/vz": ("vz", TELEMETRY_YELLOW, (-0.65, 0.65)),
        "/screen2/charts/angular_velocity_xyz/wx": ("wx", TELEMETRY_BLUE, (-0.08, 0.08)),
        "/screen2/charts/angular_velocity_xyz/wy": ("wy", TELEMETRY_GREEN, (-0.08, 0.08)),
        "/screen2/charts/angular_velocity_xyz/wz": ("wz", TELEMETRY_YELLOW, (-0.08, 0.08)),
    }
    position_xy = data.position_xyz[:, :2].astype(np.float32)
    position_x_range, position_y_range = position_xy_visual_bounds(position_xy)
    grid_lines = position_xy_grid_lines(position_x_range, position_y_range)
    axes_lines = [
        [[position_x_range[0], 0.0], [position_x_range[1], 0.0]],
        [[0.0, position_y_range[0]], [0.0, position_y_range[1]]],
    ]
    logo_frames = logo_video_frames(logo_video_path, rr)

    with rr.RecordingStream(APP_ID, recording_id=RECORDING_ID) as rec:
        rec.save(recording_path)
        for frame in (0, representative_frame, final_frame):
            rec.set_time("frame", sequence=frame)
            rec.log("/screen1/cam0", rgb_image(camera_image("cam0", frame, seed)))
            rec.log("/screen1/cam1", rgb_image(camera_image("cam1", frame, seed)))
            rec.log("/screen2/t265", rgb_image(t265_image(frame)))
            rec.log("/screen2/deck_indicator", rgb_image(deck_indicator("ready" if frame else "check", frame)))

        rec.set_time("frame", sequence=0)

        for path, (name, color, _) in chart_series.items():
            rec.log(path, rr.SeriesLines(names=name, colors=color, widths=2.0), static=True)

        rec.log(
            "/screen2/position_xy/grid",
            rr.LineStrips2D(
                grid_lines,
                colors=[rgba([222, 226, 232], 96) for _ in grid_lines],
                radii=0.003,
                draw_order=0.0,
            ),
            static=True,
        )
        rec.log(
            "/screen2/position_xy/axes",
            rr.LineStrips2D(
                axes_lines,
                colors=[rgba(TELEMETRY_BLUE, 128), rgba(TELEMETRY_GREEN, 128)],
                radii=0.006,
                draw_order=2.0,
            ),
            static=True,
        )
        rec.log(
            "/screen2/position_xy/origin",
            rr.Points2D([[0.0, 0.0]], colors=[rgba([24, 32, 42], 220)], radii=0.035, draw_order=20.0),
            static=True,
        )

        for frame, velocity, angular, position in zip(
            data.frames,
            data.velocity_xyz,
            data.angular_velocity_xyz,
            data.position_xyz,
            strict=True,
        ):
            rec.set_time("frame", sequence=int(frame))
            for axis, value in zip(["vx", "vy", "vz"], velocity, strict=True):
                rec.log(f"/screen2/charts/velocity_xyz/{axis}", rr.Scalars(float(value)))
            for axis, value in zip(["wx", "wy", "wz"], angular, strict=True):
                rec.log(f"/screen2/charts/angular_velocity_xyz/{axis}", rr.Scalars(float(value)))
            trajectory_strips, trajectory_colors = position_xy_faded_segments(position_xy, int(frame))
            if trajectory_strips:
                rec.log(
                    "/screen2/position_xy/trajectory",
                    rr.LineStrips2D(
                        trajectory_strips,
                        colors=trajectory_colors,
                        radii=0.012,
                        draw_order=10.0,
                    ),
                )
            rec.log(
                "/screen2/position_xy/current",
                rr.Points2D(
                    [position[:2].astype(np.float32)],
                    colors=[rgba(TELEMETRY_YELLOW, 255)],
                    radii=0.055,
                    draw_order=30.0,
                ),
            )
            rec.log("/screen2/delta_logo", logo_frames[logo_video_frame_index(int(frame), len(logo_frames))])

    return {
        "recording": str(recording_path),
        "recording_id": RECORDING_ID,
        "representative_frame": representative_frame,
        "frame_count": len(data.frames),
        "logo_video": {
            "source": str(logo_video_path),
            "rendering": "MP4 decoded at generation time into a Rerun Image sequence on /screen2/delta_logo starting from a visible source-frame offset",
            "source_frame_count": len(logo_frames),
            "source_frame_offset": LOGO_VIDEO_FRAME_OFFSET,
            "looped_by_frame_index": len(data.frames) > len(logo_frames),
            "compatibility_reason": "The current deployed web-viewer browser path lacks WebCodecs VideoDecoder, so native AssetVideo does not render there.",
        },
        "position_xy": {
            "trajectory_alpha_floor": POSITION_TRAJECTORY_ALPHA_FLOOR,
            "trajectory_fade_window_frames": POSITION_TRAJECTORY_FADE_WINDOW_FRAMES,
        },
        "entities": [
            "/screen1/cam0",
            "/screen1/cam1",
            "/screen2/charts/velocity_xyz/vx",
            "/screen2/charts/velocity_xyz/vy",
            "/screen2/charts/velocity_xyz/vz",
            "/screen2/charts/angular_velocity_xyz/wx",
            "/screen2/charts/angular_velocity_xyz/wy",
            "/screen2/charts/angular_velocity_xyz/wz",
            "/screen2/position_xy/grid",
            "/screen2/position_xy/axes",
            "/screen2/position_xy/trajectory",
            "/screen2/position_xy/origin",
            "/screen2/position_xy/current",
            "/screen2/deck_indicator",
            "/screen2/delta_logo",
            "/screen2/t265",
        ],
    }


def frame_time_range(frame_count: int):
    import rerun.blueprint as rrb

    return rrb.VisibleTimeRange(
        "frame",
        start=rrb.TimeRangeBoundary.absolute(seq=0),
        end=rrb.TimeRangeBoundary.absolute(seq=frame_count - 1),
    )


def frame_time_axis(frame_count: int):
    import rerun.blueprint as rrb

    return rrb.TimeAxis(
        view_range=rrb.TimeRange(
            start=rrb.TimeRangeBoundary.absolute(seq=0),
            end=rrb.TimeRangeBoundary.absolute(seq=frame_count - 1),
        ),
        zoom_lock=True,
    )


def position_xy_visual_bounds(position_xy: np.ndarray) -> tuple[list[float], list[float]]:
    minimum = np.min(position_xy, axis=0)
    maximum = np.max(position_xy, axis=0)
    span = np.maximum(maximum - minimum, 0.5)
    padding = span * 0.25 + 0.1

    x_range = [float(minimum[0] - padding[0]), float(maximum[0] + padding[0])]
    y_range = [float(minimum[1] - padding[1]), float(maximum[1] + padding[1])]
    return x_range, y_range


def make_screen1_blueprint(path: Path, frame_count: int) -> None:
    import rerun.blueprint as rrb

    time_range = frame_time_range(frame_count)
    camera_bounds = rrb.VisualBounds2D(x_range=[0, 1280], y_range=[0, 720])
    rrb.Blueprint(
        rrb.Horizontal(
            rrb.Spatial2DView(
                name="Cam0",
                origin="/screen1/cam0",
                contents=["/screen1/cam0"],
                background=[8, 10, 12],
                visual_bounds=camera_bounds,
                time_ranges=time_range,
            ),
            rrb.Spatial2DView(
                name="Cam1",
                origin="/screen1/cam1",
                contents=["/screen1/cam1"],
                background=[8, 10, 12],
                visual_bounds=camera_bounds,
                time_ranges=time_range,
            ),
            column_shares=[1, 1],
        ),
        rrb.TimePanel(state="hidden", timeline="frame", play_state="paused", fps=30.0),
        rrb.SelectionPanel(state="collapsed"),
        rrb.BlueprintPanel(state="collapsed"),
        collapse_panels=True,
    ).save(APP_ID, path)


def make_screen2_blueprint(path: Path, data: FakeBackendData) -> None:
    import rerun.blueprint as rrb

    frame_count = len(data.frames)
    time_range = frame_time_range(frame_count)
    time_axis = frame_time_axis(frame_count)
    image_bounds = rrb.VisualBounds2D(x_range=[0, 900], y_range=[0, 720])
    logo_bounds = rrb.VisualBounds2D(x_range=[0, LOGO_VIDEO_WIDTH], y_range=[0, LOGO_VIDEO_HEIGHT])
    position_x_range, position_y_range = position_xy_visual_bounds(data.position_xyz[:, :2])
    position_bounds = rrb.VisualBounds2D(x_range=position_x_range, y_range=position_y_range)
    plot_background = rrb.archetypes.PlotBackground(color=LIGHT_BACKGROUND, show_grid=True)
    rrb.Blueprint(
        rrb.Horizontal(
            rrb.Vertical(
                rrb.TimeSeriesView(
                    name="Velocity XYZ",
                    origin="/screen2/charts/velocity_xyz",
                    time_ranges=time_range,
                    axis_x=time_axis,
                    plot_legend=rrb.PlotLegend(visible=True),
                    axis_y=rrb.ScalarAxis(range=(-0.65, 0.65)),
                    background=plot_background,
                ),
                rrb.TimeSeriesView(
                    name="Angular velocity XYZ",
                    origin="/screen2/charts/angular_velocity_xyz",
                    time_ranges=time_range,
                    axis_x=time_axis,
                    plot_legend=rrb.PlotLegend(visible=True),
                    axis_y=rrb.ScalarAxis(range=(-0.08, 0.08)),
                    background=plot_background,
                ),
                rrb.Spatial2DView(
                    name="Position XY",
                    origin="/screen2/position_xy",
                    background=LIGHT_BACKGROUND,
                    visual_bounds=position_bounds,
                    time_ranges=time_range,
                ),
                row_shares=[1, 1, 1],
            ),
            rrb.Spatial2DView(
                name="",
                origin="/screen2/delta_logo",
                contents=["/screen2/delta_logo"],
                background=LIGHT_BACKGROUND,
                visual_bounds=logo_bounds,
                time_ranges=time_range,
            ),
            rrb.Spatial2DView(
                name="DOHC DECK",
                origin="/screen2/deck_indicator",
                contents=["/screen2/deck_indicator"],
                background=LIGHT_BACKGROUND,
                visual_bounds=image_bounds,
                time_ranges=time_range,
            ),
            column_shares=[1.2, 0.8, 1],
        ),
        rrb.TimePanel(state="hidden", timeline="frame", play_state="paused", fps=30.0),
        rrb.SelectionPanel(state="collapsed"),
        rrb.BlueprintPanel(state="collapsed"),
        collapse_panels=True,
    ).save(APP_ID, path)


def write_blueprints(out_dir: Path, data: FakeBackendData) -> dict[str, str]:
    layouts = out_dir / "layouts"
    layouts.mkdir(parents=True, exist_ok=True)
    screen1 = layouts / SCREEN1_LAYOUT
    screen2 = layouts / SCREEN2_LAYOUT
    make_screen1_blueprint(screen1, len(data.frames))
    make_screen2_blueprint(screen2, data)
    return {
        "screen1_layout": str(screen1),
        "screen2_layout": str(screen2),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--logo-video", type=Path, default=DEFAULT_LOGO_VIDEO)
    parser.add_argument("--summary-out", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--frame-count", type=int, default=360)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=193)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if args.frame_count < 2:
        raise ValueError("--frame-count must be at least 2")
    if not math.isfinite(args.fps) or args.fps <= 0:
        raise ValueError("--fps must be a positive finite number")
    if not args.logo_video.is_file():
        raise FileNotFoundError(args.logo_video)


def main() -> None:
    args = parse_args()
    validate_args(args)

    data = generate_backend_data(args.frame_count, args.fps)
    recording = log_recording(data, args.logo_video, args.out, args.seed)
    blueprints = write_blueprints(args.out, data)

    summary = {
        "contract": "LR-193-fake-backend-v1",
        "app_id": APP_ID,
        "seed": args.seed,
        "fps": args.fps,
        "recording": recording,
        "blueprints": blueprints,
        "real_data_input_boundary": {
            "keep_entity_paths": recording["entities"],
            "replace_with_real_inputs": [
                "cam0 and cam1 encoded image bytes or RGB arrays",
                "deck status image or state sequence for /screen2/deck_indicator",
                "logo MP4 path or bytes decoded into the /screen2/delta_logo image sequence",
                "pose samples containing frame, position_xyz or position_xy, velocity_xyz, and euler_rpy",
                "XY pose samples drive the faded /screen2/position_xy/trajectory and /screen2/position_xy/current",
                "optional T265 image bytes for /screen2/t265",
            ],
            "layout_contract": "The two .rbl files depend on the entity paths above, not on wrapper HTML.",
        },
    }
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
