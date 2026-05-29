#!/usr/bin/env python3
"""Launch the LR-193 screens as native Rerun web viewers."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
RECORDING = ROOT / "out" / "default_lr193.rrd"
SCREEN1_BLUEPRINT = ROOT / "out" / "layouts" / "screen1.rbl"
SCREEN2_BLUEPRINT = ROOT / "out" / "layouts" / "screen2.rbl"
STATIC_SERVER = ROOT / "serve_web_viewer_gzip.py"
DATA_PREFIX = "/lr193"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-bin", default=os.environ.get("PYTHON", sys.executable))
    parser.add_argument("--web-viewer-dir", type=Path, default=REPO_ROOT / "web_viewer")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--url-host", default="127.0.0.1")
    parser.add_argument("--screen1-port", type=int, default=9101)
    parser.add_argument("--screen2-port", type=int, default=9102)
    parser.add_argument("--renderer", default="webgl", choices=["webgl", "webgpu"])
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def viewer_command(
    python_bin: str,
    web_viewer_dir: Path,
    bind: str,
    web_port: int,
) -> list[str]:
    return [
        python_bin,
        str(STATIC_SERVER),
        "--bind",
        bind,
        "--port",
        str(web_port),
        "--directory",
        str(web_viewer_dir),
        "--asset-root",
        f"{DATA_PREFIX}={ROOT / 'out'}",
    ]


def viewer_url(host: str, web_port: int, layout: str, renderer: str) -> str:
    recording_url = quote(f"http://{host}:{web_port}{DATA_PREFIX}/{RECORDING.name}", safe="")
    layout_url = quote(f"http://{host}:{web_port}{DATA_PREFIX}/layouts/{layout}", safe="")
    return f"http://{host}:{web_port}?url={recording_url}&url={layout_url}&renderer={renderer}"


def validate_inputs(web_viewer_dir: Path, dry_run: bool) -> None:
    missing = [path for path in [RECORDING, SCREEN1_BLUEPRINT, SCREEN2_BLUEPRINT] if not path.is_file()]
    if not dry_run and not web_viewer_dir.is_dir():
        missing.append(web_viewer_dir)
    if missing:
        for path in missing:
            print(f"Missing required artifact: {path}", file=sys.stderr)
        raise SystemExit(2)


def main() -> None:
    args = parse_args()
    validate_inputs(args.web_viewer_dir, args.dry_run)

    commands = [
        viewer_command(
            args.python_bin,
            args.web_viewer_dir,
            args.bind,
            args.screen1_port,
        ),
        viewer_command(
            args.python_bin,
            args.web_viewer_dir,
            args.bind,
            args.screen2_port,
        ),
    ]

    for command in commands:
        print(" ".join(command))

    print("Screen 1: " + viewer_url(args.url_host, args.screen1_port, SCREEN1_BLUEPRINT.name, args.renderer))
    print("Screen 2: " + viewer_url(args.url_host, args.screen2_port, SCREEN2_BLUEPRINT.name, args.renderer))

    if args.dry_run:
        return

    processes = [subprocess.Popen(command) for command in commands]
    try:
        for process in processes:
            process.wait()
    except KeyboardInterrupt:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
