#!/usr/bin/env python3
"""Capture browser evidence for the LR-193 live native Rerun viewers."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image, ImageChops, ImageStat
import websockets


DEFAULT_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
DEFAULT_OUT = Path(__file__).resolve().parent / "out" / "evidence"
DEFAULT_GUI_CAPTURE_HEIGHT = 800


@dataclass(frozen=True)
class Screen:
    name: str
    url: str
    width: int
    height: int


class CdpClient:
    def __init__(self, websocket_url: str) -> None:
        self.websocket_url = websocket_url
        self.next_id = 1
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: asyncio.Queue[dict[str, Any]] | None = None
        self.websocket: Any = None
        self.reader_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "CdpClient":
        self.events = asyncio.Queue()
        self.websocket = await websockets.connect(self.websocket_url, max_size=64 * 1024 * 1024)
        self.reader_task = asyncio.create_task(self._reader())
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if self.reader_task is not None:
            self.reader_task.cancel()
        if self.websocket is not None:
            await self.websocket.close()

    async def _reader(self) -> None:
        while True:
            raw = await self.websocket.recv()
            message = json.loads(raw)
            message_id = message.get("id")
            if message_id is None:
                if self.events is not None:
                    await self.events.put(message)
                continue
            future = self.pending.pop(message_id, None)
            if future is not None and not future.done():
                future.set_result(message)

    async def send(self, method: str, params: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
        message_id = self.next_id
        self.next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[message_id] = future
        await self.websocket.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        response = await asyncio.wait_for(future, timeout=timeout)
        if "error" in response:
            raise RuntimeError(f"CDP {method} failed: {response['error']}")
        return response.get("result", {})

    async def wait_event(self, method: str, timeout: float = 20.0) -> dict[str, Any]:
        if self.events is None:
            raise RuntimeError("CDP event queue is not initialized")
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError(method)
            event = await asyncio.wait_for(self.events.get(), timeout=remaining)
            if event.get("method") == method:
                return event


def wait_for_json(url: str, timeout_seconds: float) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def read_status_sample(status_url: str | None) -> dict[str, Any] | None:
    if not status_url:
        return None
    sample = wait_for_json(status_url, 2.0)
    if not isinstance(sample, dict):
        raise RuntimeError(f"Status URL returned non-object JSON: {sample!r}")
    sample["sampled_at_unix"] = time.time()
    return sample


def timeline_evidence(before_status: dict[str, Any] | None, after_status: dict[str, Any] | None) -> dict[str, Any]:
    before_frame = before_status.get("frame") if before_status else None
    after_frame = after_status.get("frame") if after_status else None
    frame_delta = None
    if isinstance(before_frame, int) and isinstance(after_frame, int):
        frame_delta = after_frame - before_frame
    before_timeline = before_status.get("timeline") if before_status else None
    after_timeline = after_status.get("timeline") if after_status else None
    return {
        "before_frame": before_frame,
        "after_frame": after_frame,
        "frame_delta": frame_delta,
        "before_timeline": before_timeline,
        "after_timeline": after_timeline,
        "same_timeline_name": before_timeline == after_timeline == "frame",
        "advanced": isinstance(frame_delta, int) and frame_delta > 0,
    }


def viewer_source_urls(url: str) -> list[str]:
    query = parse_qs(urlparse(url).query)
    return [unquote(value) for value in query.get("url", [])]


def shared_timeline_evidence(results: list[dict[str, Any]], status_url: str | None) -> dict[str, Any]:
    source_urls = {result["name"]: viewer_source_urls(result["url"]) for result in results}
    primary_sources = {
        name: urls[0] if urls else None
        for name, urls in source_urls.items()
    }
    primary_source_values = [source for source in primary_sources.values() if source is not None]
    samples = []
    for result in results:
        timeline_samples = result.get("timeline_samples", {})
        for phase in ["before", "after"]:
            sample = timeline_samples.get(phase)
            if isinstance(sample, dict):
                samples.append(sample)
    return {
        "status_url": status_url,
        "viewer_source_urls": source_urls,
        "primary_source_urls": primary_sources,
        "same_primary_source_url": len(set(primary_source_values)) == 1 and len(primary_source_values) == len(results),
        "shared_primary_source_url": primary_source_values[0] if primary_source_values else None,
        "all_samples_timeline_frame": bool(samples) and all(sample.get("timeline") == "frame" for sample in samples),
        "all_screens_advanced_on_frame_timeline": all(
            result.get("timeline_evidence", {}).get("same_timeline_name")
            and result.get("timeline_evidence", {}).get("advanced")
            for result in results
        ),
        "sampled_frame_ranges": {
            result["name"]: [
                result.get("timeline_evidence", {}).get("before_frame"),
                result.get("timeline_evidence", {}).get("after_frame"),
            ]
            for result in results
        },
    }


def select_page_target(tabs: list[dict[str, Any]], screen: Screen) -> dict[str, Any]:
    expected = urlparse(screen.url)
    expected_netloc = expected.netloc
    page_tabs = [tab for tab in tabs if tab.get("type") == "page"]
    for tab in page_tabs:
        tab_url = str(tab.get("url", ""))
        parsed = urlparse(tab_url)
        if parsed.netloc == expected_netloc:
            return tab
    if page_tabs:
        return page_tabs[0]
    return tabs[0]


def chrome_command(
    chrome: Path,
    remote_debugging_port: int,
    user_data_dir: Path,
    screen: Screen,
) -> list[str]:
    return [
        str(chrome),
        "--headless=new",
        "--disable-gpu",
        "--use-gl=swiftshader",
        "--enable-unsafe-swiftshader",
        "--no-first-run",
        "--no-default-browser-check",
        f"--remote-debugging-port={remote_debugging_port}",
        f"--user-data-dir={user_data_dir}",
        f"--window-size={screen.width},{screen.height}",
        screen.url,
    ]


def gui_cdp_chrome_command(
    chrome: Path,
    remote_debugging_port: int,
    user_data_dir: Path,
    screen: Screen,
    capture_height: int,
) -> list[str]:
    return [
        str(chrome),
        "--new-window",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-popup-blocking",
        f"--remote-debugging-port={remote_debugging_port}",
        f"--user-data-dir={user_data_dir}",
        "--window-position=0,25",
        f"--window-size={screen.width},{capture_height}",
        screen.url,
    ]


def applescript_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def open_chrome_gui_process(chrome: Path, screen: Screen, capture_height: int) -> tuple[subprocess.Popen[bytes], Path]:
    user_data_dir = Path(tempfile.mkdtemp(prefix=f"lr237-{screen.name}-chrome-gui-"))
    process = subprocess.Popen(
        [
            str(chrome),
            "--new-window",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            f"--user-data-dir={user_data_dir}",
            "--window-position=0,25",
            f"--window-size={screen.width},{capture_height}",
            screen.url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process, user_data_dir


def close_chrome_gui_process(process: subprocess.Popen[bytes], user_data_dir: Path) -> None:
    process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)
    shutil.rmtree(user_data_dir, ignore_errors=True)


def gui_screenshot(output: Path, screen: Screen, capture_height: int) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f"lr237-{screen.name}-", suffix=".png")
    os.close(fd)
    temp_output = Path(temp_name)
    try:
        subprocess.run(["screencapture", "-x", str(temp_output)], check=True)
        with Image.open(temp_output).convert("RGB") as image:
            crop_width = min(screen.width, image.width)
            crop_height = min(capture_height, image.height)
            image.crop((0, 0, crop_width, crop_height)).save(output)
    finally:
        temp_output.unlink(missing_ok=True)


def capture_screen_gui(
    chrome: Path,
    screen: Screen,
    output_dir: Path,
    first_delay: float,
    second_delay: float,
    status_url: str | None,
    capture_height: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    before = output_dir / f"lr237-{screen.name}-live-before.png"
    after = output_dir / f"lr237-{screen.name}-live-after.png"
    process, user_data_dir = open_chrome_gui_process(chrome, screen, capture_height)
    try:
        time.sleep(first_delay)
        before_status = read_status_sample(status_url)
        gui_screenshot(before, screen, capture_height)
        time.sleep(second_delay)
        after_status = read_status_sample(status_url)
        gui_screenshot(after, screen, capture_height)
    finally:
        close_chrome_gui_process(process, user_data_dir)
    return {
        "name": screen.name,
        "url": screen.url,
        "viewport": [screen.width, capture_height],
        "before": str(before),
        "after": str(after),
        "timeline_samples": {
            "before": before_status,
            "after": after_status,
            "expected_visible_marker_path": f"/{screen.name}/frame_marker",
        },
        "timeline_evidence": timeline_evidence(before_status, after_status),
        "diff": image_diff(before, after),
    }


async def capture_png(cdp: CdpClient, output: Path) -> None:
    try:
        result = await cdp.send("Page.captureScreenshot", {"format": "png", "fromSurface": True}, timeout=30.0)
    except asyncio.TimeoutError:
        try:
            result = await cdp.send("Page.captureScreenshot", {"format": "png", "fromSurface": False}, timeout=45.0)
        except asyncio.TimeoutError:
            await cdp.send("Runtime.enable", timeout=10.0)
            result = await cdp.send(
                "Runtime.evaluate",
                {
                    "expression": "(() => { const c = document.querySelector('canvas'); return c ? c.toDataURL('image/png') : null; })()",
                    "returnByValue": True,
                },
                timeout=45.0,
            )
            data_url = result.get("result", {}).get("value")
            if not isinstance(data_url, str) or not data_url.startswith("data:image/png;base64,"):
                raise RuntimeError(f"Canvas capture failed: {result}")
            output.write_bytes(base64.b64decode(data_url.split(",", 1)[1]))
            return
    output.write_bytes(base64.b64decode(result["data"]))


async def capture_screencast_png(cdp: CdpClient, output: Path) -> None:
    await cdp.send(
        "Page.startScreencast",
        {"format": "png", "quality": 100, "maxWidth": 1280, "maxHeight": 800},
        timeout=10.0,
    )
    try:
        event = await cdp.wait_event("Page.screencastFrame", timeout=45.0)
        params = event["params"]
        await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]}, timeout=10.0)
        output.write_bytes(base64.b64decode(params["data"]))
    finally:
        try:
            await cdp.send("Page.stopScreencast", timeout=10.0)
        except Exception:
            pass


async def write_latest_screencast_frame(cdp: CdpClient, output: Path, collect_seconds: float) -> None:
    latest_data: str | None = None
    deadline = time.monotonic() + collect_seconds
    while time.monotonic() < deadline:
        timeout = min(1.0, max(0.01, deadline - time.monotonic()))
        try:
            event = await cdp.wait_event("Page.screencastFrame", timeout=timeout)
        except asyncio.TimeoutError:
            continue
        params = event["params"]
        await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]}, timeout=10.0)
        latest_data = params["data"]
    if latest_data is None:
        event = await cdp.wait_event("Page.screencastFrame", timeout=45.0)
        params = event["params"]
        await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]}, timeout=10.0)
        latest_data = params["data"]
    output.write_bytes(base64.b64decode(latest_data))


async def capture_screen(
    chrome: Path,
    screen: Screen,
    remote_debugging_port: int,
    output_dir: Path,
    first_delay: float,
    second_delay: float,
    status_url: str | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    user_data_dir = Path(tempfile.mkdtemp(prefix=f"lr237-{screen.name}-chrome-"))
    process = subprocess.Popen(
        chrome_command(chrome, remote_debugging_port, user_data_dir, screen),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        version = wait_for_json(f"http://127.0.0.1:{remote_debugging_port}/json/version", 20.0)
        tabs = wait_for_json(f"http://127.0.0.1:{remote_debugging_port}/json", 20.0)
        page = select_page_target(tabs, screen)
        websocket_url = page.get("webSocketDebuggerUrl") or version["webSocketDebuggerUrl"]
        async with CdpClient(websocket_url) as cdp:
            await cdp.send("Page.enable")
            await asyncio.sleep(first_delay)
            before = output_dir / f"lr237-{screen.name}-live-before.png"
            before_status = read_status_sample(status_url)
            await capture_png(cdp, before)
            await asyncio.sleep(second_delay)
            after = output_dir / f"lr237-{screen.name}-live-after.png"
            after_status = read_status_sample(status_url)
            await capture_png(cdp, after)
        return {
            "name": screen.name,
            "url": screen.url,
            "viewport": [screen.width, screen.height],
            "before": str(before),
            "after": str(after),
            "timeline_samples": {
                "before": before_status,
                "after": after_status,
                "expected_visible_marker_path": f"/{screen.name}/frame_marker",
            },
            "timeline_evidence": timeline_evidence(before_status, after_status),
            "diff": image_diff(before, after),
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
        shutil.rmtree(user_data_dir, ignore_errors=True)


async def capture_screen_cdp_screencast(
    chrome: Path,
    screen: Screen,
    remote_debugging_port: int,
    output_dir: Path,
    first_delay: float,
    second_delay: float,
    status_url: str | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    user_data_dir = Path(tempfile.mkdtemp(prefix=f"lr237-{screen.name}-chrome-screencast-"))
    process = subprocess.Popen(
        chrome_command(chrome, remote_debugging_port, user_data_dir, screen),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        version = wait_for_json(f"http://127.0.0.1:{remote_debugging_port}/json/version", 20.0)
        tabs = wait_for_json(f"http://127.0.0.1:{remote_debugging_port}/json", 20.0)
        page = select_page_target(tabs, screen)
        websocket_url = page.get("webSocketDebuggerUrl") or version["webSocketDebuggerUrl"]
        async with CdpClient(websocket_url) as cdp:
            await cdp.send("Page.enable")
            before = output_dir / f"lr237-{screen.name}-live-before.png"
            after = output_dir / f"lr237-{screen.name}-live-after.png"
            await asyncio.sleep(first_delay)
            before_status = read_status_sample(status_url)
            await capture_screencast_png(cdp, before)
            await asyncio.sleep(second_delay)
            after_status = read_status_sample(status_url)
            await capture_screencast_png(cdp, after)
        return {
            "name": screen.name,
            "url": screen.url,
            "viewport": [screen.width, screen.height],
            "capture_transport": "Page.startScreencast",
            "before": str(before),
            "after": str(after),
            "timeline_samples": {
                "before": before_status,
                "after": after_status,
                "expected_visible_marker_path": f"/{screen.name}/frame_marker",
            },
            "timeline_evidence": timeline_evidence(before_status, after_status),
            "diff": image_diff(before, after),
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
        shutil.rmtree(user_data_dir, ignore_errors=True)


async def capture_screen_gui_cdp(
    chrome: Path,
    screen: Screen,
    remote_debugging_port: int,
    output_dir: Path,
    first_delay: float,
    second_delay: float,
    status_url: str | None,
    capture_height: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    user_data_dir = Path(tempfile.mkdtemp(prefix=f"lr237-{screen.name}-chrome-gui-cdp-"))
    process = subprocess.Popen(
        gui_cdp_chrome_command(chrome, remote_debugging_port, user_data_dir, screen, capture_height),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        version = wait_for_json(f"http://127.0.0.1:{remote_debugging_port}/json/version", 20.0)
        tabs = wait_for_json(f"http://127.0.0.1:{remote_debugging_port}/json", 20.0)
        page = select_page_target(tabs, screen)
        websocket_url = page.get("webSocketDebuggerUrl") or version["webSocketDebuggerUrl"]
        async with CdpClient(websocket_url) as cdp:
            await cdp.send("Page.enable")
            await cdp.send("Page.bringToFront")
            await cdp.send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": screen.width,
                    "height": capture_height,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await cdp.send(
                "Page.startScreencast",
                {"format": "png", "quality": 100, "maxWidth": screen.width, "maxHeight": capture_height},
                timeout=10.0,
            )
            try:
                before = output_dir / f"lr237-{screen.name}-live-before.png"
                await write_latest_screencast_frame(cdp, before, first_delay)
                before_status = read_status_sample(status_url)
                after = output_dir / f"lr237-{screen.name}-live-after.png"
                await write_latest_screencast_frame(cdp, after, second_delay)
                after_status = read_status_sample(status_url)
            finally:
                try:
                    await cdp.send("Page.stopScreencast", timeout=10.0)
                except Exception:
                    pass
        return {
            "name": screen.name,
            "url": screen.url,
            "viewport": [screen.width, capture_height],
            "capture_transport": "Page.startScreencast",
            "before": str(before),
            "after": str(after),
            "timeline_samples": {
                "before": before_status,
                "after": after_status,
                "expected_visible_marker_path": f"/{screen.name}/frame_marker",
            },
            "timeline_evidence": timeline_evidence(before_status, after_status),
            "diff": image_diff(before, after),
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
        shutil.rmtree(user_data_dir, ignore_errors=True)


async def capture_one_gui_cdp_frame(
    chrome: Path,
    screen: Screen,
    remote_debugging_port: int,
    output: Path,
    wait_seconds: float,
    status_url: str | None,
    capture_height: int,
) -> dict[str, Any] | None:
    user_data_dir = Path(tempfile.mkdtemp(prefix=f"lr237-{screen.name}-chrome-gui-cdp-"))
    process = subprocess.Popen(
        gui_cdp_chrome_command(chrome, remote_debugging_port, user_data_dir, screen, capture_height),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        version = wait_for_json(f"http://127.0.0.1:{remote_debugging_port}/json/version", 20.0)
        tabs = wait_for_json(f"http://127.0.0.1:{remote_debugging_port}/json", 20.0)
        page = select_page_target(tabs, screen)
        websocket_url = page.get("webSocketDebuggerUrl") or version["webSocketDebuggerUrl"]
        async with CdpClient(websocket_url) as cdp:
            await cdp.send("Page.enable")
            await cdp.send("Page.bringToFront")
            await cdp.send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": screen.width,
                    "height": capture_height,
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            await cdp.send(
                "Page.startScreencast",
                {"format": "png", "quality": 100, "maxWidth": screen.width, "maxHeight": capture_height},
                timeout=10.0,
            )
            try:
                await write_latest_screencast_frame(cdp, output, wait_seconds)
                return read_status_sample(status_url)
            finally:
                try:
                    await cdp.send("Page.stopScreencast", timeout=10.0)
                except Exception:
                    pass
    finally:
        process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
        shutil.rmtree(user_data_dir, ignore_errors=True)


async def capture_screen_gui_cdp_reopen(
    chrome: Path,
    screen: Screen,
    remote_debugging_port: int,
    output_dir: Path,
    first_delay: float,
    second_delay: float,
    status_url: str | None,
    capture_height: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    before = output_dir / f"lr237-{screen.name}-live-before.png"
    after = output_dir / f"lr237-{screen.name}-live-after.png"
    before_status = await capture_one_gui_cdp_frame(
        chrome,
        screen,
        remote_debugging_port,
        before,
        first_delay,
        status_url,
        capture_height,
    )
    await asyncio.sleep(second_delay)
    after_status = await capture_one_gui_cdp_frame(
        chrome,
        screen,
        remote_debugging_port,
        after,
        first_delay,
        status_url,
        capture_height,
    )
    return {
        "name": screen.name,
        "url": screen.url,
        "viewport": [screen.width, capture_height],
        "capture_transport": "Page.startScreencast with fresh browser target per sample",
        "before": str(before),
        "after": str(after),
        "timeline_samples": {
            "before": before_status,
            "after": after_status,
            "expected_visible_marker_path": f"/{screen.name}/frame_marker",
        },
        "timeline_evidence": timeline_evidence(before_status, after_status),
        "diff": image_diff(before, after),
    }


def image_diff(before: Path, after: Path) -> dict[str, Any]:
    with Image.open(before).convert("RGB") as img_before, Image.open(after).convert("RGB") as img_after:
        diff = ImageChops.difference(img_before, img_after)
        stat = ImageStat.Stat(diff)
        width, height = img_before.size
        nonzero_bbox = diff.getbbox()
        changed_pixels = 0
        if nonzero_bbox is not None:
            changed_pixels = sum(1 for pixel in diff.getdata() if pixel != (0, 0, 0))
        return {
            "size": [width, height],
            "changed_pixels": changed_pixels,
            "changed_ratio": changed_pixels / float(width * height),
            "mean_abs_rgb": stat.mean,
            "bbox": list(nonzero_bbox) if nonzero_bbox is not None else None,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", type=Path, default=DEFAULT_CHROME)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--screen1-url", required=True)
    parser.add_argument("--screen2-url", required=True)
    parser.add_argument("--first-delay", type=float, default=10.0)
    parser.add_argument("--second-delay", type=float, default=6.0)
    parser.add_argument("--screen1-port", type=int, default=9331)
    parser.add_argument("--screen2-port", type=int, default=9332)
    parser.add_argument("--status-url", default=None, help="Optional LR-237 stream status JSON URL")
    parser.add_argument(
        "--capture-mode",
        choices=["cdp", "cdp-screencast", "screencapture", "gui-cdp", "gui-cdp-reopen"],
        default="cdp",
    )
    parser.add_argument("--screen-order", choices=["screen1-first", "screen2-first"], default="screen1-first")
    parser.add_argument("--gui-capture-height", type=int, default=DEFAULT_GUI_CAPTURE_HEIGHT)
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    if not args.chrome.is_file():
        raise FileNotFoundError(args.chrome)
    screen_ports = [
        (Screen("screen1", args.screen1_url, 1280, 720), args.screen1_port),
        (Screen("screen2", args.screen2_url, 1280, 720), args.screen2_port),
    ]
    if args.screen_order == "screen2-first":
        screen_ports.reverse()
    results = []
    if args.capture_mode == "screencapture":
        for screen, _port in screen_ports:
            results.append(
                capture_screen_gui(
                    args.chrome,
                    screen,
                    args.out_dir,
                    args.first_delay,
                    args.second_delay,
                    args.status_url,
                    args.gui_capture_height,
                )
            )
    elif args.capture_mode == "cdp-screencast":
        for screen, port in screen_ports:
            results.append(
                await capture_screen_cdp_screencast(
                    args.chrome,
                    screen,
                    port,
                    args.out_dir,
                    args.first_delay,
                    args.second_delay,
                    args.status_url,
                )
            )
    elif args.capture_mode == "gui-cdp":
        for screen, port in screen_ports:
            results.append(
                await capture_screen_gui_cdp(
                    args.chrome,
                    screen,
                    port,
                    args.out_dir,
                    args.first_delay,
                    args.second_delay,
                    args.status_url,
                    args.gui_capture_height,
                )
            )
    elif args.capture_mode == "gui-cdp-reopen":
        for screen, port in screen_ports:
            results.append(
                await capture_screen_gui_cdp_reopen(
                    args.chrome,
                    screen,
                    port,
                    args.out_dir,
                    args.first_delay,
                    args.second_delay,
                    args.status_url,
                    args.gui_capture_height,
                )
            )
    else:
        for screen, port in screen_ports:
            results.append(
                await capture_screen(
                    args.chrome,
                    screen,
                    port,
                    args.out_dir,
                    args.first_delay,
                    args.second_delay,
                    args.status_url,
                )
            )
    report = {
        "contract": "LR-237-browser-evidence-v1",
        "captured_at_unix": time.time(),
        "chrome": str(args.chrome),
        "capture_mode": args.capture_mode,
        "status_url": args.status_url,
        "shared_timeline_evidence": shared_timeline_evidence(results, args.status_url),
        "screens": results,
    }
    report_path = args.out_dir / "lr237-browser-evidence.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def main() -> int:
    os.environ.setdefault("NO_COLOR", "1")
    args = parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
