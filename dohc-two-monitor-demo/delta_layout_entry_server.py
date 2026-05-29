#!/usr/bin/env python3
"""Entry server for the LR-193 two-screen Rerun deployment."""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import shutil
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final
from urllib.parse import parse_qs, urlparse


ROUTE_BY_PORT: Final[dict[int, str]] = {
    9101: "screen1",
    9102: "screen2",
}
LAYOUT_BY_ROUTE: Final[dict[str, str]] = {
    "screen1": "screen1.rbl",
    "screen2": "screen2.rbl",
}
ENTRY_TITLE_BY_ROUTE: Final[dict[str, str]] = {
    "screen1": "Delta Screen 1",
    "screen2": "Delta Screen 2",
}


class DeltaEntryServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        root: Path,
        asset_base_url: str,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.root = root
        self.asset_base_url = asset_base_url.rstrip("/")


class DeltaEntryHandler(BaseHTTPRequestHandler):
    server: DeltaEntryServer

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Delta-Layout-Name")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = self._route_for(parsed.path)

        if parsed.path in {"/", "/screen1", "/screen2", "/index.html"}:
            self._send_html(route, parsed.query)
            return

        if parsed.path == "/healthz":
            self._send_json(
                {
                    "ok": True,
                    "route": route,
                    "recording": "/recordings/default_lr193.rrd",
                    "layout": f"/layouts/{LAYOUT_BY_ROUTE[route]}",
                }
            )
            return

        if parsed.path == "/sources":
            self._send_sources(route)
            return

        if parsed.path == "/delta-icon.png":
            self._send_file(self.server.root / "assets" / "delta-icon.png", "image/png")
            return

        if parsed.path == "/favicon.ico":
            self._send_file(self.server.root / "assets" / "delta-icon.png", "image/png")
            return

        if parsed.path.startswith("/static/"):
            name = Path(parsed.path).name
            self._send_file(self.server.root / "out" / "static" / name)
            return

        if parsed.path == "/recordings/default_lr193.rrd":
            self._send_file(self.server.root / "out" / "default_lr193.rrd", "application/octet-stream")
            return

        if parsed.path.startswith("/layouts/"):
            name = Path(parsed.path).name
            if name in set(LAYOUT_BY_ROUTE.values()):
                self._send_file(self.server.root / "out" / "layouts" / name, "application/octet-stream")
                return

        if parsed.path in {
            "/re_viewer.js",
            "/re_viewer_bg.wasm",
            "/sw.js",
            "/apple-touch-icon.png",
        }:
            self._proxy_asset(parsed.path)
            return

        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/save-layout":
            self._save_layout(parsed.query)
            return

        self.send_error(404, "Not found")

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} [{self.log_date_time_string()}] {fmt % args}")

    def _route_for(self, path: str) -> str:
        if path.startswith("/screen2"):
            return "screen2"
        if path.startswith("/screen1"):
            return "screen1"
        return ROUTE_BY_PORT.get(self.server.server_port, "screen1")

    def _public_origin(self) -> str:
        host = self.headers.get("Host")
        if host:
            return f"http://{host}"
        return f"http://127.0.0.1:{self.server.server_port}"

    def _send_html(self, route: str, query_string: str) -> None:
        origin = self._public_origin()
        layout = LAYOUT_BY_ROUTE[route]
        title = ENTRY_TITLE_BY_ROUTE[route]
        source_urls = [
            f"{origin}/recordings/default_lr193.rrd?route={route}",
            f"{origin}/layouts/{layout}?route={route}",
        ]
        params = parse_qs(query_string)
        renderer = params.get("renderer", ["webgl"])[0]
        theme = params.get("theme", ["dark"])[0]
        if not self._is_live_mode(params):
            body = STATIC_HTML_TEMPLATE.format(
                route=json.dumps(route),
                title=html.escape(title),
            )
            self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8")
            return

        body = RERUN_HTML_TEMPLATE.format(
            route=json.dumps(route),
            title=html.escape(title),
            source_urls=json.dumps(source_urls),
            renderer=json.dumps(renderer),
            theme=json.dumps(theme),
        )
        self._send_bytes(body.encode("utf-8"), "text/html; charset=utf-8")

    def _is_live_mode(self, params: dict[str, list[str]]) -> bool:
        if params.get("live", ["0"])[0] in {"1", "true", "yes"}:
            return True
        if params.get("freeze", ["1"])[0] in {"0", "false", "no"}:
            return True
        return False

    def _send_sources(self, route: str) -> None:
        origin = self._public_origin()
        layout = LAYOUT_BY_ROUTE[route]
        self._send_json(
            {
                "default_session_id": "lr193-default",
                "blueprints": {
                    "screen1": f"{origin}/layouts/screen1.rbl",
                    "screen2": f"{origin}/layouts/screen2.rbl",
                },
                "sessions": [
                    {
                        "id": "lr193-default",
                        "recording_url": f"{origin}/recordings/default_lr193.rrd",
                        "blueprint_url": f"{origin}/layouts/{layout}",
                        "source_urls": [
                            f"{origin}/recordings/default_lr193.rrd",
                            f"{origin}/layouts/{layout}",
                        ],
                    }
                ],
            }
        )

    def _save_layout(self, query_string: str) -> None:
        params = parse_qs(query_string)
        route = params.get("route", [ROUTE_BY_PORT.get(self.server.server_port, "screen1")])[0]
        if route not in LAYOUT_BY_ROUTE:
            self.send_error(400, f"Invalid route: {route}")
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(411, "Invalid Content-Length")
            return

        max_layout_bytes = 5 * 1024 * 1024
        if content_length <= 0:
            self.send_error(400, "Empty layout body")
            return
        if content_length > max_layout_bytes:
            self.send_error(413, "Layout body is too large")
            return

        data = self.rfile.read(content_length)
        if len(data) != content_length:
            self.send_error(400, "Incomplete layout body")
            return

        layouts = self.server.root / "out" / "layouts"
        layouts.mkdir(parents=True, exist_ok=True)
        target = layouts / LAYOUT_BY_ROUTE[route]
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(target)

        self._send_json(
            {
                "ok": True,
                "route": route,
                "layout": f"/layouts/{target.name}",
                "bytes": len(data),
                "path": str(target),
            }
        )

    def _send_json(self, payload: object) -> None:
        self._send_bytes(json.dumps(payload, indent=2).encode("utf-8"), "application/json; charset=utf-8")

    def _send_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.is_file():
            self.send_error(404, f"Missing file: {path.name}")
            return

        guessed_type = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send_bytes(path.read_bytes(), guessed_type)

    def _proxy_asset(self, path: str) -> None:
        url = f"{self.server.asset_base_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                data = response.read()
                content_type = response.headers.get_content_type()
        except (urllib.error.URLError, TimeoutError) as err:
            self.send_error(502, f"Failed to fetch Rerun asset: {err}")
            return

        self._send_bytes(data, content_type)

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        if content_type == "application/wasm":
            self.send_header("rerun-final-length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


STATIC_HTML_TEMPLATE: Final[str] = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no" />
    <link rel="icon" href="/delta-icon.png" />
    <link rel="apple-touch-icon" href="/delta-icon.png" />
    <title>{title}</title>
    <style>
      html,
      body {{
        margin: 0;
        width: 100%;
        height: 100%;
        overflow: hidden;
        background: #0d1011;
        color: #d8e3e8;
        font: 500 13px/1.35 Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      #delta_corner_logo {{
        position: fixed;
        top: 9px;
        left: 10px;
        z-index: 5;
        width: 30px;
        height: 30px;
        object-fit: contain;
        pointer-events: none;
      }}
      #delta_loader,
      #delta_error {{
        display: none;
      }}
      .screen {{
        width: 100vw;
        height: 100vh;
        box-sizing: border-box;
        background: #0d1011;
      }}
      .screen-one {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
        gap: 8px;
        padding: 8px;
      }}
      .pane {{
        position: relative;
        min-width: 0;
        min-height: 0;
        overflow: hidden;
        background: #060708;
        border: 1px solid #20272b;
      }}
      .pane__label {{
        position: absolute;
        top: 8px;
        left: 12px;
        z-index: 1;
        padding: 3px 7px;
        border-radius: 4px;
        background: rgba(6, 8, 10, 0.72);
        color: #e6eef2;
        font-size: 12px;
        letter-spacing: 0;
      }}
      .screen-one .pane:first-child .pane__label {{
        left: 48px;
      }}
      .pane img {{
        width: 100%;
        height: 100%;
        display: block;
        object-fit: contain;
      }}
      .screen-two {{
        display: grid;
        grid-template-columns: minmax(360px, 1fr) minmax(300px, 1fr) minmax(360px, 1fr);
        gap: 8px;
        padding: 8px;
      }}
      .chart-stack {{
        display: grid;
        grid-template-rows: repeat(3, minmax(0, 1fr));
        gap: 8px;
        min-width: 0;
        min-height: 0;
      }}
      .chart-pane {{
        position: relative;
        min-width: 0;
        min-height: 0;
        overflow: hidden;
        background: #101416;
        border: 1px solid #263034;
      }}
      .chart-title {{
        position: absolute;
        top: 8px;
        left: 10px;
        z-index: 1;
        color: #edf5f8;
        font-size: 12px;
      }}
      .chart-stack .chart-pane:first-child .chart-title {{
        left: 48px;
      }}
      .chart-pane canvas {{
        width: 100%;
        height: 100%;
        display: block;
      }}
      .logo-stage {{
        display: grid;
        place-items: center;
        min-width: 0;
        min-height: 0;
        background: #f4f4f0;
        border: 1px solid #d7d7d0;
      }}
      .logo-stage img {{
        width: min(58%, 310px);
        max-height: 58%;
        object-fit: contain;
      }}
      .t265-stage {{
        min-width: 0;
        min-height: 0;
        background: #060708;
        border: 1px solid #20272b;
      }}
      @media (max-width: 900px) {{
        .screen-one,
        .screen-two {{
          grid-template-columns: 1fr;
          grid-template-rows: repeat(2, minmax(0, 1fr));
        }}
        .screen-two {{
          grid-template-rows: minmax(0, 1.2fr) minmax(0, 0.8fr) minmax(0, 1fr);
        }}
      }}
    </style>
  </head>
  <body>
    <img id="delta_corner_logo" src="/delta-icon.png" alt="" />
    <div id="delta_loader">Loading {title}...</div>
    <pre id="delta_error"></pre>
    <main id="delta_static" class="screen"></main>
    <script>
      const route = {route};
      const root = document.getElementById("delta_static");
      window.deltaFramePolicy = {{
        mode: "static_snapshot",
        stopped: true,
      }};

      function htmlForScreen1() {{
        return `
          <section class="pane">
            <div class="pane__label">Cam0</div>
            <img src="/static/cam0.jpg" alt="" />
          </section>
          <section class="pane">
            <div class="pane__label">Cam1</div>
            <img src="/static/cam1.jpg" alt="" />
          </section>
        `;
      }}

      function htmlForScreen2() {{
        return `
          <section class="chart-stack">
            <div class="chart-pane">
              <div class="chart-title">Velocity XYZ</div>
              <canvas data-chart="velocity_xyz"></canvas>
            </div>
            <div class="chart-pane">
              <div class="chart-title">Angular velocity XYZ</div>
              <canvas data-chart="angular_velocity_xyz"></canvas>
            </div>
            <div class="chart-pane">
              <div class="chart-title">Position XY</div>
              <canvas data-chart="position_xy"></canvas>
            </div>
          </section>
          <section class="logo-stage">
            <img src="/delta-icon.png" alt="" />
          </section>
          <section class="pane t265-stage">
            <div class="pane__label">T265</div>
            <img src="/static/t265.jpg" alt="" />
          </section>
        `;
      }}

      function extent(rows) {{
        let min = Infinity;
        let max = -Infinity;
        for (const row of rows) {{
          for (const value of row) {{
            min = Math.min(min, value);
            max = Math.max(max, value);
          }}
        }}
        if (!Number.isFinite(min) || !Number.isFinite(max)) {{
          return [-1, 1];
        }}
        if (min === max) {{
          return [min - 1, max + 1];
        }}
        const pad = (max - min) * 0.12;
        return [min - pad, max + pad];
      }}

      function drawChart(canvas, rows, labels) {{
        const rect = canvas.getBoundingClientRect();
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const width = Math.max(1, Math.floor(rect.width * dpr));
        const height = Math.max(1, Math.floor(rect.height * dpr));
        if (canvas.width !== width || canvas.height !== height) {{
          canvas.width = width;
          canvas.height = height;
        }}

        const ctx = canvas.getContext("2d");
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, rect.width, rect.height);

        const plot = {{
          left: 48,
          right: rect.width - 18,
          top: 32,
          bottom: rect.height - 24,
        }};
        const [minY, maxY] = extent(rows);
        const yFor = (value) => plot.bottom - ((value - minY) / (maxY - minY)) * (plot.bottom - plot.top);
        const xFor = (index) => plot.left + (index / Math.max(1, rows.length - 1)) * (plot.right - plot.left);
        const colors = ["#55beff", "#4cd68d", "#ffcc5c"];

        ctx.strokeStyle = "#253036";
        ctx.lineWidth = 1;
        ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
        ctx.fillStyle = "#82939b";
        for (let i = 0; i <= 4; i += 1) {{
          const y = plot.top + ((plot.bottom - plot.top) * i) / 4;
          ctx.beginPath();
          ctx.moveTo(plot.left, y);
          ctx.lineTo(plot.right, y);
          ctx.stroke();
          const value = maxY - ((maxY - minY) * i) / 4;
          ctx.fillText(value.toExponential(1), 8, y + 4);
        }}

        labels.forEach((label, series) => {{
          ctx.strokeStyle = colors[series % colors.length];
          ctx.lineWidth = 2;
          ctx.beginPath();
          rows.forEach((row, index) => {{
            const x = xFor(index);
            const y = yFor(row[series] || 0);
            if (index === 0) {{
              ctx.moveTo(x, y);
            }} else {{
              ctx.lineTo(x, y);
            }}
          }});
          ctx.stroke();

          ctx.fillStyle = colors[series % colors.length];
          ctx.fillText(label, plot.right - (labels.length - series) * 34, 19);
        }});
      }}

      async function render() {{
        if (route === "screen2") {{
          root.className = "screen screen-two";
          root.innerHTML = htmlForScreen2();
          const telemetry = await fetch("/static/telemetry.json", {{ cache: "no-store" }}).then((response) => response.json());
          const renderCharts = () => {{
            drawChart(document.querySelector('[data-chart="velocity_xyz"]'), telemetry.velocity_xyz, ["vx", "vy", "vz"]);
            drawChart(
              document.querySelector('[data-chart="angular_velocity_xyz"]'),
              telemetry.angular_velocity_xyz,
              ["wx", "wy", "wz"],
            );
            drawChart(document.querySelector('[data-chart="position_xy"]'), telemetry.position_xy, ["x", "y"]);
          }};
          renderCharts();
          let resizeTimer = 0;
          window.addEventListener("resize", () => {{
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(renderCharts, 120);
          }});
        }} else {{
          root.className = "screen screen-one";
          root.innerHTML = htmlForScreen1();
        }}
        window.deltaStaticReady = true;
      }}

      render().catch((error) => {{
        console.error(error);
        const errorBox = document.getElementById("delta_error");
        errorBox.textContent = String(error && error.stack ? error.stack : error);
        errorBox.style.display = "grid";
      }});
    </script>
  </body>
