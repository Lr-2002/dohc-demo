#!/usr/bin/env python3
"""Serve Rerun web viewer assets with precompressed WASM support."""

from __future__ import annotations

import argparse
import http.server
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit


class GzipStaticHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".wasm": "application/wasm",
    }

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
        path = urlsplit(path).path
        path = unquote(path)
        return super().translate_path(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    handler = lambda *handler_args, **handler_kwargs: GzipStaticHandler(
        *handler_args,
        directory=str(args.directory),
        **handler_kwargs,
    )
    with http.server.ThreadingHTTPServer((args.bind, args.port), handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
