#!/usr/bin/env python3
"""Generate the LR-193 Rerun recording and two screen layouts."""

from __future__ import annotations

import argparse
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np


APP_ID = "dohc_lr193_rerun_dual_screen"
SCREEN1_LAYOUT = "screen1.rbl"
SCREEN2_LAYOUT = "screen2.rbl"
RECORDING = "default_lr193.rrd"
DEFAULT_DELTA_LOGO = Path("dohc-two-monitor-demo/assets/delta-icon.png")


@dataclass(frozen=True)
class PoseSample:
    frame: int
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    euler: tuple[float, float, float]
    confidence: int | None


def frame_number(name: str) -> int | None:
    if not name.startswith("frame_"):
        return None
    try:
        return int(name.removeprefix("frame_"))
    except ValueError:
        return None


def sorted_frame_numbers(h5: h5py.File) -> list[int]:
    frames = [frame_number(name) for name in h5.keys()]
    return sorted(frame for frame in frames if frame is not None)


def parse_pose(frame: int, raw: Any) -> PoseSample | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(str(raw))

    position = tuple(float(v) for v in data.get("position", []))
    velocity = tuple(float(v) for v in data.get("velocity", []))
    euler = tuple(float(v) for v in data.get("euler", []))
    if len(position) != 3 or len(velocity) != 3 or len(euler) != 3:
        return None

    confidence = data.get("confidence")
    return PoseSample(
        frame=frame,
        position=position,
        velocity=velocity,
        euler=euler,
        confidence=int(confidence) if confidence is not None else None,
    )


def collect_pose_samples(h5: h5py.File) -> list[PoseSample]:
    samples: list[PoseSample] = []
    for frame in sorted_frame_numbers(h5):
        group = h5.get(f"frame_{frame}/t265")
        if group is None:
            continue
        sample = parse_pose(frame, group.attrs.get("pose"))
        if sample is not None:
            samples.append(sample)
    return samples


def angular_velocity(samples: list[PoseSample]) -> np.ndarray:
    if not samples:
        return np.empty((0, 3), dtype=np.float64)
    if len(samples) == 1:
        return np.zeros((1, 3), dtype=np.float64)

    frames = np.asarray([sample.frame for sample in samples], dtype=np.float64)
    eulers = np.asarray([sample.euler for sample in samples], dtype=np.float64)
    return np.gradient(eulers, frames, axis=0)


def decimation_stride(length: int, max_points: int) -> int:
    if max_points <= 0:
        raise ValueError("max_points must be positive")
    return max(1, math.ceil(length / max_points))


def encoded_image_bytes(h5: h5py.File, frame: int, dataset_path: str) -> bytes | None:
    dataset = h5.get(f"frame_{frame}/{dataset_path}")
    if dataset is None or not isinstance(dataset, h5py.Dataset):
        return None
    if dataset.shape == (1,):
        return None
    return np.asarray(dataset[()]).astype(np.uint8, copy=False).tobytes()


def first_valid_image(h5: h5py.File, dataset_path: str, preferred_frame: int) -> tuple[int, bytes]:
    preferred = encoded_image_bytes(h5, preferred_frame, dataset_path)
    if preferred is not None:
        return preferred_frame, preferred

    for frame in sorted_frame_numbers(h5):
        blob = encoded_image_bytes(h5, frame, dataset_path)
        if blob is not None:
            return frame, blob

    raise ValueError(f"No valid image data found for {dataset_path}")


def static_chart_payload(samples: list[PoseSample], angular: np.ndarray, max_points: int = 420) -> dict[str, Any]:
    stride = decimation_stride(len(samples), max_points) if samples else 1
    selected = list(range(0, len(samples), stride))
    if samples and selected[-1] != len(samples) - 1:
        selected.append(len(samples) - 1)

    def rounded(values: tuple[float, ...] | list[float] | np.ndarray) -> list[float]:
        return [round(float(value), 7) for value in values]

    return {
        "frame": [samples[index].frame for index in selected],
        "velocity_xyz": [rounded(samples[index].velocity) for index in selected],
        "angular_velocity_xyz": [rounded(angular[index]) for index in selected],
        "position_xy": [rounded(samples[index].position[:2]) for index in selected],
    }