</html>
"""


RERUN_HTML_TEMPLATE: Final[str] = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no" />
    <link rel="icon" href="/delta-icon.png" />
    <link rel="apple-touch-icon" href="/delta-icon.png" />
    <title>{title}</title>
    <style>
      html,
      body {{
        margin: 0;
        width: 100%;
        height: 100%;
        overflow: hidden;
        background: #0d1011;
      }}
      canvas {{
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
      }}
      #delta_corner_logo {{
        position: fixed;
        top: 9px;
        left: 10px;
        z-index: 2;
        width: 30px;
        height: 30px;
        object-fit: contain;
        pointer-events: none;
      }}
      #delta_loader,
      #delta_error {{
        position: fixed;
        inset: 0;
        display: grid;
        place-items: center;
        color: #cad8de;
        background: #0d1011;
        padding: 24px;
        font: 500 14px/1.4 Inter, system-ui, sans-serif;
      }}
      #delta_error {{
        display: none;
        color: #fff;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        white-space: pre-wrap;
      }}
    </style>
    <script src="/re_viewer.js"></script>
  </head>
  <body>
    <canvas id="the_canvas_id"></canvas>
    <img id="delta_corner_logo" src="/delta-icon.png" alt="" />
    <div id="delta_loader">Loading {title}...</div>
    <pre id="delta_error"></pre>
    <script>
      const route = {route};
      const sourceUrls = {source_urls};
      const renderer = {renderer};
      const theme = {theme};
      window.deltaFramePolicy = {{
        mode: "live_rerun",
        stopped: false,
      }};

      function installLayoutSaveBridge(route) {{
        const originalCreateObjectURL = URL.createObjectURL.bind(URL);
        const blobByUrl = new Map();
        const persistedBlobs = new WeakSet();

        async function persistLayout(blob, name) {{
          if (!name.endsWith(".rbl") || persistedBlobs.has(blob)) {{
            return;
          }}
          if (blob.size < 1024 || blob.size > 5 * 1024 * 1024) {{
            return;
          }}

          persistedBlobs.add(blob);
          const response = await fetch(
            `/save-layout?route=${{encodeURIComponent(route)}}&name=${{encodeURIComponent(name)}}`,
            {{
              method: "POST",
              headers: {{
                "Content-Type": "application/octet-stream",
                "X-Delta-Layout-Name": name,
              }},
              body: blob,
            }},
          );

          if (!response.ok) {{
            throw new Error(`Failed to persist Delta layout: ${{response.status}} ${{response.statusText}}`);
          }}

          window.deltaLastLayoutSave = await response.json();
          console.info("Persisted Delta layout", window.deltaLastLayoutSave);
        }}

        function maybePersist(anchor) {{
          const name = anchor.download || "";
          const blob = blobByUrl.get(anchor.href);
          if (blob) {{
            persistLayout(blob, name).catch((error) => console.error(error));
          }}
        }}

        URL.createObjectURL = function deltaCreateObjectURL(blob) {{
          const url = originalCreateObjectURL(blob);
          if (blob instanceof Blob && blob.type === "application/octet-stream") {{
            blobByUrl.set(url, blob);
          }}
          return url;
        }};

        const hrefDescriptor = Object.getOwnPropertyDescriptor(HTMLAnchorElement.prototype, "href");
        if (hrefDescriptor && hrefDescriptor.get && hrefDescriptor.set) {{
          Object.defineProperty(HTMLAnchorElement.prototype, "href", {{
            configurable: true,
            enumerable: hrefDescriptor.enumerable,
            get: hrefDescriptor.get,
            set(value) {{
              hrefDescriptor.set.call(this, value);
              maybePersist(this);
            }},
          }});
        }}

        const downloadDescriptor = Object.getOwnPropertyDescriptor(HTMLAnchorElement.prototype, "download");
        if (downloadDescriptor && downloadDescriptor.get && downloadDescriptor.set) {{
          Object.defineProperty(HTMLAnchorElement.prototype, "download", {{
            configurable: true,
            enumerable: downloadDescriptor.enumerable,
            get: downloadDescriptor.get,
            set(value) {{
              downloadDescriptor.set.call(this, value);
              maybePersist(this);
            }},
          }});
        }}
      }}

      function showError(error) {{
        console.error(error);
        document.getElementById("delta_loader").style.display = "none";
        const errorBox = document.getElementById("delta_error");
        errorBox.textContent = String(error && error.stack ? error.stack : error);
        errorBox.style.display = "grid";
      }}

      async function start() {{
        try {{
          installLayoutSaveBridge(route);
          await wasm_bindgen("/re_viewer_bg.wasm");
          const handle = new wasm_bindgen.WebHandle({{
            url: sourceUrls,
            render_backend: renderer,
            hide_welcome_screen: true,
            enable_history: false,
            theme,
          }});
          await handle.start(document.getElementById("the_canvas_id"));
          window.deltaRerunHandle = handle;
          document.getElementById("delta_loader").style.display = "none";
          setInterval(() => {{
            if (handle.has_panicked()) {{
              showError(`Delta ${{route}} viewer crashed:\\n${{handle.panic_message()}}`);
            }}
          }}, 1000);
        }} catch (error) {{
          showError(error);
        }}
      }}

      start();
    </script>
  </body>
</html>
"""


def ensure_layout_files(root: Path) -> None:
    layouts = root / "out" / "layouts"
    layouts.mkdir(parents=True, exist_ok=True)
    for layout_name in LAYOUT_BY_ROUTE.values():
        target = layouts / layout_name
        source = root / "out" / "blueprints" / layout_name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--ports", default="9101,9102")
    parser.add_argument("--asset-base-url", default="http://127.0.0.1:9090")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.root.resolve()
    ensure_layout_files(root)

    servers: list[DeltaEntryServer] = []
    for port_text in args.ports.split(","):
        port = int(port_text.strip())
        server = DeltaEntryServer(
            (args.bind, port),
            DeltaEntryHandler,
            root=root,
            asset_base_url=args.asset_base_url,
        )
        thread = threading.Thread(target=server.serve_forever, name=f"delta-entry-{port}", daemon=True)
        thread.start()
        servers.append(server)
        print(f"Delta {ROUTE_BY_PORT.get(port, 'screen1')} entry: http://{socket.gethostname()}:{port}/")

    try:
        threading.Event().wait()
    finally:
        for server in servers:
            server.shutdown()


if __name__ == "__main__":
    main()
