# LR-193 native Rerun delivery

This delivery uses the Rerun viewer as the primary display surface.
It does not ship or start the previous `delta_layout_entry_server.py` HTML wrapper.

## Artifacts

- `out/default_lr193.rrd` is the generated Rerun recording.
- `out/layouts/screen1.rbl` opens the two-camera screen.
- `out/layouts/screen2.rbl` opens the telemetry, top-down Position XY, logo, and DOHC DECK screen.
- `out/fake_lr193_summary.json` records the fake backend contract and the real-data replacement boundary.
- `assets/icon.png` is the user-provided logo source.

The only Rerun viewer branding change in this checkout is the top-left menu logo replacement in `crates/viewer/re_viewer/src/ui/rerun_menu.rs`.
The logo asset is embedded from `crates/viewer/re_ui/data/icons/delta_logo.png`.

## Native viewer commands

Serve the patched Rerun web viewer bundle with the generated Rerun artifacts exposed under `/lr193`:

```bash
python3 dohc-two-monitor-demo/serve_web_viewer_gzip.py \
  --directory web_viewer \
  --asset-root /lr193=dohc-two-monitor-demo/out \
  --port 9101
```

Use the same command with `--port 9102` for the second screen.

The local URLs are:

- `http://127.0.0.1:9101?url=http%3A%2F%2F127.0.0.1%3A9101%2Flr193%2Fdefault_lr193.rrd&url=http%3A%2F%2F127.0.0.1%3A9101%2Flr193%2Flayouts%2Fscreen1.rbl&renderer=webgl`
- `http://127.0.0.1:9102?url=http%3A%2F%2F127.0.0.1%3A9102%2Flr193%2Fdefault_lr193.rrd&url=http%3A%2F%2F127.0.0.1%3A9102%2Flr193%2Flayouts%2Fscreen2.rbl&renderer=webgl`

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
- `/screen2/charts/velocity_xyz/vx`
- `/screen2/charts/velocity_xyz/vy`
- `/screen2/charts/velocity_xyz/vz`
- `/screen2/charts/angular_velocity_xyz/wx`
- `/screen2/charts/angular_velocity_xyz/wy`
- `/screen2/charts/angular_velocity_xyz/wz`
- `/screen2/position_xy/trajectory`
- `/screen2/position_xy/origin`
- `/screen2/position_xy/current`
- `/screen2/deck_indicator`
- `/screen2/delta_logo`
- `/screen2/t265`

When real data arrives, replace the fake image and pose inputs inside `generate_fake_lr193_rerun.py` while preserving those entity paths and the two layout files.
The XY samples feed the native 2D spatial trajectory and current-position entities instead of a time-series plot.
The native Rerun viewer deployment does not require a wrapper change for that swap.

## Remote asset serving

The native viewer assets can be served with precompressed WASM support:

```bash
gzip -kf -9 web_viewer/re_viewer_bg.wasm
python3 dohc-two-monitor-demo/serve_web_viewer_gzip.py \
  --directory web_viewer \
  --asset-root /lr193=dohc-two-monitor-demo/out \
  --port 9101
```

Use the same command with `--port 9102` for the second screen.
This is still the Rerun web viewer bundle, not a wrapper UI.

## Convenience launcher

```bash
python3 dohc-two-monitor-demo/run_native_rerun_viewers.py
```

Set `PYTHON=/path/to/python` to choose the Python interpreter.
Use `--dry-run` to print the exact commands without starting processes.

## Deprecated URLs

The old deployed wrapper URLs on `9101/9102` served a custom static HTML display.
They are not part of this native Rerun delivery and should be considered disabled or stale.
