# LR-193 native Rerun delivery

This delivery uses the Rerun viewer as the primary display surface.
It does not ship or start the previous `delta_layout_entry_server.py` HTML wrapper.

## Artifacts

- `out/default_lr193.rrd` is the generated Rerun recording.
- `out/layouts/screen1.rbl` opens the two-camera screen.
- `out/layouts/screen2.rbl` opens the telemetry and DOHE DECK screen.
- `out/fake_lr193_summary.json` records the fake backend contract and the real-data replacement boundary.
- `assets/icon.png` is the user-provided logo source.

The only Rerun viewer branding change in this checkout is the top-left menu logo replacement in `crates/viewer/re_viewer/src/ui/rerun_menu.rs`.
The logo asset is embedded from `crates/viewer/re_ui/data/icons/delta_logo.png`.

## Native viewer commands

Open screen 1:

```bash
target/debug/rerun \
  --bind 127.0.0.1 \
  --web-viewer-port 9101 \
  --port 9876 \
  --serve-web \
  --renderer webgl \
  dohc-two-monitor-demo/out/default_lr193.rrd \
  dohc-two-monitor-demo/out/layouts/screen1.rbl
```

Open screen 2:

```bash
target/debug/rerun \
  --bind 127.0.0.1 \
  --web-viewer-port 9102 \
  --port 9877 \
  --serve-web \
  --renderer webgl \
  dohc-two-monitor-demo/out/default_lr193.rrd \
  dohc-two-monitor-demo/out/layouts/screen2.rbl
```

The local URLs are:

- `http://127.0.0.1:9101?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9876%2Fproxy&renderer=webgl`
- `http://127.0.0.1:9102?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9877%2Fproxy&renderer=webgl`

Using an unpatched `rerun-sdk` binary will open the same Rerun data, but it will not contain the logo replacement.

## Fake data generation

Regenerate the fake backend recording and the two Rerun layouts with:

```bash
pixi run uv run --with rerun-sdk==0.32.2 --with numpy --with pillow \
  python dohc-two-monitor-demo/generate_fake_lr193_rerun.py
```

The generator writes the stable entity paths consumed by the two `.rbl` layouts:

- `/screen1/cam0`
- `/screen1/cam1`
- `/screen2/charts/velocity_xyz`
- `/screen2/charts/angular_velocity_xyz`
- `/screen2/charts/position_xy`
- `/screen2/deck_indicator`
- `/screen2/delta_logo`
- `/screen2/t265`

When real data arrives, replace the fake image and pose inputs inside `generate_fake_lr193_rerun.py` while preserving those entity paths and the two layout files.
The native Rerun viewer deployment does not require a wrapper change for that swap.

## Remote asset serving

The native viewer assets can be served with precompressed WASM support:

```bash
gzip -kf -9 web_viewer/re_viewer_bg.wasm
python3 dohc-two-monitor-demo/serve_web_viewer_gzip.py --directory web_viewer --port 9101
```

Use the same command with `--port 9102` for the second screen.
This is still the Rerun web viewer bundle, not a wrapper UI.

## Convenience launcher

```bash
python3 dohc-two-monitor-demo/run_native_rerun_viewers.py
```

Set `RERUN_BIN=/path/to/rerun` to use a locally built viewer.
Use `--dry-run` to print the exact commands without starting processes.

## Deprecated URLs

The old deployed wrapper URLs on `9101/9102` served a custom static HTML display.
They are not part of this native Rerun delivery and should be considered disabled or stale.
