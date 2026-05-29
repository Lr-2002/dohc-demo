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
RECORDING = ROOT / "out" / "default_lr193.rrd"
SCREEN1_BLUEPRINT = ROOT / "out" / "layouts" / "screen1.rbl"
SCREEN2_BLUEPRINT = ROOT / "out" / "layouts" / "screen2.rbl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rerun-bin", default=os.environ.get("RERUN_BIN", "rerun"))
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--url-host", default="127.0.0.1")
    parser.add_argument("--screen1-port", type=int, default=9101)
    parser.add_argument("--screen2-port", type=int, default=9102)
    parser.add_argument("--grpc-port-base", type=int, default=9876)
    parser.add_argument("--renderer", default="webgl", choices=["webgl", "webgpu"])
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def viewer_command(
    rerun_bin: str,
    bind: str,
    web_port: int,
    grpc_port: int,
    renderer: str,
    blueprint: Path,
) -> list[str]:
    return [
        rerun_bin,
        "--bind",
        bind,
        "--web-viewer-port",
        str(web_port),
        "--port",
        str(grpc_port),
        "--serve-web",
        "--renderer",
        renderer,
        str(RECORDING),
        str(blueprint),
    ]


def viewer_url(host: str, web_port: int, grpc_port: int, renderer: str) -> str:
    grpc_url = quote(f"rerun+http://{host}:{grpc_port}/proxy", safe="")
    return f"http://{host}:{web_port}?url={grpc_url}&renderer={renderer}"


def validate_inputs() -> None:
    missing = [path for path in [RECORDING, SCREEN1_BLUEPRINT, SCREEN2_BLUEPRINT] if not path.is_file()]
    if missing:
        for path in missing:
            print(f"Missing required artifact: {path}", file=sys.stderr)
        raise SystemExit(2)


def main() -> None:
    args = parse_args()
    validate_inputs()

    commands = [
        viewer_command(
            args.rerun_bin,
            args.bind,
            args.screen1_port,
            args.grpc_port_base,
            args.renderer,
            SCREEN1_BLUEPRINT,
        ),
        viewer_command(
            args.rerun_bin,
            args.bind,
            args.screen2_port,
            args.grpc_port_base + 1,
            args.renderer,
            SCREEN2_BLUEPRINT,
        ),
    ]

    for command in commands:
        print(" ".join(command))

    print(
        "Screen 1: "
        + viewer_url(args.url_host, args.screen1_port, args.grpc_port_base, args.renderer)
    )
    print(
        "Screen 2: "
        + viewer_url(args.url_host, args.screen2_port, args.grpc_port_base + 1, args.renderer)
    )

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
