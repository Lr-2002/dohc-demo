# LR-193 Rerun dual-screen demo

This directory holds the Rerun-native rebuild for the DOHC two-screen demo.

## Generate artifacts

```bash
pixi run uv run --with rerun-sdk --with h5py --with numpy \
  python dohc-two-monitor-demo/generate_lr193_rerun.py \
  /Users/w/default.h5 \
  --icon dohc-two-monitor-demo/assets/delta-icon.png \
  --out dohc-two-monitor-demo/out
```

Outputs:

- `out/default_lr193.rrd`
- `out/layouts/screen1.rbl`
- `out/layouts/screen2.rbl`
- `out/static/cam0.jpg`
- `out/static/cam1.jpg`
- `out/static/delta-icon.png`
- `out/static/t265.jpg`
- `out/static/telemetry.json`
- `out/lr193_dual_screen_summary.json`

## Layout contract

- Screen 1: two large Rerun `Spatial2DView` camera panes, `Cam0` left and `Cam1` right.
- Screen 2: left third is three stacked Rerun `TimeSeriesView` charts, center third is only the Delta logo as a Rerun image view, right third is a T265 image view.
- The Rerun time panel is hidden in both saved layouts.
- The center Delta view and browser chrome use the transparent `delta-icon.png` asset on a light background, not the original corner-badge icon, and do not add a title/subtitle/card/shadow/border around the mark.

The angular velocity chart is derived from finite differences of the H5 pose Euler angles because the source pose payload does not include angular velocity directly.

## Serve two entries

Run the Rerun backend:

```bash
pixi run uv run --with rerun-sdk rerun \
  --bind 0.0.0.0 \
  --web-viewer-port 9090 \
  --port 9876 \
  --serve-web \
  dohc-two-monitor-demo/out/default_lr193.rrd
```

Run the two-entry wrapper:

```bash
python3 dohc-two-monitor-demo/delta_layout_entry_server.py \
  --bind 0.0.0.0 \
  --ports 9101,9102 \
  --asset-base-url http://127.0.0.1:9090
```

Open:

- Screen 1: `http://HOST:9101/?renderer=webgl`
- Screen 2: `http://HOST:9102/?renderer=webgl`

The entry wrapper defaults to a static display mode for low kiosk CPU usage.
The static mode serves pre-generated images and charts from the same H5-derived recording inputs so the browser does not keep a WebGL render loop running.
Append `&live=1` when interactive Rerun navigation is needed.
Use `&freeze=0` as a compatibility alias for the same live mode.
