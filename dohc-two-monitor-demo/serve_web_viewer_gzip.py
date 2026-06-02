#!/usr/bin/env python3
"""Serve Rerun web viewer assets with precompressed WASM support."""

from __future__ import annotations

import argparse
import http.server
import os
import posixpath
from pathlib import Path
from urllib.parse import unquote, urlsplit


class GzipStaticHandler(http.server.SimpleHTTPRequestHandler):
    asset_roots: dict[str, Path] = {}

    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".wasm": "application/wasm",
    }

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def send_head(self):
        if self.path_has_gzip_wasm():
            return self.send_gzip_wasm()
        return super().send_head()

    def path_has_gzip_wasm(self) -> bool:
        path = self.translate_path(self.path)
        return (
            path.endswith(".wasm")
            and Path(path + ".gz").is_file()
            and "gzip" in self.headers.get("Accept-Encoding", "")
        )

    def send_gzip_wasm(self):
        path = self.translate_path(self.path) + ".gz"
        file_handle = open(path, "rb")
        stat = os.fstat(file_handle.fileno())
        self.send_response(200)
        self.send_header("Content-type", "application/wasm")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        return file_handle

    def translate_path(self, path: str) -> str:
        request_path = unquote(urlsplit(path).path)
        for prefix, root in self.asset_roots.items():
            if request_path == prefix or request_path.startswith(prefix + "/"):
                relative = posixpath.normpath(request_path.removeprefix(prefix).lstrip("/"))
                if relative in ("", "."):
                    return str(root)
                if relative == ".." or relative.startswith("../"):
                    return str(root / "__forbidden__")
                return str(root / relative)
        return super().translate_path(request_path)


def parse_asset_root(value: str) -> tuple[str, Path]:
    try:
        prefix, directory = value.split("=", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--asset-root must be URL_PREFIX=DIR") from exc
    if not prefix.startswith("/"):
        raise argparse.ArgumentTypeError("asset root URL_PREFIX must start with /")
    path = Path(directory)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"asset root directory does not exist: {path}")
    return prefix.rstrip("/"), path.resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--asset-root",
        action="append",
        default=[],
        type=parse_asset_root,
        help="Serve an extra directory at URL_PREFIX, for example /lr193=dohc-two-monitor-demo/out",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    handler_class = type(
        "ConfiguredGzipStaticHandler",
        (GzipStaticHandler,),
        {"asset_roots": dict(args.asset_root)},
    )
    handler = lambda *handler_args, **handler_kwargs: handler_class(
        *handler_args,
        directory=str(args.directory),
        **handler_kwargs,
    )
    with http.server.ThreadingHTTPServer((args.bind, args.port), handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
