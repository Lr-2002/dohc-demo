#!/usr/bin/env python3
"""Record short MP4 evidence videos for the LR-193 live native Rerun viewers."""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import av
import numpy as np
from PIL import Image

from capture_lr193_browser_evidence import (
    CdpClient,
    DEFAULT_CHROME,
    DEFAULT_OUT,
    Screen,
    chrome_command,
    read_status_sample,
    select_page_target,
    shared_timeline_evidence,
    timeline_evidence,
    wait_for_json,
)


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


class Mp4Encoder:
    def __init__(self, output: Path, fps: int, width: int, height: int) -> None:
        self.output = output
        self.width = width - (width % 2)
        self.height = height - (height % 2)
        self.container = av.open(str(output), mode="w")
        self.stream = self._add_stream(fps)

    def _add_stream(self, fps: int) -> Any:
        last_error: Exception | None = None
        for codec in ["libx264", "h264", "mpeg4"]:
            try:
                stream = self.container.add_stream(codec, rate=fps)
                stream.width = self.width
                stream.height = self.height
                stream.pix_fmt = "yuv420p"
                if codec == "libx264":
                    stream.options = {"preset": "veryfast", "crf": "23"}
                return stream
            except Exception as error:  # pragma: no cover - depends on local codec build
                last_error = error
        raise RuntimeError(f"No MP4 video codec available: {last_error}")

    def encode(self, image: Image.Image) -> None:
        frame_image = self._fit(image)
        frame = av.VideoFrame.from_ndarray(np.asarray(frame_image), format="rgb24")
        for packet in self.stream.encode(frame):
            self.container.mux(packet)

    def close(self) -> None:
        for packet in self.stream.encode():
            self.container.mux(packet)
        self.container.close()

    def _fit(self, image: Image.Image) -> Image.Image:
        rgb = image.convert("RGB")
        if rgb.size == (self.width, self.height):
            return rgb
        fitted = rgb.copy()
        fitted.thumbnail((self.width, self.height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (self.width, self.height), (0, 0, 0))
        x = (self.width - fitted.width) // 2
        y = (self.height - fitted.height) // 2
        canvas.paste(fitted, (x, y))
        return canvas


async def record_screen(
    chrome: Path,
    screen: Screen,
    remote_debugging_port: int,
    output_dir: Path,
    warmup_seconds: float,
    duration_seconds: float,
    fps: int,
    status_url: str | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"lr237-{screen.name}-live-video.mp4"
    user_data_dir = Path(tempfile.mkdtemp(prefix=f"lr237-{screen.name}-video-chrome-"))
    process = subprocess.Popen(
        chrome_command(chrome, remote_debugging_port, user_data_dir, screen),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    encoder: Mp4Encoder | None = None
    frame_count = 0
    first_status: dict[str, Any] | None = None
    final_status: dict[str, Any] | None = None
    try:
        version = wait_for_json(f"http://127.0.0.1:{remote_debugging_port}/json/version", 20.0)
        tabs = wait_for_json(f"http://127.0.0.1:{remote_debugging_port}/json", 20.0)
        page = select_page_target(tabs, screen)
        websocket_url = page.get("webSocketDebuggerUrl") or version["webSocketDebuggerUrl"]
        async with CdpClient(websocket_url) as cdp:
            await cdp.send("Page.enable")
            await cdp.send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": screen.width,
                    "height": screen.height,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await asyncio.sleep(warmup_seconds)
            first_status = read_status_sample(status_url)
            await cdp.send(
                "Page.startScreencast",
                {
                    "format": "png",
                    "quality": 80,
                    "maxWidth": screen.width,
                    "maxHeight": screen.height,
                    "everyNthFrame": 1,
                },
                timeout=10.0,
            )
            encoder = Mp4Encoder(output, fps, screen.width, screen.height)
            deadline = time.monotonic() + duration_seconds
            try:
                while time.monotonic() < deadline:
                    timeout = min(1.0, max(0.01, deadline - time.monotonic()))
                    try:
                        event = await cdp.wait_event("Page.screencastFrame", timeout=timeout)
                    except asyncio.TimeoutError:
                        continue
                    params = event["params"]
                    await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]}, timeout=10.0)
                    image = Image.open(io.BytesIO(base64.b64decode(params["data"]))).convert("RGB")
                    encoder.encode(image)
                    frame_count += 1
            finally:
                try:
                    await cdp.send("Page.stopScreencast", timeout=10.0)
                except Exception:
                    pass
            final_status = read_status_sample(status_url)
    finally:
        if encoder is not None:
            encoder.close()
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
        shutil.rmtree(user_data_dir, ignore_errors=True)
    if frame_count == 0:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"No screencast frames captured for {screen.name}")
    return {
        "name": screen.name,
        "url": screen.url,
        "video": str(output),
        "viewport": [screen.width, screen.height],
        "recording": {
            "duration_seconds": duration_seconds,
            "encoded_fps": fps,
            "captured_frame_count": frame_count,
            "container": "mp4",
        },
        "timeline_samples": {
            "before": first_status,
            "after": final_status,
            "expected_visible_marker_path": f"/{screen.name}/frame_marker",
        },
        "timeline_evidence": timeline_evidence(first_status, final_status),
    }


async def main_async(args: argparse.Namespace) -> int:
    screens = [
        Screen("screen1", args.screen1_url, args.width, args.height),
        Screen("screen2", args.screen2_url, args.width, args.height),
    ]
    results = []
    for index, screen in enumerate(screens):
        results.append(
            await record_screen(
                args.chrome,
                screen,
                args.remote_debugging_port + index,
                args.out_dir,
                args.warmup_seconds,
                args.duration_seconds,
                args.fps,
                args.status_url,
            )
        )
    payload = {
        "captured_at_unix": time.time(),
        "status_url": args.status_url,
        "videos": results,
        "shared_timeline": shared_timeline_evidence(results, args.status_url),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True), flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen1-url", required=True)
    parser.add_argument("--screen2-url", required=True)
    parser.add_argument("--status-url", default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--summary-out", type=Path, default=DEFAULT_OUT / "lr237-browser-video-evidence.json")
    parser.add_argument("--chrome", type=Path, default=DEFAULT_CHROME)
    parser.add_argument("--remote-debugging-port", type=int, default=9661)
    parser.add_argument("--width", type=positive_int, default=1280)
    parser.add_argument("--height", type=positive_int, default=720)
    parser.add_argument("--warmup-seconds", type=positive_float, default=20.0)
    parser.add_argument("--duration-seconds", type=positive_float, default=12.0)
    parser.add_argument("--fps", type=positive_int, default=15)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
