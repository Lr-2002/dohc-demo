# LR-193 native Rerun delivery

This delivery uses the Rerun viewer as the primary display surface.
It does not ship or start the previous `delta_layout_entry_server.py` HTML wrapper.

## Artifacts

- `out/default_lr193.rrd` is the generated Rerun recording.
- `out/layouts/screen1.rbl` opens the two-camera screen.
- `out/layouts/screen2.rbl` opens the light telemetry, top-down Position XY, MP4 logo, and right-side dhoc/deck image panes.
- `out/fake_lr193_summary.json` records the fake backend contract and the real-data replacement boundary.
- `assets/08e875350fba3add6ecebe0de7d26021.mp4` is the user-provided middle-logo video source.
- `assets/screen2-right/dhoc_bottom_view.png` and `assets/screen2-right/deck_side.png` are the user-provided Screen 2 right-pane images.

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
pixi run uv run --with rerun-sdk==0.32.2 --with numpy --with pillow --with av \
  python dohc-two-monitor-demo/generate_fake_lr193_rerun.py
```

The middle logo uses the provided MP4 as its source, but logs a sampled Rerun `Image` sequence on `/screen2/delta_logo` for web-viewer compatibility and starts from a visible source-frame offset. Native `AssetVideo` was tested first and hit the deployed browser path's missing WebCodecs `VideoDecoder`; logging every source frame also delayed RRD ingestion in the web viewer before telemetry/right-pane entities became visible.

The generator writes the stable entity paths consumed by the two `.rbl` layouts:

- `/screen1/cam0`
- `/screen1/cam1`
- `/screen2/charts/velocity_xyz/vx`
- `/screen2/charts/velocity_xyz/vy`
- `/screen2/charts/velocity_xyz/vz`
- `/screen2/charts/angular_velocity_xyz/wx`
- `/screen2/charts/angular_velocity_xyz/wy`
- `/screen2/charts/angular_velocity_xyz/wz`
- `/screen2/position_xy/grid`
- `/screen2/position_xy/frame`
- `/screen2/position_xy/axes`
- `/screen2/position_xy/trajectory`
- `/screen2/position_xy/head`
- `/screen2/position_xy/origin`
- `/screen2/position_xy/current`
- `/screen2/right/dhoc_bottom_view`
- `/screen2/right/deck_side`
- `/screen2/delta_logo`
- `/screen2/t265`

When real data arrives, replace the fake image and pose inputs inside `generate_fake_lr193_rerun.py` while preserving those entity paths and the two layout files.
The right-side `dhoc 仰视图` and `deck 侧面` panes are static Rerun `Image` entities sourced from the downloaded Multica attachments and composited against black for contrast.
The XY samples feed the native 2D spatial trajectory and current-position entities instead of a time-series plot.
For the file-URL fake delivery, Position XY is logged as a static age-faded final trajectory snapshot with a 20% alpha floor, avoiding native Spatial2D overdraw across the visible frame range.
The Position XY view uses native `Spatial2D` visual bounds that are symmetric around zero and aspect-matched to the wide pane so the origin stays centered while the grid fills the pane.
It renders a light-blue faded history plus a separate deep-blue current head.
Rerun `LineStrips2D` stroke thickness is controlled by world-unit radii, so the trajectory radius is tuned to approximate the requested 6 px stroke under the generated bounds.
The native Rerun viewer deployment does not require a wrapper change for that swap.

## H5 target-data wrapper

`h5_target_data.py` is the first-stage H5 extraction boundary for real LR-193 data.
It does not modify the viewer, layouts, online service, generated `.rrd`, or generated `.rbl` files.

Inspect a readable H5 and print the four target streams:

```bash
python3 dohc-two-monitor-demo/h5_target_data.py inspect \
  "/tmp/lr193_h5_probe/2026-05-29 10:05:30.h5"
```

Write quick extraction artifacts:

```bash
python3 dohc-two-monitor-demo/h5_target_data.py extract \
  "/tmp/lr193_h5_probe/2026-05-29 10:05:30.h5" \
  --out-dir /tmp/lr193_h5_target_extract \
  --limit 24
```

The script writes `target_numeric.npz`, `target_data_summary.json`, and a few decoded `cam0` JPEG samples.
It fail-fast probes the HDF5 magic header and `h5py.File` open path before inventory, so unreadable `/Users/w/Downloads/*.h5` files report the filesystem/HDF5 access failure instead of hanging.

Target contract:

- `cam0`: source `frame_*/cam0`, target `/screen1/cam0`, encoded as per-frame `uint8` JPEG bytes.
- `XY`: source `frame_*/t265.attrs['pose'].position`, target `/screen2/position_xy/{trajectory,head,current}`, default plane `position[0:2]`.
- `VXYZ`: source `frame_*/t265.attrs['pose'].velocity`, target `/screen2/charts/velocity_xyz/{vx,vy,vz}`.
- `OmegaXYZ`: no direct H5 key in the verified schema, so it is derived from normalized `pose.quaternion` `[x,y,z,w]` frame deltas and reported in radians/second using `--fps`.
  Unwrapped `pose.euler` finite differences are included as a cross-check and fallback if quaternion samples are unavailable.

The next realtime RRD streamer should consume the wrapper output as one shared frame timeline: one backend frame tick logs `cam0`, `XY`, `VXYZ`, and `OmegaXYZ` to the existing entity paths so both Viewer URLs advance in sync.
Right-side images remain static asset entities, while `/screen2/delta_logo` remains a dynamic image sequence.

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
