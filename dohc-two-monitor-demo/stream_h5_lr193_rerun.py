#!/usr/bin/env python3
"""Stream LR-193 H5 target data to a live Rerun gRPC data service."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import threading
import time
from pathlib import Path
from typing import Any

import generate_fake_lr193_rerun as fake
import h5_target_data as h5_contract
import numpy as np
from PIL import Image

APP_ID = fake.APP_ID
RECORDING_ID = "lr193_live_h5_v1"
DEFAULT_LAYOUT_OUT = fake.DEFAULT_OUT / "live" / "layouts"
DEFAULT_GRPC_PORT = 9876
DEFAULT_SERVER_MEMORY_LIMIT = "2GiB"
DEFAULT_CHART_WINDOW_FRAMES = 180
SCREEN2_WIDTH = 1280
SCREEN2_HEIGHT = 720
VELOCITY_BOX = (12, 12, 430, 224)
OMEGA_BOX = (12, 236, 430, 448)
POSITION_BOX = (12, 460, 430, 708)
LOGO_POS = (500, 88)
LOGO_SIZE = (300, 510)
RIGHT_IMAGE_SIZE = (384, 216)
RIGHT_TOP_POS = (875, 36)
RIGHT_BOTTOM_POS = (875, 384)


class LiveStatus:
    def __init__(self, fps: float) -> None:
        self._lock = threading.Lock()
        self._payload: dict[str, Any] = {
            "status": "starting",
            "timeline": "frame",
            "frame": None,
            "source_frame": None,
            "sample_index": None,
            "fps": fps,
            "updated_at_unix": time.time(),
        }

    def update(
        self,
        status: str,
        frame: int | None = None,
        source_frame: int | None = None,
        sample_index: int | None = None,
    ) -> None:
        with self._lock:
            self._payload.update(
                {
                    "status": status,
                    "frame": frame,
                    "source_frame": source_frame,
                    "sample_index": sample_index,
                    "updated_at_unix": time.time(),
                }
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._payload)


def start_status_server(status: LiveStatus, port: int) -> ThreadingHTTPServer:
    class StatusHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in {"/", "/status"}:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(status.snapshot(), sort_keys=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), StatusHandler)
    thread = threading.Thread(target=server.serve_forever, name="lr237-status-http", daemon=True)
    thread.start()
    return server


def rgba(color: list[int], alpha: int) -> list[int]:
    return [color[0], color[1], color[2], alpha]


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


def current_time_range():
    import rerun.blueprint as rrb

    return rrb.VisibleTimeRange(
        "frame",
        start=rrb.TimeRangeBoundary.cursor_relative(seq=0),
        end=rrb.TimeRangeBoundary.cursor_relative(seq=0),
    )


def chart_time_range(history_frames: int):
    import rerun.blueprint as rrb

    return rrb.VisibleTimeRange(
        "frame",
        start=rrb.TimeRangeBoundary.cursor_relative(seq=-history_frames),
        end=rrb.TimeRangeBoundary.cursor_relative(seq=0),
    )


def chart_time_axis(history_frames: int):
    import rerun.blueprint as rrb

    return rrb.TimeAxis(
        view_range=rrb.TimeRange(
            start=rrb.TimeRangeBoundary.cursor_relative(seq=-history_frames),
            end=rrb.TimeRangeBoundary.cursor_relative(seq=0),
        ),
        zoom_lock=True,
    )


def frame_text(frame: int, source_frame: int) -> str:
    return f"timeline: frame\nframe: {frame}\nsource_frame: {source_frame}"


def frame_label(frame: int, source_frame: int) -> str:
    return f"frame {frame} | source {source_frame} | timeline frame"


def fit_rgb_image(image: Image.Image, size: tuple[int, int], background: tuple[int, int, int]) -> np.ndarray:
    fitted = image.convert("RGB")
    fitted.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, background)
    x = (size[0] - fitted.width) // 2
    y = (size[1] - fitted.height) // 2
    canvas.paste(fitted, (x, y))
    return np.asarray(canvas)


def image_at_path(image_path: Path, rr: Any, size: tuple[int, int], background: tuple[int, int, int]) -> Any:
    with Image.open(image_path) as image:
        if image.mode == "RGBA":
            base = Image.new("RGBA", image.size, (*background, 255))
            image = Image.alpha_composite(base, image.convert("RGBA"))
        return rr.Image(fit_rgb_image(image, size, background)).compress(jpeg_quality=90)


def logo_frames_at_size(logo_video_path: Path, rr: Any, size: tuple[int, int]) -> tuple[list[Any], int]:
    import av

    frames: list[np.ndarray] = []
    source_frame_count = 0
    with av.open(str(logo_video_path)) as container:
        video_stream = container.streams.video[0]
        for frame in container.decode(video_stream):
            source_frame_count += 1
            frames.append(fit_rgb_image(Image.fromarray(frame.to_ndarray(format="rgb24")), size, (255, 255, 255)))
    if not frames:
        raise ValueError(f"Logo video has no readable frames: {logo_video_path}")
    if len(frames) > fake.LOGO_VIDEO_MAX_LOGGED_FRAMES:
        sample_indexes = np.linspace(0, len(frames) - 1, fake.LOGO_VIDEO_MAX_LOGGED_FRAMES).round().astype(int)
        frames = [frames[index] for index in sample_indexes]
    return [rr.Image(frame).compress(jpeg_quality=82) for frame in frames], source_frame_count


def plot_area(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    return (left + 34, top + 28, right - 12, bottom - 18)


def map_value(value: float, src: tuple[float, float], dst: tuple[int, int]) -> float:
    if src[1] == src[0]:
        return float(dst[0])
    fraction = (value - src[0]) / (src[1] - src[0])
    return float(dst[0] + fraction * (dst[1] - dst[0]))


def chart_grid_lines(box: tuple[int, int, int, int]) -> list[list[list[float]]]:
    left, top, right, bottom = box
    plot = plot_area(box)
    lines = [
        [[left, top], [right, top], [right, bottom], [left, bottom], [left, top]],
    ]
    for step in range(5):
        y = float(plot[1] + (plot[3] - plot[1]) * step / 4)
        lines.append([[plot[0], y], [plot[2], y]])
    for step in range(5):
        x = float(plot[0] + (plot[2] - plot[0]) * step / 4)
        lines.append([[x, plot[1]], [x, plot[3]]])
    return lines


def position_box_lines() -> list[list[list[float]]]:
    return chart_grid_lines(POSITION_BOX)


def write_live_blueprints(
    layout_out_dir: Path,
    targets: h5_contract.LoadedTargets,
    fps: float,
    chart_window_frames: int,
) -> dict[str, str]:
    import rerun.blueprint as rrb

    layout_out_dir.mkdir(parents=True, exist_ok=True)
    screen1_path = layout_out_dir / fake.SCREEN1_LAYOUT
    screen2_path = layout_out_dir / fake.SCREEN2_LAYOUT

    current_range = current_time_range()
    chart_range = chart_time_range(chart_window_frames)
    time_axis = chart_time_axis(chart_window_frames)
    camera_bounds = rrb.VisualBounds2D(x_range=[0, 1280], y_range=[0, 720])
    velocity_range = nice_symmetric_range(targets.vxyz, 0.1)
    omega_range = nice_symmetric_range(targets.omega_xyz, 0.1)
    logo_bounds = rrb.VisualBounds2D(x_range=[0, LOGO_SIZE[0]], y_range=[0, LOGO_SIZE[1]])
    right_image_bounds = rrb.VisualBounds2D(x_range=[0, RIGHT_IMAGE_SIZE[0]], y_range=[0, RIGHT_IMAGE_SIZE[1]])
    position_x_range, position_y_range = fake.position_xy_visual_bounds(targets.xy.astype(np.float32))
    position_bounds = rrb.VisualBounds2D(x_range=position_x_range, y_range=position_y_range)
    plot_background = rrb.archetypes.PlotBackground(color=fake.LIGHT_BACKGROUND, show_grid=True)

    rrb.Blueprint(
        rrb.Spatial2DView(
            name="Cam0",
            origin="/screen1",
            contents=["/screen1/cam0", "/screen1/frame_marker"],
            background=[8, 10, 12],
            visual_bounds=camera_bounds,
            time_ranges=current_range,
        ),
        rrb.TimePanel(state="hidden", timeline="frame", play_state="following", fps=fps),
        rrb.SelectionPanel(state="collapsed"),
        rrb.BlueprintPanel(state="collapsed"),
        collapse_panels=True,
    ).save(APP_ID, screen1_path)

    rrb.Blueprint(
        rrb.Horizontal(
            rrb.Vertical(
                rrb.TimeSeriesView(
                    name="Velocity XYZ",
                    origin="/screen2/charts/velocity_xyz",
                    time_ranges=chart_range,
                    axis_x=time_axis,
                    plot_legend=rrb.PlotLegend(visible=True),
                    axis_y=rrb.ScalarAxis(range=velocity_range),
                    background=plot_background,
                ),
                rrb.TimeSeriesView(
                    name="Angular velocity XYZ",
                    origin="/screen2/charts/angular_velocity_xyz",
                    time_ranges=chart_range,
                    axis_x=time_axis,
                    plot_legend=rrb.PlotLegend(visible=True),
                    axis_y=rrb.ScalarAxis(range=omega_range),
                    background=plot_background,
                ),
                rrb.Spatial2DView(
                    name="Position XY",
                    origin="/screen2/position_xy",
                    contents=["/screen2/position_xy/**"],
                    background=fake.LIGHT_BACKGROUND,
                    visual_bounds=position_bounds,
                    time_ranges=current_range,
                ),
                row_shares=[1, 1, 1],
            ),
            rrb.Spatial2DView(
                name="Delta Logo",
                origin="/screen2/delta_logo",
                contents=["/screen2/delta_logo"],
                background=fake.LIGHT_BACKGROUND,
                visual_bounds=logo_bounds,
                time_ranges=current_range,
            ),
            rrb.Vertical(
                rrb.Spatial2DView(
                    name="dhoc top-down",
                    origin="/screen2/right/dhoc_bottom_view",
                    contents=["/screen2/right/dhoc_bottom_view"],
                    background=fake.BLACK_BACKGROUND,
                    visual_bounds=right_image_bounds,
                    time_ranges=current_range,
                ),
                rrb.Spatial2DView(
                    name="deck side",
                    origin="/screen2/right/deck_side",
                    contents=["/screen2/right/deck_side"],
                    background=fake.BLACK_BACKGROUND,
                    visual_bounds=right_image_bounds,
                    time_ranges=current_range,
                ),
                row_shares=[1, 1],
            ),
            column_shares=[1.2, 0.8, 1],
        ),
        rrb.TimePanel(state="hidden", timeline="frame", play_state="following", fps=fps),
        rrb.SelectionPanel(state="collapsed"),
        rrb.BlueprintPanel(state="collapsed"),
        collapse_panels=True,
    ).save(APP_ID, screen2_path)

    return {
        "screen1_layout": str(screen1_path),
        "screen2_layout": str(screen2_path),
        "play_state": "following",
        "chart_window_frames": str(chart_window_frames),
        "velocity_axis_range": str(velocity_range),
        "omega_axis_range": str(omega_range),
        "position_visual_bounds": str({"x_range": position_x_range, "y_range": position_y_range}),
        "screen2_rendering": "native TimeSeriesView and Spatial2DView panes for charts, XY, logo, and right-side images",
    }

def load_cam0_jpegs(h5_file: Any, targets: h5_contract.LoadedTargets) -> list[bytes]:
    jpegs: list[bytes] = []
    for frame_name in targets.frame_names:
        jpegs.append(bytes(h5_file[frame_name]["cam0"][()]))
    return jpegs


def log_label(rec: Any, rr: Any, path: str, xy: tuple[float, float], label: str, color: list[int]) -> None:
    rec.log(
        path,
        rr.Points2D([xy], labels=[label], show_labels=True, colors=[rgba(color, 255)], radii=0.01, draw_order=100.0),
        static=True,
    )


def log_chart_grid(rec: Any, rr: Any, path: str, box: tuple[int, int, int, int], title: str) -> None:
    rec.log(
        f"{path}/grid",
        rr.LineStrips2D(
            chart_grid_lines(box),
            colors=[rgba(fake.POSITION_GRID_COLOR, 160) for _ in chart_grid_lines(box)],
            radii=1.0,
            draw_order=0.0,
        ),
        static=True,
    )
    log_label(rec, rr, f"{path}/title", (box[0] + 12, box[1] + 12), title, fake.POSITION_AXIS_COLOR)


def position_xy_static_entities(rec: Any, rr: Any, position_xy: np.ndarray) -> None:
    x_range, y_range = fake.position_xy_visual_bounds(position_xy)
    grid_lines = fake.position_xy_grid_lines(x_range, y_range)
    frame_lines = fake.position_xy_frame_lines(x_range, y_range)
    axes_lines = [
        [[x_range[0], 0.0], [x_range[1], 0.0]],
        [[0.0, y_range[0]], [0.0, y_range[1]]],
    ]
    rec.log(
        "/screen2/position_xy/grid",
        rr.LineStrips2D(
            grid_lines,
            colors=[rgba(fake.POSITION_GRID_COLOR, 128) for _ in grid_lines],
            radii=fake.POSITION_GRID_RADIUS,
            draw_order=0.0,
        ),
        static=True,
    )
    rec.log(
        "/screen2/position_xy/frame",
        rr.LineStrips2D(
            frame_lines,
            colors=[rgba(fake.POSITION_GRID_COLOR, 176)],
            radii=fake.POSITION_GRID_RADIUS,
            draw_order=1.0,
        ),
        static=True,
    )
    rec.log(
        "/screen2/position_xy/axes",
        rr.LineStrips2D(
            axes_lines,
            colors=[rgba(fake.POSITION_AXIS_COLOR, 160), rgba(fake.POSITION_AXIS_COLOR, 160)],
            radii=fake.POSITION_AXIS_RADIUS,
            draw_order=2.0,
        ),
        static=True,
    )
    rec.log(
        "/screen2/position_xy/origin",
        rr.Points2D(
            [[0.0, 0.0]],
            colors=[rgba(fake.POSITION_HEAD_BLUE, 220)],
            radii=0.035,
            draw_order=20.0,
        ),
        static=True,
    )
    label_x = x_range[0] + (x_range[1] - x_range[0]) * 0.02
    label_y = y_range[1] - (y_range[1] - y_range[0]) * 0.06
    log_label(rec, rr, "/screen2/position_xy/title", (label_x, label_y), "Position XY", fake.POSITION_AXIS_COLOR)


def log_chart_series_styles(rec: Any, rr: Any) -> None:
    for path, (name, color) in {
        "/screen2/charts/velocity_xyz/vx": ("vx", fake.TELEMETRY_BLUE),
        "/screen2/charts/velocity_xyz/vy": ("vy", fake.TELEMETRY_GREEN),
        "/screen2/charts/velocity_xyz/vz": ("vz", fake.TELEMETRY_YELLOW),
        "/screen2/charts/angular_velocity_xyz/wx": ("wx", fake.TELEMETRY_BLUE),
        "/screen2/charts/angular_velocity_xyz/wy": ("wy", fake.TELEMETRY_GREEN),
        "/screen2/charts/angular_velocity_xyz/wz": ("wz", fake.TELEMETRY_YELLOW),
    }.items():
        rec.log(path, rr.SeriesLines(names=name, colors=color, widths=2.0), static=True)


def log_static_entities(
    rec: Any,
    rr: Any,
    targets: h5_contract.LoadedTargets,
) -> None:
    log_chart_series_styles(rec, rr)
    position_xy_static_entities(rec, rr, targets.xy.astype(np.float32))


def map_position_xy(position_xy: np.ndarray, xy: np.ndarray) -> list[float]:
    x_range, y_range = fake.position_xy_visual_bounds(position_xy)
    plot = plot_area(POSITION_BOX)
    return [
        map_value(float(xy[0]), tuple(x_range), (plot[0], plot[2])),
        map_value(float(xy[1]), tuple(y_range), (plot[3], plot[1])),
    ]


def log_position_xy_frame(rec: Any, rr: Any, position_xy: np.ndarray, sample_index: int) -> None:
    trajectory_strips, trajectory_colors = fake.position_xy_faded_segments(position_xy, sample_index)
    if trajectory_strips:
        rec.log(
            "/screen2/position_xy/trajectory",
            rr.LineStrips2D(
                trajectory_strips,
                colors=trajectory_colors,
                radii=fake.POSITION_TRAJECTORY_RADIUS,
                draw_order=10.0,
            ),
        )
    head_strips = fake.position_xy_head_segments(position_xy, sample_index)
    if head_strips:
        rec.log(
            "/screen2/position_xy/head",
            rr.LineStrips2D(
                head_strips,
                colors=[rgba(fake.POSITION_HEAD_BLUE, 255) for _ in head_strips],
                radii=fake.POSITION_TRAJECTORY_RADIUS,
                draw_order=20.0,
            ),
        )
    rec.log(
        "/screen2/position_xy/current",
        rr.Points2D(
            [position_xy[sample_index]],
            colors=[rgba(fake.POSITION_HEAD_BLUE, 255)],
            radii=fake.POSITION_CURRENT_RADIUS,
            draw_order=30.0,
        ),
    )


def log_chart_frame(
    rec: Any,
    rr: Any,
    prefix: str,
    values: np.ndarray,
    sample_index: int,
    history_frames: int,
    y_range: tuple[float, float],
    box: tuple[int, int, int, int],
    axis_names: list[str],
) -> None:
    del history_frames, y_range, box
    for axis, axis_name in enumerate(axis_names):
        rec.log(f"{prefix}/{axis_name}", rr.Scalars(float(values[sample_index, axis])))


def build_cors_allow_origin(args: argparse.Namespace) -> list[str] | None:
    origins = list(args.cors_allow_origin or [])
    if args.url_host not in {"127.0.0.1", "localhost"}:
        origins.append(f"http://{args.url_host}:*")
    return origins or None


def source_url(host: str, grpc_port: int) -> str:
    return f"rerun+http://{host}:{grpc_port}/proxy"


def build_summary(
    args: argparse.Namespace,
    targets: h5_contract.LoadedTargets,
    layouts: dict[str, str],
    local_source_url: str,
    external_source_url: str,
    logo_source_frame_count: int,
    logo_sampled_frame_count: int,
    cors_allow_origin: list[str] | None,
) -> dict[str, Any]:
    return {
        "contract": "LR-193-h5-live-rerun-v1",
        "transport": "live Rerun gRPC data service, not file-URL RRD playback",
        "source_h5": str(args.h5),
        "fps": args.fps,
        "xy_plane": args.xy_plane,
        "grpc": {
            "port": args.grpc_port,
            "local_source_url": local_source_url,
            "browser_source_url": external_source_url,
            "server_memory_limit": args.server_memory_limit,
            "newest_first": args.newest_first,
            "cors_allow_origin": cors_allow_origin,
            "status_url": f"http://127.0.0.1:{args.status_http_port}/status" if args.status_http_port else None,
        },
        "frames": {
            "source_frame_count": targets.source_frame_count,
            "used_frame_count": len(targets.frames),
            "source_first_frame": int(targets.frames[0]),
            "source_last_frame": int(targets.frames[-1]),
            "playback_timeline": "monotonic shared frame sequence; loops keep increasing the Rerun frame value",
        },
        "layouts": layouts,
        "entity_paths": {
            "cam0": h5_contract.TARGET_ENTITY_PATHS["cam0"],
            "XY": h5_contract.TARGET_ENTITY_PATHS["XY"],
            "VXYZ": h5_contract.TARGET_ENTITY_PATHS["VXYZ"],
            "OmegaXYZ": h5_contract.TARGET_ENTITY_PATHS["OmegaXYZ"],
            "logo": ["/screen2/delta_logo"],
            "right_images": ["/screen2/right/dhoc_bottom_view", "/screen2/right/deck_side"],
            "frame_markers": ["/screen1/frame_marker", "/screen2/frame_marker"],
        },
        "logo": {
            "source": str(args.logo_video),
            "rendering": "MP4 decoded to a dynamic Rerun Image sequence on /screen2/delta_logo",
            "source_frame_count": logo_source_frame_count,
            "sampled_frame_count": logo_sampled_frame_count,
        },
        "right_images": {
            "dhoc_bottom_view": str(args.dhoc_bottom_view),
            "deck_side": str(args.deck_side),
            "rendering": "same native Rerun Image archetypes repeated on the shared frame timeline so they remain visually static",
        },
        "omega_xyz": {
            "source": targets.omega_source,
            "count": len(targets.omega_xyz) if targets.omega_xyz is not None else 0,
        },
    }


def stream_targets(args: argparse.Namespace) -> int:
    import rerun as rr

    if not math.isfinite(args.fps) or args.fps <= 0:
        raise ValueError("--fps must be a positive finite number")
    if not math.isfinite(args.startup_delay_seconds) or args.startup_delay_seconds < 0:
        raise ValueError("--startup-delay-seconds must be a non-negative finite number")

    with h5_contract.open_h5_fail_fast(args.h5, args.open_timeout_seconds) as h5_file:
        targets = h5_contract.load_targets(h5_file, args.xy_plane, args.fps, args.max_frames)
        if targets.omega_xyz is None:
            raise ValueError("OmegaXYZ is unavailable: no complete quaternion/euler source was found")
        cam0_jpegs = load_cam0_jpegs(h5_file, targets)

    layouts = write_live_blueprints(args.layout_out_dir, targets, args.fps, args.chart_window_frames)
    logo_frames, logo_source_frame_count = logo_frames_at_size(args.logo_video, rr, LOGO_SIZE)
    dhoc_bottom_view = image_at_path(args.dhoc_bottom_view, rr, RIGHT_IMAGE_SIZE, (0, 0, 0))
    deck_side = image_at_path(args.deck_side, rr, RIGHT_IMAGE_SIZE, (0, 0, 0))
    velocity_range = nice_symmetric_range(targets.vxyz, 0.1)
    omega_range = nice_symmetric_range(targets.omega_xyz, 0.1)
    cors_allow_origin = build_cors_allow_origin(args)
    live_status = LiveStatus(args.fps)
    status_server = start_status_server(live_status, args.status_http_port) if args.status_http_port else None

    rec = rr.RecordingStream(APP_ID, recording_id=args.recording_id)
    local_source_url = rec.serve_grpc(
        grpc_port=args.grpc_port,
        server_memory_limit=args.server_memory_limit,
        newest_first=args.newest_first,
        cors_allow_origin=cors_allow_origin,
    )
    external_source_url = source_url(args.url_host, args.grpc_port)

    summary = build_summary(
        args,
        targets,
        layouts,
        local_source_url,
        external_source_url,
        logo_source_frame_count,
        len(logo_frames),
        cors_allow_origin,
    )
    if args.summary_out:
        args.summary_out.parent.mkdir(parents=True, exist_ok=True)
        args.summary_out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)

    log_static_entities(rec, rr, targets)
    if args.startup_delay_seconds > 0:
        live_status.update("waiting_before_stream")
        print(
            json.dumps({"status": "waiting_before_stream", "seconds": args.startup_delay_seconds}),
            flush=True,
        )
        time.sleep(args.startup_delay_seconds)

    position_xy = targets.xy.astype(np.float32)
    position_x_range, position_y_range = fake.position_xy_visual_bounds(position_xy)
    position_marker = [
        position_x_range[0] + (position_x_range[1] - position_x_range[0]) * 0.02,
        position_y_range[1] - (position_y_range[1] - position_y_range[0]) * 0.06,
    ]
    playback_frame = 0
    interval = 1.0 / args.fps
    next_tick = time.monotonic()

    while True:
        for sample_index, source_frame in enumerate(targets.frames):
            rec.set_time("frame", sequence=playback_frame)
            live_status.update("streaming", playback_frame, int(source_frame), sample_index)
            marker = frame_text(playback_frame, int(source_frame))
            marker_label = frame_label(playback_frame, int(source_frame))
            rec.log(
                "/screen1/frame_marker",
                rr.Points2D([[32, 32]], labels=[marker_label], show_labels=True, colors=[rgba(fake.TELEMETRY_YELLOW, 255)], radii=1.0, draw_order=200.0),
            )
            rec.log(
                "/screen2/frame_marker",
                rr.Points2D([position_marker], labels=[marker_label], show_labels=True, colors=[rgba(fake.POSITION_AXIS_COLOR, 255)], radii=0.01, draw_order=200.0),
            )
            rec.log(
                "/screen2/position_xy/frame_marker",
                rr.Points2D([position_marker], labels=[marker_label], show_labels=True, colors=[rgba(fake.POSITION_AXIS_COLOR, 255)], radii=0.01, draw_order=200.0),
            )
            rec.log("/screen1/cam0", rr.EncodedImage(contents=cam0_jpegs[sample_index], media_type="image/jpeg"))
            log_position_xy_frame(rec, rr, position_xy, sample_index)
            log_chart_frame(
                rec,
                rr,
                "/screen2/charts/velocity_xyz",
                targets.vxyz,
                sample_index,
                args.chart_window_frames,
                velocity_range,
                VELOCITY_BOX,
                ["vx", "vy", "vz"],
            )
            log_chart_frame(
                rec,
                rr,
                "/screen2/charts/angular_velocity_xyz",
                targets.omega_xyz,
                sample_index,
                args.chart_window_frames,
                omega_range,
                OMEGA_BOX,
                ["wx", "wy", "wz"],
            )
            logo_index = fake.logo_video_frame_index(playback_frame, len(logo_frames))
            rec.log("/screen2/delta_logo", logo_frames[logo_index])
            rec.log("/screen2/right/dhoc_bottom_view", dhoc_bottom_view)
            rec.log("/screen2/right/deck_side", deck_side)

            if args.status_every and playback_frame % args.status_every == 0:
                print(
                    json.dumps(
                        {
                            "status": "streaming",
                            "frame": playback_frame,
                            "source_frame": int(source_frame),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

            playback_frame += 1
            if args.max_stream_frames is not None and playback_frame >= args.max_stream_frames:
                rec.flush(timeout_sec=5.0)
                live_status.update("finished", playback_frame - 1, int(source_frame), sample_index)
                if status_server is not None:
                    status_server.shutdown()
                print(json.dumps({"status": "finished", "frames_streamed": playback_frame}), flush=True)
                return 0

            next_tick += interval
            sleep_seconds = next_tick - time.monotonic()
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)

        if not args.loop:
            rec.flush(timeout_sec=5.0)
            live_status.update("finished", playback_frame - 1, int(targets.frames[-1]), len(targets.frames) - 1)
            if status_server is not None:
                status_server.shutdown()
            print(json.dumps({"status": "finished", "frames_streamed": playback_frame}), flush=True)
            return 0


def positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def validate_paths(args: argparse.Namespace) -> None:
    for path in [args.logo_video, args.dhoc_bottom_view, args.deck_side]:
        if not path.is_file():
            raise FileNotFoundError(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("h5", type=Path, help="Readable source H5")
    parser.add_argument("--fps", type=positive_float, default=h5_contract.DEFAULT_FPS)
    parser.add_argument("--xy-plane", choices=sorted(h5_contract.XY_PLANES), default="xy")
    parser.add_argument("--open-timeout-seconds", type=positive_float, default=h5_contract.DEFAULT_OPEN_TIMEOUT_SECONDS)
    parser.add_argument("--max-frames", type=positive_int, default=None, help="Limit H5 samples loaded for quick demos")
    parser.add_argument("--grpc-port", type=int, default=DEFAULT_GRPC_PORT)
    parser.add_argument("--url-host", default="127.0.0.1", help="Host printed in browser-facing source URLs")
    parser.add_argument("--recording-id", default=RECORDING_ID)
    parser.add_argument("--server-memory-limit", default=DEFAULT_SERVER_MEMORY_LIMIT)
    parser.add_argument("--oldest-first", dest="newest_first", action="store_false")
    parser.add_argument("--cors-allow-origin", action="append", default=[])
    parser.add_argument(
        "--loop", action="store_true", help="Loop source samples while keeping frame timeline monotonic"
    )
    parser.add_argument("--max-stream-frames", type=positive_int, default=None, help="Stop after N streamed frames")
    parser.add_argument(
        "--status-every", type=int, default=30, help="Print a status row every N playback frames; 0 disables"
    )
    parser.add_argument(
        "--startup-delay-seconds",
        type=float,
        default=0.0,
        help="Wait after starting the gRPC server and logging static entities before streaming dynamic frames",
    )
    parser.add_argument("--chart-window-frames", type=positive_int, default=DEFAULT_CHART_WINDOW_FRAMES)
    parser.add_argument(
        "--status-http-port",
        type=int,
        default=None,
        help="Optional local HTTP port exposing the current shared frame timeline as JSON",
    )
    parser.add_argument("--layout-out-dir", type=Path, default=DEFAULT_LAYOUT_OUT)
    parser.add_argument("--summary-out", type=Path, default=fake.DEFAULT_OUT / "live" / "live_h5_summary.json")
    parser.add_argument("--logo-video", type=Path, default=fake.DEFAULT_LOGO_VIDEO)
    parser.add_argument("--dhoc-bottom-view", type=Path, default=fake.DEFAULT_DHOC_BOTTOM_VIEW)
    parser.add_argument("--deck-side", type=Path, default=fake.DEFAULT_DECK_SIDE)
    parser.set_defaults(newest_first=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        validate_paths(args)
        return stream_targets(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, h5_contract.H5AccessError) as error:
        payload = {
            "status": "failed",
            "source_h5": str(args.h5),
            "reason": str(error),
            "fail_fast": isinstance(error, h5_contract.H5AccessError),
        }
        print(json.dumps(payload, indent=2), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
