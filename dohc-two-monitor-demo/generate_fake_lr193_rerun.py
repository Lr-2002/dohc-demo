#!/usr/bin/env python3
"""Generate deterministic LR-193 fake Rerun data and two native layouts."""

from __future__ import annotations

import argparse
import io
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
DEFAULT_ICON = Path("dohc-two-monitor-demo/assets/icon.png")
DEFAULT_OUT = Path("dohc-two-monitor-demo/out")
DEFAULT_SUMMARY = DEFAULT_OUT / "fake_lr193_summary.json"


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


def encode_image(image: Image.Image, media_type: str = "image/jpeg") -> bytes:
    buffer = io.BytesIO()
    if media_type == "image/jpeg":
        image.convert("RGB").save(buffer, format="JPEG", quality=88, optimize=True)
    elif media_type == "image/png":
        image.save(buffer, format="PNG", optimize=True)
    else:
        raise ValueError(f"unsupported media type: {media_type}")
    return buffer.getvalue()


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
    title = "DOHE DECK"
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


def log_recording(data: FakeBackendData, icon_path: Path, out_dir: Path, seed: int) -> dict[str, Any]:
    import rerun as rr

    out_dir.mkdir(parents=True, exist_ok=True)
    recording_path = out_dir / RECORDING
    representative_frame = int(data.frames[len(data.frames) // 2])
    final_frame = int(data.frames[-1])

    with rr.RecordingStream(APP_ID, recording_id=RECORDING_ID) as rec:
        rec.save(recording_path)
        rec.log(
            "/screen1/cam0",
            rr.EncodedImage(
                contents=encode_image(camera_image("cam0", representative_frame, seed)),
                media_type="image/jpeg",
            ),
            static=True,
        )
        rec.log(
            "/screen1/cam1",
            rr.EncodedImage(
                contents=encode_image(camera_image("cam1", representative_frame, seed)),
                media_type="image/jpeg",
            ),
            static=True,
        )
        rec.log(
            "/screen2/t265",
            rr.EncodedImage(contents=encode_image(t265_image(representative_frame)), media_type="image/jpeg"),
            static=True,
        )
        rec.log(
            "/screen2/delta_logo",
            rr.EncodedImage(path=icon_path, media_type="image/png", magnification_filter="linear"),
            static=True,
        )

        rec.set_time("frame", sequence=int(data.frames[0]))
        rec.log(
            "/screen2/deck_indicator",
            rr.EncodedImage(
                contents=encode_image(deck_indicator("check", int(data.frames[0]))), media_type="image/jpeg"
            ),
        )
        rec.set_time("frame", sequence=final_frame)
        rec.log(
            "/screen2/deck_indicator",
            rr.EncodedImage(contents=encode_image(deck_indicator("ready", final_frame)), media_type="image/jpeg"),
        )

        rec.log(
            "/screen2/charts/velocity_xyz",
            rr.SeriesLines(
                names=["vx", "vy", "vz"],
                colors=[[85, 190, 255], [76, 214, 141], [255, 204, 92]],
                widths=[2.0, 2.0, 2.0],
            ),
            static=True,
        )
        rec.log(
            "/screen2/charts/angular_velocity_xyz",
            rr.SeriesLines(
                names=["wx", "wy", "wz"],
                colors=[[85, 190, 255], [76, 214, 141], [255, 204, 92]],
                widths=[2.0, 2.0, 2.0],
            ),
            static=True,
        )
        rec.log(
            "/screen2/charts/position_xy",
            rr.SeriesLines(
                names=["x", "y"],
                colors=[[85, 190, 255], [76, 214, 141]],
                widths=[2.0, 2.0],
            ),
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
            rec.log("/screen2/charts/velocity_xyz", rr.Scalars(velocity.tolist()))
            rec.log("/screen2/charts/angular_velocity_xyz", rr.Scalars(angular.tolist()))
            rec.log("/screen2/charts/position_xy", rr.Scalars(position[:2].tolist()))

    return {
        "recording": str(recording_path),
        "recording_id": RECORDING_ID,
        "representative_frame": representative_frame,
        "frame_count": len(data.frames),
        "entities": [
            "/screen1/cam0",
            "/screen1/cam1",
            "/screen2/charts/velocity_xyz",
            "/screen2/charts/angular_velocity_xyz",
            "/screen2/charts/position_xy",
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


def make_screen1_blueprint(path: Path, frame_count: int) -> None:
    import rerun.blueprint as rrb

    time_range = frame_time_range(frame_count)
    rrb.Blueprint(
        rrb.Horizontal(
            rrb.Spatial2DView(name="Cam0", origin="/screen1/cam0", background=[8, 10, 12], time_ranges=time_range),
            rrb.Spatial2DView(name="Cam1", origin="/screen1/cam1", background=[8, 10, 12], time_ranges=time_range),
            column_shares=[1, 1],
        ),
        rrb.TimePanel(state="hidden", timeline="frame", play_state="paused", fps=30.0),
        rrb.SelectionPanel(state="collapsed"),
        rrb.BlueprintPanel(state="collapsed"),
        collapse_panels=True,
    ).save(APP_ID, path)


def make_screen2_blueprint(path: Path, frame_count: int) -> None:
    import rerun.blueprint as rrb

    time_range = frame_time_range(frame_count)
    time_axis = frame_time_axis(frame_count)
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
                ),
                rrb.TimeSeriesView(
                    name="Angular velocity XYZ",
                    origin="/screen2/charts/angular_velocity_xyz",
                    time_ranges=time_range,
                    axis_x=time_axis,
                    plot_legend=rrb.PlotLegend(visible=True),
                    axis_y=rrb.ScalarAxis(range=(-0.08, 0.08)),
                ),
                rrb.TimeSeriesView(
                    name="Position XY",
                    origin="/screen2/charts/position_xy",
                    time_ranges=time_range,
                    axis_x=time_axis,
                    plot_legend=rrb.PlotLegend(visible=True),
                    axis_y=rrb.ScalarAxis(range=(-2.5, 2.5)),
                ),
                row_shares=[1, 1, 1],
            ),
            rrb.Spatial2DView(
                name="DOHE DECK", origin="/screen2/deck_indicator", background=[8, 10, 12], time_ranges=time_range
            ),
            rrb.Spatial2DView(
                name="", origin="/screen2/delta_logo", background=[244, 244, 240], time_ranges=time_range
            ),
            column_shares=[1.2, 1, 0.8],
        ),
        rrb.TimePanel(state="hidden", timeline="frame", play_state="paused", fps=30.0),
        rrb.SelectionPanel(state="collapsed"),
        rrb.BlueprintPanel(state="collapsed"),
        collapse_panels=True,
    ).save(APP_ID, path)


def write_blueprints(out_dir: Path, frame_count: int) -> dict[str, str]:
    layouts = out_dir / "layouts"
    layouts.mkdir(parents=True, exist_ok=True)
    screen1 = layouts / SCREEN1_LAYOUT
    screen2 = layouts / SCREEN2_LAYOUT
    make_screen1_blueprint(screen1, frame_count)
    make_screen2_blueprint(screen2, frame_count)
    return {
        "screen1_layout": str(screen1),
        "screen2_layout": str(screen2),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--icon", type=Path, default=DEFAULT_ICON)
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
    if not args.icon.is_file():
        raise FileNotFoundError(args.icon)


def main() -> None:
    args = parse_args()
    validate_args(args)

    data = generate_backend_data(args.frame_count, args.fps)
    recording = log_recording(data, args.icon, args.out, args.seed)
    blueprints = write_blueprints(args.out, args.frame_count)

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
                "pose samples containing frame, position_xyz, velocity_xyz, and euler_rpy",
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