def write_static_assets(
    h5_path: Path,
    icon_path: Path,
    out_dir: Path,
    representative_frame: int,
) -> dict[str, Any]:
    static_dir = out_dir / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    with h5py.File(h5_path, "r") as h5:
        image_outputs = {
            "cam0": ("cam0.jpg", "cam0"),
            "cam1": ("cam1.jpg", "cam1"),
            "t265": ("t265.jpg", "t265/left"),
        }
        image_frames: dict[str, int] = {}
        for name, (filename, dataset_path) in image_outputs.items():
            frame, blob = first_valid_image(h5, dataset_path, representative_frame)
            (static_dir / filename).write_bytes(blob)
            image_frames[name] = frame

        samples = collect_pose_samples(h5)
        angular = angular_velocity(samples)

    telemetry_path = static_dir / "telemetry.json"
    telemetry = static_chart_payload(samples, angular)
    telemetry_path.write_text(json.dumps(telemetry, separators=(",", ":")), encoding="utf-8")
    shutil.copy2(icon_path, static_dir / "delta-icon.png")

    return {
        "dir": str(static_dir),
        "images": {
            "cam0": "static/cam0.jpg",
            "cam1": "static/cam1.jpg",
            "t265": "static/t265.jpg",
            "delta_icon": "static/delta-icon.png",
        },
        "telemetry": "static/telemetry.json",
        "representative_frames": image_frames,
        "telemetry_points": len(telemetry["frame"]),
    }


def write_recording(
    h5_path: Path,
    icon_path: Path,
    out_dir: Path,
    representative_frame: int,
) -> dict[str, Any]:
    import rerun as rr

    out_dir.mkdir(parents=True, exist_ok=True)
    recording_path = out_dir / RECORDING

    with h5py.File(h5_path, "r") as h5, rr.RecordingStream(APP_ID) as rec:
        rec.save(recording_path)

        cam0_frame, cam0 = first_valid_image(h5, "cam0", representative_frame)
        cam1_frame, cam1 = first_valid_image(h5, "cam1", representative_frame)
        t265_frame, t265 = first_valid_image(h5, "t265/left", representative_frame)

        rec.log("/screen1/cam0", rr.EncodedImage(contents=cam0, media_type="image/jpeg"), static=True)
        rec.log("/screen1/cam1", rr.EncodedImage(contents=cam1, media_type="image/jpeg"), static=True)
        rec.log("/screen2/t265", rr.EncodedImage(contents=t265, media_type="image/jpeg"), static=True)
        rec.log(
            "/screen2/delta_logo",
            rr.EncodedImage(path=icon_path, media_type="image/png", magnification_filter="linear"),
            static=True,
        )

        samples = collect_pose_samples(h5)
        angular = angular_velocity(samples)

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

        for sample, omega in zip(samples, angular, strict=True):
            rec.set_time("frame", sequence=sample.frame)
            rec.log("/screen2/charts/velocity_xyz", rr.Scalars(sample.velocity))
            rec.log("/screen2/charts/angular_velocity_xyz", rr.Scalars(omega.tolist()))
            rec.log("/screen2/charts/position_xy", rr.Scalars(sample.position[:2]))

    return {
        "recording": str(recording_path),
        "representative_frame": representative_frame,
        "image_frames": {
            "cam0": cam0_frame,
            "cam1": cam1_frame,
            "t265": t265_frame,
        },
        "pose_sample_count": len(samples),
        "angular_velocity": "derived_from_euler_finite_difference_rad_per_frame",
    }


def make_screen1_blueprint(path: Path) -> None:
    import rerun.blueprint as rrb

    rrb.Blueprint(
        rrb.Horizontal(
            rrb.Spatial2DView(name="Cam0", origin="/screen1/cam0", background=[8, 10, 12]),
            rrb.Spatial2DView(name="Cam1", origin="/screen1/cam1", background=[8, 10, 12]),
            column_shares=[1, 1],
        ),
        rrb.TimePanel(state="hidden"),
        rrb.SelectionPanel(state="collapsed"),
        rrb.BlueprintPanel(state="collapsed"),
        collapse_panels=True,
    ).save(APP_ID, path)


