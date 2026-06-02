#!/usr/bin/env python3
"""Generate LR-193 native Rerun artifacts from the readable H5 capture."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from generate_fake_lr193_rerun import (
    APP_ID,
    BLACK_BACKGROUND,
    DEFAULT_DECK_SIDE,
    DEFAULT_DHOC_BOTTOM_VIEW,
    DEFAULT_LOGO_VIDEO,
    DEFAULT_OUT,
    LIGHT_BACKGROUND,
    LOGO_VIDEO_HEIGHT,
    LOGO_VIDEO_WIDTH,
    POSITION_AXIS_COLOR,
    POSITION_AXIS_RADIUS,
    POSITION_CURRENT_RADIUS,
    POSITION_GRID_COLOR,
    POSITION_GRID_RADIUS,
    POSITION_HEAD_BLUE,
    POSITION_TRAJECTORY_RADIUS,
    RIGHT_ASSET_HEIGHT,
    RIGHT_ASSET_WIDTH,
    SCREEN1_LAYOUT,
    SCREEN2_LAYOUT,
    TELEMETRY_BLUE,
    TELEMETRY_GREEN,
    TELEMETRY_YELLOW,
    logo_video_frames,
    position_xy_faded_segments,
    position_xy_frame_lines,
    position_xy_grid_lines,
    position_xy_head_segments,
    position_xy_visual_bounds,
    rgba,
    transparent_asset_image,
)
from h5_target_data import (
    DEFAULT_FPS,
    H5AccessError,
    build_summary,
    load_targets,
    open_h5_fail_fast,
)

RECORDING = "default_lr193.rrd"
RECORDING_ID = "lr193_h5_attachment_019e86b2_8777_75b6_ba70_662be2fab741"
DEFAULT_SUMMARY = DEFAULT_OUT / "h5_lr193_summary.json"
DEFAULT_H5 = Path("/tmp/lr193_h5_probe/2026-05-29 10:05:30.h5")


def cam0_jpeg_bytes(h5_file: h5py.File, frame_name: str) -> bytes:
    return bytes(h5_file[frame_name]["cam0"][()])


def nice_symmetric_range(values: np.ndarray, minimum_half_extent: float) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        half_extent = minimum_half_extent
    else:
        half_extent = max(float(np.max(np.abs(finite))) * 1.08, minimum_half_extent)
    magnitude = 10 ** math.floor(math.log10(half_extent))
    normalized = half_extent / magnitude
    if normalized <= 1:
        nice = 1
    elif normalized <= 2:
        nice = 2
    elif normalized <= 5:
        nice = 5
    else:
        nice = 10
    rounded = float(nice * magnitude)
    return (-rounded, rounded)


def frame_time_range(first_frame: int, last_frame: int):
    import rerun.blueprint as rrb

    return rrb.VisibleTimeRange(
        "frame",
        start=rrb.TimeRangeBoundary.absolute(seq=first_frame),
        end=rrb.TimeRangeBoundary.absolute(seq=last_frame),
    )


def frame_time_axis(first_frame: int, last_frame: int):
    import rerun.blueprint as rrb

    return rrb.TimeAxis(
        view_range=rrb.TimeRange(
            start=rrb.TimeRangeBoundary.absolute(seq=first_frame),
            end=rrb.TimeRangeBoundary.absolute(seq=last_frame),
        ),
        zoom_lock=True,
    )


def playing_time_panel(fps: float):
    import rerun.blueprint as rrb

    return rrb.TimePanel(
        state="hidden",
        timeline="frame",
        play_state=rrb.components.PlayState.Playing,
        loop_mode=rrb.components.LoopMode.All,
        fps=fps,
        playback_speed=1.0,
    )


def make_screen1_blueprint(path: Path, first_frame: int, last_frame: int, fps: float) -> None:
    import rerun.blueprint as rrb

    camera_bounds = rrb.VisualBounds2D(x_range=[0, 1280], y_range=[0, 720])
    rrb.Blueprint(
        rrb.Spatial2DView(
            name="Cam0",
            origin="/screen1/cam0",
            contents=["/screen1/cam0"],
            background=[8, 10, 12],
            visual_bounds=camera_bounds,
            time_ranges=frame_time_range(first_frame, last_frame),
        ),
        playing_time_panel(fps),
        rrb.SelectionPanel(state="collapsed"),
        rrb.BlueprintPanel(state="collapsed"),
        collapse_panels=True,
    ).save(APP_ID, path)


def make_screen2_blueprint(
    path: Path,
    frames: np.ndarray,
    position_xy: np.ndarray,
    velocity_xyz: np.ndarray,
    omega_xyz: np.ndarray,
    fps: float,
) -> dict[str, Any]:
    import rerun.blueprint as rrb

    first_frame = int(frames[0])
    last_frame = int(frames[-1])
    time_range = frame_time_range(first_frame, last_frame)
    time_axis = frame_time_axis(first_frame, last_frame)
    right_image_bounds = rrb.VisualBounds2D(x_range=[0, RIGHT_ASSET_WIDTH], y_range=[0, RIGHT_ASSET_HEIGHT])
    logo_bounds = rrb.VisualBounds2D(x_range=[0, LOGO_VIDEO_WIDTH], y_range=[0, LOGO_VIDEO_HEIGHT])
    position_x_range, position_y_range = position_xy_visual_bounds(position_xy)
    position_bounds = rrb.VisualBounds2D(x_range=position_x_range, y_range=position_y_range)
    velocity_range = nice_symmetric_range(velocity_xyz, 0.1)
    omega_range = nice_symmetric_range(omega_xyz, 0.1)
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
                    axis_y=rrb.ScalarAxis(range=velocity_range),
                    background=plot_background,
                ),
                rrb.TimeSeriesView(
                    name="Angular velocity XYZ",
                    origin="/screen2/charts/angular_velocity_xyz",
                    time_ranges=time_range,
                    axis_x=time_axis,
                    plot_legend=rrb.PlotLegend(visible=True),
                    axis_y=rrb.ScalarAxis(range=omega_range),
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
            rrb.Vertical(
                rrb.Spatial2DView(
                    name="dhoc top-down",
                    origin="/screen2/right/dhoc_bottom_view",
                    contents=["/screen2/right/dhoc_bottom_view"],
                    background=BLACK_BACKGROUND,
                    visual_bounds=right_image_bounds,
                    time_ranges=time_range,
                ),
                rrb.Spatial2DView(
                    name="deck side",
                    origin="/screen2/right/deck_side",
                    contents=["/screen2/right/deck_side"],
                    background=BLACK_BACKGROUND,
                    visual_bounds=right_image_bounds,
                    time_ranges=time_range,
                ),
                row_shares=[1, 1],
            ),
            column_shares=[1.2, 0.8, 1],
        ),
        playing_time_panel(fps),
        rrb.SelectionPanel(state="collapsed"),
        rrb.BlueprintPanel(state="collapsed"),
        collapse_panels=True,
    ).save(APP_ID, path)

    return {
        "velocity_axis_range": list(velocity_range),
        "omega_axis_range": list(omega_range),
        "position_visual_bounds": {
            "x_range": position_x_range,
            "y_range": position_y_range,
        },
    }


def write_blueprints(
    out_dir: Path,
    frames: np.ndarray,
    position_xy: np.ndarray,
    velocity_xyz: np.ndarray,
    omega_xyz: np.ndarray,
    fps: float,
) -> dict[str, Any]:
    layouts = out_dir / "layouts"
    layouts.mkdir(parents=True, exist_ok=True)
    screen1 = layouts / SCREEN1_LAYOUT
    screen2 = layouts / SCREEN2_LAYOUT
    make_screen1_blueprint(screen1, int(frames[0]), int(frames[-1]), fps)
    screen2_details = make_screen2_blueprint(screen2, frames, position_xy, velocity_xyz, omega_xyz, fps)
    return {
        "screen1_layout": str(screen1),
        "screen2_layout": str(screen2),
        **screen2_details,
    }


def log_recording(
    h5_file: h5py.File,
    targets: Any,
    logo_video_path: Path,
    dhoc_bottom_view_path: Path,
    deck_side_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    import rerun as rr

    if targets.omega_xyz is None:
        raise ValueError("OmegaXYZ is required for the LR-193 native viewer recording")

    out_dir.mkdir(parents=True, exist_ok=True)
    recording_path = out_dir / RECORDING
    frames = targets.frames.astype(np.int64)
    position_xy = targets.xy.astype(np.float32)
    velocity_xyz = targets.vxyz.astype(np.float64)
    omega_xyz = targets.omega_xyz.astype(np.float64)
    position_x_range, position_y_range = position_xy_visual_bounds(position_xy)
    grid_lines = position_xy_grid_lines(position_x_range, position_y_range)
    frame_lines = position_xy_frame_lines(position_x_range, position_y_range)
    axes_lines = [
        [[position_x_range[0], 0.0], [position_x_range[1], 0.0]],
        [[0.0, position_y_range[0]], [0.0, position_y_range[1]]],
    ]
    logo_frames, logo_source_frame_count = logo_video_frames(logo_video_path, rr)
    dhoc_bottom_view = transparent_asset_image(dhoc_bottom_view_path, rr)
    deck_side = transparent_asset_image(deck_side_path, rr)

    with rr.RecordingStream(APP_ID, recording_id=RECORDING_ID) as rec:
        rec.save(recording_path)
        rec.set_time("frame", sequence=int(frames[0]))
        rec.log(
            "/screen2/right/dhoc_bottom_view",
            dhoc_bottom_view,
            static=True,
        )
        rec.log(
            "/screen2/right/deck_side",
            deck_side,
            static=True,
        )
        rec.log(
            "/screen2/position_xy/grid",
            rr.LineStrips2D(
                grid_lines,
                colors=[rgba(POSITION_GRID_COLOR, 128) for _ in grid_lines],
                radii=POSITION_GRID_RADIUS,
                draw_order=0.0,
            ),
            static=True,
        )
        rec.log(
            "/screen2/position_xy/frame",
            rr.LineStrips2D(
                frame_lines,
                colors=[rgba(POSITION_GRID_COLOR, 176)],
                radii=POSITION_GRID_RADIUS,
                draw_order=1.0,
            ),
            static=True,
        )
        rec.log(
            "/screen2/position_xy/axes",
            rr.LineStrips2D(
                axes_lines,
                colors=[rgba(POSITION_AXIS_COLOR, 160), rgba(POSITION_AXIS_COLOR, 160)],
                radii=POSITION_AXIS_RADIUS,
                draw_order=2.0,
            ),
            static=True,
        )
        rec.log(
            "/screen2/position_xy/origin",
            rr.Points2D([[0.0, 0.0]], colors=[rgba(POSITION_HEAD_BLUE, 220)], radii=0.035, draw_order=20.0),
            static=True,
        )

        for path, (name, color) in {
            "/screen2/charts/velocity_xyz/vx": ("vx", TELEMETRY_BLUE),
            "/screen2/charts/velocity_xyz/vy": ("vy", TELEMETRY_GREEN),
            "/screen2/charts/velocity_xyz/vz": ("vz", TELEMETRY_YELLOW),
            "/screen2/charts/angular_velocity_xyz/wx": ("wx", TELEMETRY_BLUE),
            "/screen2/charts/angular_velocity_xyz/wy": ("wy", TELEMETRY_GREEN),
            "/screen2/charts/angular_velocity_xyz/wz": ("wz", TELEMETRY_YELLOW),
        }.items():
            rec.log(path, rr.SeriesLines(names=name, colors=color, widths=2.0), static=True)

        for offset, (frame, frame_name, velocity, angular) in enumerate(
            zip(frames, targets.frame_names, velocity_xyz, omega_xyz, strict=True)
        ):
            rec.set_time("frame", sequence=int(frame))
            rec.log(
                "/screen1/cam0",
                rr.EncodedImage(contents=cam0_jpeg_bytes(h5_file, frame_name), media_type="image/jpeg"),
            )
            for axis, value in zip(["vx", "vy", "vz"], velocity, strict=True):
                rec.log(f"/screen2/charts/velocity_xyz/{axis}", rr.Scalars(float(value)))
            for axis, value in zip(["wx", "wy", "wz"], angular, strict=True):
                rec.log(f"/screen2/charts/angular_velocity_xyz/{axis}", rr.Scalars(float(value)))

            trajectory_strips, trajectory_colors = position_xy_faded_segments(position_xy, offset)
            rec.log(
                "/screen2/position_xy/trajectory",
                rr.LineStrips2D(
                    trajectory_strips,
                    colors=trajectory_colors,
                    radii=POSITION_TRAJECTORY_RADIUS,
                    draw_order=10.0,
                ),
            )
            head_strips = position_xy_head_segments(position_xy, offset)
            rec.log(
                "/screen2/position_xy/head",
                rr.LineStrips2D(
                    head_strips,
                    colors=[rgba(POSITION_HEAD_BLUE, 255) for _ in head_strips],
                    radii=POSITION_TRAJECTORY_RADIUS,
                    draw_order=20.0,
                ),
            )
            rec.log(
                "/screen2/position_xy/current",
                rr.Points2D(
                    [position_xy[offset]],
                    colors=[rgba(POSITION_HEAD_BLUE, 255)],
                    radii=POSITION_CURRENT_RADIUS,
                    draw_order=30.0,
                ),
            )
            rec.log("/screen2/delta_logo", logo_frames[offset % len(logo_frames)])

    return {
        "recording": str(recording_path),
        "recording_id": RECORDING_ID,
        "frame_timeline": {
            "name": "frame",
            "first": int(frames[0]),
            "last": int(frames[-1]),
            "count": len(frames),
        },
        "entities": [
            "/screen1/cam0",
            "/screen2/charts/velocity_xyz/vx",
            "/screen2/charts/velocity_xyz/vy",
            "/screen2/charts/velocity_xyz/vz",
            "/screen2/charts/angular_velocity_xyz/wx",
            "/screen2/charts/angular_velocity_xyz/wy",
            "/screen2/charts/angular_velocity_xyz/wz",
            "/screen2/position_xy/grid",
            "/screen2/position_xy/frame",
            "/screen2/position_xy/axes",
            "/screen2/position_xy/trajectory",
            "/screen2/position_xy/head",
            "/screen2/position_xy/origin",
            "/screen2/position_xy/current",
            "/screen2/right/dhoc_bottom_view",
            "/screen2/right/deck_side",
            "/screen2/delta_logo",
        ],
        "dynamic_rows": {
            "cam0": len(frames),
            "velocity_xyz": len(frames),
            "omega_xyz": len(frames),
            "position_xy": len(frames),
            "delta_logo": len(frames),
        },
        "static_rows": {
            "screen2_right_images": 2,
            "position_grid_frame_axes_origin": 4,
        },
        "logo_video": {
            "source": str(logo_video_path),
            "source_frame_count": logo_source_frame_count,
            "sampled_frame_count": len(logo_frames),
            "logged_frame_count": len(frames),
            "rendering": "MP4 decoded at generation time into a Rerun Image sequence on /screen2/delta_logo",
        },
        "screen2_right_images": {
            "dhoc_bottom_view": str(dhoc_bottom_view_path),
            "deck_side": str(deck_side_path),
            "rendering": "Static Rerun Image entities composited against black backgrounds.",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("h5", type=Path, nargs="?", default=DEFAULT_H5)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--summary-out", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--logo-video", type=Path, default=DEFAULT_LOGO_VIDEO)
    parser.add_argument("--dhoc-bottom-view", type=Path, default=DEFAULT_DHOC_BOTTOM_VIEW)
    parser.add_argument("--deck-side", type=Path, default=DEFAULT_DECK_SIDE)
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS)
    parser.add_argument("--xy-plane", choices=["xy", "xz", "yz"], default="xy")
    parser.add_argument("--open-timeout-seconds", type=float, default=8.0)
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not math.isfinite(args.fps) or args.fps <= 0:
        raise ValueError("--fps must be a positive finite number")
    for path in [args.logo_video, args.dhoc_bottom_view, args.deck_side]:
        if not path.is_file():
            raise FileNotFoundError(path)


def main() -> int:
    args = parse_args()
    validate_args(args)

    try:
        with open_h5_fail_fast(args.h5, args.open_timeout_seconds) as h5_file:
            targets = load_targets(h5_file, args.xy_plane, args.fps)
            h5_summary = build_summary(args.h5, h5_file, targets, args.xy_plane, args.fps)
            recording = log_recording(
                h5_file,
                targets,
                args.logo_video,
                args.dhoc_bottom_view,
                args.deck_side,
                args.out,
            )
    except (H5AccessError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"ERROR: cannot generate LR-193 H5 Rerun artifacts: {error}", file=__import__("sys").stderr)
        return 2

    blueprints = write_blueprints(
        args.out,
        targets.frames,
        targets.xy.astype(np.float32),
        targets.vxyz,
        targets.omega_xyz,
        args.fps,
    )
    summary = {
        "contract": "LR-193-h5-native-rerun-v1",
        "app_id": APP_ID,
        "source_h5": str(args.h5),
        "fps": args.fps,
        "xy_plane": args.xy_plane,
        "recording": recording,
        "blueprints": blueprints,
        "h5_target_data": h5_summary,
        "viewer_contract": {
            "screen1": {
                "layout": blueprints["screen1_layout"],
                "required_dynamic_entity": "/screen1/cam0",
            },
            "screen2": {
                "layout": blueprints["screen2_layout"],
                "required_entities": [
                    "/screen2/position_xy/trajectory",
                    "/screen2/position_xy/head",
                    "/screen2/position_xy/current",
                    "/screen2/charts/velocity_xyz/{vx,vy,vz}",
                    "/screen2/charts/angular_velocity_xyz/{wx,wy,wz}",
                    "/screen2/delta_logo",
                    "/screen2/right/dhoc_bottom_view",
                    "/screen2/right/deck_side",
                ],
            },
            "timeline": "Both generated .rbl files set TimePanel timeline='frame', fps=30, PlayState.Playing, and LoopMode.All.",
        },
    }
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
