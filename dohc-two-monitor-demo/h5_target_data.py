#!/usr/bin/env python3
"""Inspect and extract LR-193 target data from raw H5 captures.

This is a data-boundary wrapper only. It does not generate RRD files, edit
Rerun layouts, start viewers, or change the native viewer delivery.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import signal
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
DEFAULT_FPS = 30.0
DEFAULT_OPEN_TIMEOUT_SECONDS = 8.0

XY_PLANES = {
    "xy": (0, 1),
    "xz": (0, 2),
    "yz": (1, 2),
}

TARGET_ENTITY_PATHS = {
    "cam0": ["/screen1/cam0"],
    "XY": [
        "/screen2/position_xy/trajectory",
        "/screen2/position_xy/head",
        "/screen2/position_xy/current",
    ],
    "VXYZ": [
        "/screen2/charts/velocity_xyz/vx",
        "/screen2/charts/velocity_xyz/vy",
        "/screen2/charts/velocity_xyz/vz",
    ],
    "OmegaXYZ": [
        "/screen2/charts/angular_velocity_xyz/wx",
        "/screen2/charts/angular_velocity_xyz/wy",
        "/screen2/charts/angular_velocity_xyz/wz",
    ],
}


class H5AccessError(RuntimeError):
    """Raised when a source H5 cannot be opened quickly and read-only."""


@dataclass(frozen=True)
class LoadedTargets:
    """In-memory target arrays and source metadata."""

    frames: np.ndarray
    frame_names: list[str]
    xy: np.ndarray
    vxyz: np.ndarray
    omega_xyz: np.ndarray | None
    omega_source: str | None
    euler_xyz: np.ndarray | None
    euler_omega_xyz: np.ndarray | None
    quaternion_xyzw: np.ndarray | None
    cam0_lengths: np.ndarray
    cam0_dtype_counts: Counter[str]
    cam0_shape_counts: Counter[str]
    cam0_attr_counts: Counter[str]
    cam0_chunk_counts: Counter[str]
    cam0_compression_counts: Counter[str]
    cam0_nonjpeg_frames: list[int]
    field_set_counts: Counter[tuple[str, ...]]
    pose_key_counts: Counter[tuple[str, ...]]
    pose_missing_frames: list[int]
    source_frame_count: int
    missing_frame_count: int


@contextlib.contextmanager
def operation_timeout(seconds: float, label: str) -> Iterator[None]:
    """Interrupt blocking local file reads quickly on Unix-like systems."""

    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def raise_timeout(_signum: int, _frame: object) -> None:
        raise H5AccessError(f"{label} timed out after {seconds:g}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def open_h5_fail_fast(path: Path, timeout_seconds: float) -> h5py.File:
    """Open an H5 file read-only after a bounded HDF5 magic-header probe."""

    expanded = path.expanduser()
    if not expanded.exists():
        raise H5AccessError(f"{expanded} does not exist")
    if not expanded.is_file():
        raise H5AccessError(f"{expanded} is not a regular file")

    try:
        with operation_timeout(timeout_seconds, "HDF5 magic-header read"), expanded.open("rb") as file:
            magic = file.read(len(HDF5_MAGIC))
    except H5AccessError:
        raise
    except InterruptedError as error:
        raise H5AccessError(f"HDF5 magic-header read interrupted: {error}") from error
    except OSError as error:
        raise H5AccessError(f"HDF5 magic-header read failed: {error}") from error

    if magic != HDF5_MAGIC:
        raise H5AccessError(f"{expanded} is not an HDF5 file; magic={magic.hex(' ')}")

    try:
        with operation_timeout(timeout_seconds, "h5py open"):
            return h5py.File(expanded, "r")
    except H5AccessError:
        raise
    except InterruptedError as error:
        raise H5AccessError(f"h5py open interrupted: {error}") from error
    except OSError as error:
        raise H5AccessError(f"h5py open failed: {error}") from error


def parse_frame_number(name: str) -> int | None:
    if not name.startswith("frame_"):
        return None
    try:
        return int(name.split("_", 1)[1])
    except ValueError:
        return None


def sorted_frame_items(h5_file: h5py.File) -> list[tuple[int, str]]:
    frames: list[tuple[int, str]] = []
    for name in h5_file:
        frame = parse_frame_number(name)
        if frame is not None:
            frames.append((frame, name))
    return sorted(frames)


def dataset_keys(group: h5py.Group) -> tuple[str, ...]:
    keys: list[str] = []

    def visit(name: str, obj: object) -> None:
        if isinstance(obj, h5py.Dataset):
            keys.append(name)

    group.visititems(visit)
    return tuple(sorted(keys))


def json_attr(value: object) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, np.bytes_):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    raise ValueError(f"unsupported JSON attr type: {type(value).__name__}")


def pose_from_frame(frame_group: h5py.Group) -> dict[str, Any] | None:
    if "t265" not in frame_group:
        return None
    t265 = frame_group["t265"]
    if not isinstance(t265, h5py.Group) or "pose" not in t265.attrs:
        return None
    pose = json_attr(t265.attrs["pose"])
    if not isinstance(pose, dict):
        raise ValueError("pose attr JSON is not an object")
    return pose


def jpeg_dimensions_from_bytes(data: bytes) -> tuple[int, int, int] | None:
    """Return JPEG width, height, and bit precision from SOF markers."""

    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None

    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            return None
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            precision = data[index + 2]
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height, precision
        index += segment_length
    return None


def axis_summary(values: np.ndarray, axes: list[str]) -> dict[str, dict[str, float]]:
    if values.size == 0:
        return {}
    middle = len(values) // 2
    return {
        axis: {
            "min": round(float(np.min(values[:, index])), 6),
            "max": round(float(np.max(values[:, index])), 6),
            "first": round(float(values[0, index]), 6),
            "middle": round(float(values[middle, index]), 6),
            "last": round(float(values[-1, index]), 6),
        }
        for index, axis in enumerate(axes)
    }


def choose_sample_offsets(count: int, maximum: int) -> list[int]:
    if count <= 0 or maximum <= 0:
        return []
    if maximum == 1:
        return [0]
    raw_offsets = [0, count // 2, count - 1]
    if maximum > 3:
        step = max(1, count // maximum)
        raw_offsets.extend(range(0, count, step))
    deduped = sorted(set(raw_offsets))
    return deduped[:maximum]


def normalize_quaternions_xyzw(quaternions: np.ndarray) -> np.ndarray:
    normalized = quaternions.astype(np.float64, copy=True)
    norms = np.linalg.norm(normalized, axis=1)
    if np.any(norms == 0):
        raise ValueError("quaternion contains zero-length samples")
    normalized = normalized / norms[:, None]
    for index in range(1, len(normalized)):
        if float(np.dot(normalized[index - 1], normalized[index])) < 0:
            normalized[index] *= -1
    return normalized


def quaternion_conjugate_xyzw(quaternion: np.ndarray) -> np.ndarray:
    return np.array([-quaternion[0], -quaternion[1], -quaternion[2], quaternion[3]], dtype=np.float64)


def quaternion_multiply_xyzw(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return np.array(
        [
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
            lw * rw - lx * rx - ly * ry - lz * rz,
        ],
        dtype=np.float64,
    )


def quaternion_delta_to_rotvec(delta_xyzw: np.ndarray) -> np.ndarray:
    delta = delta_xyzw / np.linalg.norm(delta_xyzw)
    vector = delta[:3]
    vector_norm = float(np.linalg.norm(vector))
    scalar = float(np.clip(delta[3], -1.0, 1.0))
    if vector_norm < 1e-12:
        return 2.0 * vector
    angle = 2.0 * math.atan2(vector_norm, scalar)
    if angle > math.pi:
        angle -= 2.0 * math.pi
    return angle * vector / vector_norm


def derive_omega_from_quaternion_xyzw(quaternions: np.ndarray, fps: float) -> np.ndarray | None:
    if len(quaternions) < 2:
        return None
    if fps <= 0:
        raise ValueError("fps must be > 0 to derive OmegaXYZ")

    normalized = normalize_quaternions_xyzw(quaternions)
    interval_omega = np.zeros((len(normalized) - 1, 3), dtype=np.float64)
    dt = 1.0 / fps
    for index in range(1, len(normalized)):
        delta = quaternion_multiply_xyzw(normalized[index], quaternion_conjugate_xyzw(normalized[index - 1]))
        interval_omega[index - 1] = quaternion_delta_to_rotvec(delta) / dt

    omega = np.zeros((len(normalized), 3), dtype=np.float64)
    omega[0] = interval_omega[0]
    omega[-1] = interval_omega[-1]
    if len(normalized) > 2:
        omega[1:-1] = (interval_omega[:-1] + interval_omega[1:]) * 0.5
    return omega


def derive_omega_from_euler_xyz(euler_xyz: np.ndarray, fps: float) -> np.ndarray | None:
    if len(euler_xyz) < 2:
        return None
    if fps <= 0:
        raise ValueError("fps must be > 0 to derive Euler OmegaXYZ")
    unwrapped = np.unwrap(euler_xyz.astype(np.float64), axis=0)
    return np.gradient(unwrapped, 1.0 / fps, axis=0)


def array_from_pose(pose: dict[str, Any], key: str, length: int) -> np.ndarray | None:
    value = pose.get(key)
    if value is None:
        return None
    array = np.asarray(value, dtype=np.float64)
    if array.shape != (length,):
        raise ValueError(f"pose.{key} expected shape ({length},), got {array.shape}")
    return array


def load_targets(h5_file: h5py.File, xy_plane: str, fps: float, limit: int | None = None) -> LoadedTargets:
    frame_items = sorted_frame_items(h5_file)
    if not frame_items:
        raise ValueError("no frame_N groups found")
    source_frame_numbers = [frame for frame, _name in frame_items]
    missing_frame_count = len(
        set(range(source_frame_numbers[0], source_frame_numbers[-1] + 1)) - set(source_frame_numbers)
    )
    if limit is not None:
        frame_items = frame_items[:limit]

    xy_indices = XY_PLANES[xy_plane]
    frames: list[int] = []
    frame_names: list[str] = []
    positions: list[np.ndarray] = []
    velocities: list[np.ndarray] = []
    eulers: list[np.ndarray] = []
    quaternions: list[np.ndarray] = []
    cam0_lengths: list[int] = []
    cam0_dtype_counts: Counter[str] = Counter()
    cam0_shape_counts: Counter[str] = Counter()
    cam0_attr_counts: Counter[str] = Counter()
    cam0_chunk_counts: Counter[str] = Counter()
    cam0_compression_counts: Counter[str] = Counter()
    cam0_nonjpeg_frames: list[int] = []
    field_set_counts: Counter[tuple[str, ...]] = Counter()
    pose_key_counts: Counter[tuple[str, ...]] = Counter()
    pose_missing_frames: list[int] = []

    for frame, name in frame_items:
        frame_group = h5_file[name]
        if not isinstance(frame_group, h5py.Group):
            continue
        fields = dataset_keys(frame_group)
        field_set_counts[fields] += 1

        if "cam0" not in frame_group:
            raise ValueError(f"{name}/cam0 is missing")
        cam0 = frame_group["cam0"]
        if not isinstance(cam0, h5py.Dataset):
            raise ValueError(f"{name}/cam0 is not a dataset")

        pose = pose_from_frame(frame_group)
        if pose is None:
            pose_missing_frames.append(frame)
            continue
        pose_key_counts[tuple(sorted(pose))] += 1
        position = array_from_pose(pose, "position", 3)
        velocity = array_from_pose(pose, "velocity", 3)
        euler = array_from_pose(pose, "euler", 3)
        quaternion = array_from_pose(pose, "quaternion", 4)
        if position is None or velocity is None:
            raise ValueError(f"{name}/t265 pose lacks position or velocity")

        cam0_lengths.append(int(cam0.shape[0]) if len(cam0.shape) == 1 else int(cam0.size))
        cam0_dtype_counts[str(cam0.dtype)] += 1
        cam0_shape_counts[str(tuple(cam0.shape))] += 1
        cam0_attr_counts[json.dumps(dict(cam0.attrs), sort_keys=True)] += 1
        cam0_chunk_counts[str(cam0.chunks)] += 1
        cam0_compression_counts[str(cam0.compression)] += 1
        prefix = bytes(cam0[: min(int(cam0.size), 16)])
        if prefix[:2] != b"\xff\xd8":
            cam0_nonjpeg_frames.append(frame)

        frames.append(frame)
        frame_names.append(name)
        positions.append(position)
        velocities.append(velocity)
        if euler is not None:
            eulers.append(euler)
        if quaternion is not None:
            quaternions.append(quaternion)

    if not frames:
        raise ValueError("no usable pose frames found")

    positions_array = np.vstack(positions).astype(np.float64)
    velocities_array = np.vstack(velocities).astype(np.float64)
    eulers_array = np.vstack(eulers).astype(np.float64) if len(eulers) == len(frames) else None
    quaternions_array = np.vstack(quaternions).astype(np.float64) if len(quaternions) == len(frames) else None
    quaternion_omega = (
        derive_omega_from_quaternion_xyzw(quaternions_array, fps) if quaternions_array is not None else None
    )
    euler_omega = derive_omega_from_euler_xyz(eulers_array, fps) if eulers_array is not None else None
    if quaternion_omega is not None:
        omega = quaternion_omega
        omega_source = "pose.quaternion"
    elif euler_omega is not None:
        omega = euler_omega
        omega_source = "pose.euler"
    else:
        omega = None
        omega_source = None

    return LoadedTargets(
        frames=np.asarray(frames, dtype=np.int64),
        frame_names=frame_names,
        xy=positions_array[:, xy_indices],
        vxyz=velocities_array,
        omega_xyz=omega,
        omega_source=omega_source,
        euler_xyz=eulers_array,
        euler_omega_xyz=euler_omega,
        quaternion_xyzw=quaternions_array,
        cam0_lengths=np.asarray(cam0_lengths, dtype=np.int64),
        cam0_dtype_counts=cam0_dtype_counts,
        cam0_shape_counts=cam0_shape_counts,
        cam0_attr_counts=cam0_attr_counts,
        cam0_chunk_counts=cam0_chunk_counts,
        cam0_compression_counts=cam0_compression_counts,
        cam0_nonjpeg_frames=cam0_nonjpeg_frames,
        field_set_counts=field_set_counts,
        pose_key_counts=pose_key_counts,
        pose_missing_frames=pose_missing_frames,
        source_frame_count=len(source_frame_numbers),
        missing_frame_count=missing_frame_count,
    )


def sample_cam0_decodes(h5_file: h5py.File, targets: LoadedTargets, maximum: int) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for offset in choose_sample_offsets(len(targets.frames), maximum):
        frame = int(targets.frames[offset])
        cam0 = h5_file[targets.frame_names[offset]]["cam0"]
        data = bytes(cam0[()])
        dimensions = jpeg_dimensions_from_bytes(data)
        samples.append({
            "frame": frame,
            "byte_length": len(data),
            "first8_hex": data[:8].hex(" "),
            "jpeg_width_height_precision": list(dimensions) if dimensions else None,
        })
    return samples


def target_rows(
    targets: LoadedTargets, cam0_samples: list[dict[str, Any]], xy_plane: str, fps: float
) -> list[dict[str, Any]]:
    cam0_lengths = targets.cam0_lengths
    cam0_shape = {
        "dtype_counts": dict(targets.cam0_dtype_counts),
        "shape_variants": len(targets.cam0_shape_counts),
        "byte_length_min": int(np.min(cam0_lengths)),
        "byte_length_max": int(np.max(cam0_lengths)),
        "byte_length_median": int(np.median(cam0_lengths)),
        "attrs": dict(targets.cam0_attr_counts),
        "chunks": dict(targets.cam0_chunk_counts),
        "compression": dict(targets.cam0_compression_counts),
        "non_jpeg_frame_count": len(targets.cam0_nonjpeg_frames),
        "sample_decodes": cam0_samples,
    }

    omega_status = "derived"
    omega_derived_from = "frame_*/t265.attrs['pose'].quaternion"
    omega_rule = (
        "No direct H5 key observed. Derived from pose.quaternion [x,y,z,w] frame deltas, normalized and "
        f"sign-continuity corrected, using fps={fps:g}; Euler gradient is reported as a cross-check."
    )
    if targets.omega_source == "pose.euler":
        omega_derived_from = "frame_*/t265.attrs['pose'].euler"
        omega_rule = (
            "No direct H5 key observed and complete quaternion samples were unavailable. Derived from unwrapped "
            f"pose.euler frame deltas using fps={fps:g}."
        )
    omega_range: dict[str, Any] | None = (
        axis_summary(targets.omega_xyz, ["wx", "wy", "wz"]) if targets.omega_xyz is not None else None
    )
    if targets.omega_xyz is None:
        omega_status = "unavailable"
        omega_derived_from = None
        omega_rule = "Unavailable: neither pose.quaternion nor pose.euler has at least two complete samples."

    return [
        {
            "target": "cam0",
            "source_key": "frame_*/cam0",
            "target_entity_paths": TARGET_ENTITY_PATHS["cam0"],
            "count": len(targets.frames),
            "dtype_shape_format": cam0_shape,
            "rule": "Use encoded JPEG bytes for the Screen 1 top-left camera image.",
            "range_or_sample": cam0_samples,
        },
        {
            "target": "XY",
            "source_key": "frame_*/t265.attrs['pose'].position",
            "target_entity_paths": TARGET_ENTITY_PATHS["XY"],
            "count": len(targets.frames),
            "dtype_shape_format": {
                "dtype": "float64",
                "shape": list(targets.xy.shape),
                "xy_plane": xy_plane,
            },
            "rule": f"Take pose.position indices {XY_PLANES[xy_plane]} as {xy_plane.upper()} samples.",
            "range_or_sample": axis_summary(targets.xy, list(xy_plane)),
        },
        {
            "target": "VXYZ",
            "source_key": "frame_*/t265.attrs['pose'].velocity",
            "target_entity_paths": TARGET_ENTITY_PATHS["VXYZ"],
            "count": len(targets.frames),
            "dtype_shape_format": {
                "dtype": "float64",
                "shape": list(targets.vxyz.shape),
            },
            "rule": "Use pose.velocity[0:3] directly.",
            "range_or_sample": axis_summary(targets.vxyz, ["vx", "vy", "vz"]),
        },
        {
            "target": "OmegaXYZ",
            "source_key": None,
            "derived_from": omega_derived_from,
            "target_entity_paths": TARGET_ENTITY_PATHS["OmegaXYZ"],
            "count": len(targets.frames) if targets.omega_xyz is not None else 0,
            "dtype_shape_format": {
                "dtype": "float64",
                "shape": list(targets.omega_xyz.shape) if targets.omega_xyz is not None else None,
                "status": omega_status,
                "units": "rad/s from assumed frame cadence",
            },
            "rule": omega_rule,
            "range_or_sample": omega_range,
            "euler_gradient_cross_check": axis_summary(targets.euler_omega_xyz, ["wx", "wy", "wz"])
            if targets.euler_omega_xyz is not None
            else None,
        },
    ]


def build_summary(path: Path, h5_file: h5py.File, targets: LoadedTargets, xy_plane: str, fps: float) -> dict[str, Any]:
    cam0_samples = sample_cam0_decodes(h5_file, targets, 3)
    return {
        "contract": "LR-193-h5-target-data-v1",
        "source_h5": str(path),
        "time_base": {
            "source": "frame_N group index",
            "fps": fps,
            "note": "No explicit timestamp dataset was observed in the verified H5 schema.",
        },
        "frames": {
            "source_frame_count": targets.source_frame_count,
            "used_frame_count": len(targets.frames),
            "first_frame": int(targets.frames[0]),
            "last_frame": int(targets.frames[-1]),
            "missing_frame_count": targets.missing_frame_count,
            "field_set_counts": {str(key): value for key, value in targets.field_set_counts.items()},
            "pose_key_counts": {str(key): value for key, value in targets.pose_key_counts.items()},
            "pose_missing_frame_count": len(targets.pose_missing_frames),
        },
        "targets": target_rows(targets, cam0_samples, xy_plane, fps),
        "next_streaming_interface": {
            "input": "H5 path plus fps and xy_plane",
            "output": [
                "frame_index: int64[N]",
                "cam0: per-frame encoded JPEG bytes from frame_*/cam0",
                "xy: float64[N,2]",
                "vxyz: float64[N,3]",
                "omega_xyz: float64[N,3] derived from quaternion frame deltas, falling back to euler deltas",
            ],
            "rrd_streaming": [
                "For each frame, set one shared Rerun timeline value used by both viewers.",
                "Log cam0 to /screen1/cam0.",
                "Log XY to /screen2/position_xy/trajectory, /head, and /current.",
                "Log VXYZ to /screen2/charts/velocity_xyz/{vx,vy,vz}.",
                "Log OmegaXYZ to /screen2/charts/angular_velocity_xyz/{wx,wy,wz}.",
            ],
        },
    }


def print_table(summary: dict[str, Any]) -> None:
    frames = summary["frames"]
    print(f"Contract: {summary['contract']}")
    print(f"Source: {summary['source_h5']}")
    print(
        "Frames: "
        f"used={frames['used_frame_count']} source={frames['source_frame_count']} "
        f"range={frames['first_frame']}..{frames['last_frame']} missing={frames['missing_frame_count']}"
    )
    print()
    header = ["target", "source/rule", "count", "dtype/shape/format", "target paths"]
    rows: list[list[str]] = []
    for target in summary["targets"]:
        dtype_shape = target["dtype_shape_format"]
        if target["target"] == "cam0":
            dtype_text = (
                f"{dtype_shape['dtype_counts']}; JPEG bytes "
                f"len={dtype_shape['byte_length_min']}..{dtype_shape['byte_length_max']} "
                f"median={dtype_shape['byte_length_median']}"
            )
        else:
            dtype_text = json.dumps(dtype_shape, ensure_ascii=True, sort_keys=True)
        source = target["source_key"] or target.get("derived_from", "derived")
        rows.append([
            target["target"],
            f"{source}; {target['rule']}",
            str(target["count"]),
            dtype_text,
            ", ".join(target["target_entity_paths"]),
        ])

    widths = [max(len(row[index]) for row in [header, *rows]) for index in range(len(header))]
    print(" | ".join(cell.ljust(widths[index]) for index, cell in enumerate(header)))
    print("-|-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))

    print()
    print("Ranges and samples:")
    for target in summary["targets"]:
        print(f"- {target['target']}: {json.dumps(target['range_or_sample'], ensure_ascii=True, sort_keys=True)}")
        if target["target"] == "OmegaXYZ":
            print(
                "  euler_gradient_cross_check: "
                + json.dumps(target["euler_gradient_cross_check"], ensure_ascii=True, sort_keys=True)
            )


def write_extract_outputs(
    out_dir: Path,
    h5_file: h5py.File,
    targets: LoadedTargets,
    summary: dict[str, Any],
    max_cam0_samples: int,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    numeric_path = out_dir / "target_numeric.npz"
    np.savez_compressed(
        numeric_path,
        frame_index=targets.frames,
        xy=targets.xy,
        vxyz=targets.vxyz,
        omega_xyz=targets.omega_xyz if targets.omega_xyz is not None else np.empty((0, 3), dtype=np.float64),
        euler_xyz=targets.euler_xyz if targets.euler_xyz is not None else np.empty((0, 3), dtype=np.float64),
        quaternion_xyzw=targets.quaternion_xyzw
        if targets.quaternion_xyzw is not None
        else np.empty((0, 4), dtype=np.float64),
        cam0_jpeg_lengths=targets.cam0_lengths,
    )

    cam0_dir = out_dir / "cam0_samples"
    cam0_dir.mkdir(parents=True, exist_ok=True)
    sample_files: list[str] = []
    for offset in choose_sample_offsets(len(targets.frames), max_cam0_samples):
        frame = int(targets.frames[offset])
        data = bytes(h5_file[targets.frame_names[offset]]["cam0"][()])
        sample_path = cam0_dir / f"cam0_frame_{frame:06d}.jpg"
        sample_path.write_bytes(data)
        sample_files.append(str(sample_path))

    extract_summary = {
        **summary,
        "extract_outputs": {
            "numeric_npz": str(numeric_path),
            "cam0_sample_files": sample_files,
        },
    }
    summary_path = out_dir / "target_data_summary.json"
    summary_path.write_text(json.dumps(extract_summary, indent=2), encoding="utf-8")
    return extract_summary


def inspect_command(args: argparse.Namespace) -> int:
    try:
        with open_h5_fail_fast(args.h5, args.open_timeout_seconds) as h5_file:
            targets = load_targets(h5_file, args.xy_plane, args.fps)
            summary = build_summary(args.h5, h5_file, targets, args.xy_plane, args.fps)
    except (H5AccessError, ValueError, KeyError, json.JSONDecodeError) as error:
        return report_failure(args.h5, error, args.output)

    if args.output == "json":
        print(json.dumps(summary, indent=2))
    else:
        print_table(summary)
    return 0


def extract_command(args: argparse.Namespace) -> int:
    try:
        with open_h5_fail_fast(args.h5, args.open_timeout_seconds) as h5_file:
            targets = load_targets(h5_file, args.xy_plane, args.fps, args.limit)
            summary = build_summary(args.h5, h5_file, targets, args.xy_plane, args.fps)
            extract_summary = write_extract_outputs(args.out_dir, h5_file, targets, summary, args.max_cam0_samples)
    except (H5AccessError, ValueError, KeyError, json.JSONDecodeError) as error:
        return report_failure(args.h5, error, args.output)

    if args.output == "json":
        print(json.dumps(extract_summary, indent=2))
    else:
        print_table(extract_summary)
        outputs = extract_summary["extract_outputs"]
        print()
        print(f"Wrote numeric targets: {outputs['numeric_npz']}")
        print(f"Wrote cam0 samples: {', '.join(outputs['cam0_sample_files'])}")
    return 0


def report_failure(path: Path, error: object, output: str) -> int:
    payload = {
        "status": "failed",
        "source_h5": str(path),
        "reason": str(error),
        "fail_fast": True,
    }
    if output == "json":
        print(json.dumps(payload, indent=2), file=sys.stderr)
    else:
        print(f"ERROR: cannot inspect {path}: {error}", file=sys.stderr)
    return 2


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("h5", type=Path, help="Source H5 file")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS, help="Assumed frame cadence for derived OmegaXYZ")
    parser.add_argument("--xy-plane", choices=sorted(XY_PLANES), default="xy", help="Pose position plane used for XY")
    parser.add_argument(
        "--open-timeout-seconds",
        type=float,
        default=DEFAULT_OPEN_TIMEOUT_SECONDS,
        help="Fail-fast timeout for local H5 header/open reads; 0 disables the alarm",
    )
    parser.add_argument("--output", choices=["table", "json"], default="table")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Print the four-target H5 contract and ranges")
    add_common_args(inspect_parser)
    inspect_parser.set_defaults(func=inspect_command)

    extract_parser = subparsers.add_parser("extract", help="Write quick target-data outputs for downstream RRD work")
    add_common_args(extract_parser)
    extract_parser.add_argument("--out-dir", type=Path, required=True)
    extract_parser.add_argument(
        "--limit", type=positive_int, default=None, help="Limit extraction to the first N frames"
    )
    extract_parser.add_argument("--max-cam0-samples", type=non_negative_int, default=3)
    extract_parser.set_defaults(func=extract_command)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.fps <= 0:
        print("ERROR: --fps must be > 0", file=sys.stderr)
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