def make_screen2_blueprint(path: Path) -> None:
    import rerun.blueprint as rrb

    rrb.Blueprint(
        rrb.Horizontal(
            rrb.Vertical(
                rrb.TimeSeriesView(
                    name="Velocity XYZ",
                    origin="/screen2/charts/velocity_xyz",
                    plot_legend=rrb.PlotLegend(visible=True),
                    axis_y=rrb.ScalarAxis(range=(-0.01, 0.04)),
                ),
                rrb.TimeSeriesView(
                    name="Angular velocity XYZ",
                    origin="/screen2/charts/angular_velocity_xyz",
                    plot_legend=rrb.PlotLegend(visible=True),
                    axis_y=rrb.ScalarAxis(range=(-0.0001, 0.0001)),
                ),
                rrb.TimeSeriesView(
                    name="Position XY",
                    origin="/screen2/charts/position_xy",
                    plot_legend=rrb.PlotLegend(visible=True),
                    axis_y=rrb.ScalarAxis(range=(-0.001, 0.002)),
                ),
                row_shares=[1, 1, 1],
            ),
            rrb.Spatial2DView(name="", origin="/screen2/delta_logo", background=[244, 244, 240]),
            rrb.Spatial2DView(name="T265", origin="/screen2/t265", background=[8, 10, 12]),
            column_shares=[1, 1, 1],
        ),
        rrb.TimePanel(state="hidden"),
        rrb.SelectionPanel(state="collapsed"),
        rrb.BlueprintPanel(state="collapsed"),
        collapse_panels=True,
    ).save(APP_ID, path)


def write_blueprints(out_dir: Path) -> dict[str, str]:
    layouts = out_dir / "layouts"
    blueprints = out_dir / "blueprints"
    layouts.mkdir(parents=True, exist_ok=True)
    blueprints.mkdir(parents=True, exist_ok=True)

    screen1 = layouts / SCREEN1_LAYOUT
    screen2 = layouts / SCREEN2_LAYOUT
    make_screen1_blueprint(screen1)
    make_screen2_blueprint(screen2)

    seed1 = blueprints / SCREEN1_LAYOUT
    seed2 = blueprints / SCREEN2_LAYOUT
    make_screen1_blueprint(seed1)
    make_screen2_blueprint(seed2)

    return {
        "screen1_layout": str(screen1),
        "screen2_layout": str(screen2),
        "screen1_seed": str(seed1),
        "screen2_seed": str(seed2),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("h5", type=Path, nargs="?", default=Path("/Users/w/default.h5"))
    parser.add_argument("--icon", type=Path, default=DEFAULT_DELTA_LOGO)
    parser.add_argument("--out", type=Path, default=Path("dohc-two-monitor-demo/out"))
    parser.add_argument("--representative-frame", type=int, default=162)
    parser.add_argument("--summary-out", type=Path, default=Path("dohc-two-monitor-demo/out/lr193_dual_screen_summary.json"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.h5.is_file():
        raise FileNotFoundError(args.h5)
    if not args.icon.is_file():
        raise FileNotFoundError(args.icon)
    if not math.isfinite(args.representative_frame):
        raise ValueError("representative frame must be finite")

    recording = write_recording(args.h5, args.icon, args.out, args.representative_frame)
    blueprints = write_blueprints(args.out)
    static_assets = write_static_assets(args.h5, args.icon, args.out, args.representative_frame)

    summary = {
        "contract": "LR-191-H5-v1",
        "app_id": APP_ID,
        "source": str(args.h5),
        "screen1": {
            "layout": "two large Rerun Spatial2D camera views, Cam0 left and Cam1 right",
            "entities": ["/screen1/cam0", "/screen1/cam1"],
            "time_panel": "hidden",
        },
        "screen2": {
            "layout": "left third has three Rerun TimeSeriesView charts, center third has Delta logo, right third has T265 image",
            "charts": [
                "/screen2/charts/velocity_xyz",
                "/screen2/charts/angular_velocity_xyz",
                "/screen2/charts/position_xy",
            ],
            "delta_logo_entity": "/screen2/delta_logo",
            "t265_entity": "/screen2/t265",
            "time_panel": "hidden",
        },
        "recording": recording,
        "blueprints": blueprints,
        "static_assets": static_assets,
    }

    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
