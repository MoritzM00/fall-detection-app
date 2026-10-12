# Preparation benchmark

## Seek before decoding (2026-10-12)

This section records proposed application behavior, not research-verified behavior. Issue #70 found that replay windows late in long recordings expired: every window decoded the source from its first frame, so preparation time grew with window position (11.99 s at 590 s of a 10-minute 720p30 clip) and exceeded the 5 s monitoring expiration. Preparation now seeks to a keyframe before the window (see [prepared-video.md](prepared-video.md)) and caches the source SHA-256 per worker process.

Run `.venv/bin/python scripts/benchmark_preparation.py --long` from the repository root. `--long` adds a generated 10-minute 1280×720, 30 FPS H.264 `testsrc2` clip (412.4 MB, FFmpeg's default 250-frame GOP) and prepares causal replay windows (16 frames, 7.5 FPS, 448 pixels) at 10, 120, 300 and 590 s. Every persisted PTS and RGB frame hash is compared with a fresh full-scan selection (`seek=False`), which decodes from the first frame. Generated media lives in a temporary directory removed afterwards.

Measured on 12 October 2026 on an Apple M3 Pro, 18 GiB RAM, macOS 15.8.1 arm64, Python 3.12.12, FFmpeg 9.0.2. One run, not a latency bound.

| Window start | Before: `prepare_video(causal=True)` (issue #70) | Full-scan decode/select | Seek decode/select | After: `prepare_video(causal=True)` |
| ---: | ---: | ---: | ---: | ---: |
| 10 s | 0.77 s | 0.223 s | 0.070 s | 0.327 s (includes 0.158 s first source hash) |
| 120 s | 2.90 s | 2.196 s | 0.099 s | 0.193 s |
| 300 s | 6.36 s | 5.370 s | 0.189 s | 0.284 s |
| 590 s | 11.99 s | 10.382 s | 0.155 s | 0.250 s |

The "before" column is the issue's measurement (same machine model, 566 MB clip). The two decode/select columns were measured on the same generated clip in one process. Seek cost is bounded by GOP length plus window length, not by position. After the first window, the source hash costs nothing; the remaining hash time is JPEG hashing (about 0.001 s). Peak RSS for the long profile was 187.0 MiB.

The shorter profiles below, re-run with seeking on the same machine:

| Input | Window start | Total s | SHA-256 s | Decode/select s | Crop s | Other s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1280×720, 60 s, 15 FPS | 1 s | 0.147 | 0.011 | 0.038 | 0.075 | 0.023 |
| same | 56 s | 0.185 | 0.001 | 0.090 | 0.076 | 0.018 |
| same, rapid change | 30 s | 0.250 | 0.001 | 0.157 | 0.074 | 0.019 |
| 1920×1080, 120 s, 10 FPS | 1 s | 0.256 | 0.031 | 0.053 | 0.147 | 0.025 |
| same | 116 s | 0.420 | 0.001 | 0.258 | 0.142 | 0.020 |
| same, rapid change | 60 s | 0.346 | 0.001 | 0.178 | 0.149 | 0.019 |

Correctness: all selected PTS and frame hashes matched the full scan for every benchmark profile. `tests/test_preparation_seek.py` repeats the comparison, nearest and causal, on short generated clips: H.264 MP4 with B-frames, H.264 open-GOP MKV, VP9 WebM, and variable-frame-rate H.264 MP4 (20–110 ms frame intervals). Windows cover start 0, windows too early to seek, windows exactly on a keyframe, half a frame before a keyframe, exactly on a frame, and the last window in the stream, including a last timestamp inside the final frame's extent. Outputs are byte-identical, so `PREPROCESSING_VERSION` stays `pyav-pillow-online-jpeg-v4`. The same file also tests a forced late seek landing (fallback to full scan) and a late replay window under a controlled clock, which is submitted with seeking and expires with the old full scan.

Limits: generated `testsrc2` content, one machine, and codecs available to the local FFmpeg build. Container seeking depends on an index (MP4 `stss`, Matroska cues). Files without one may seek less precisely; the checks above then fall back to the full scan, which is correct but slow again.

## Earlier full-scan benchmark (2026-09-25)

### Procedure

Run `.venv/bin/python scripts/benchmark_preparation.py` from the repository root with FFmpeg installed. The script creates disposable H.264 `testsrc2` clips, runs each profile in a fresh Python process, prints JSON results, and removes generated media afterward. No private video or benchmark output file is committed. It prepares 16 frames at 7.5 FPS and 448 pixels, with inspection PNGs disabled, for early and late windows, a repeated late-window cache hit, and three rapidly changed windows (early, middle, late). Timing uses `time.perf_counter`; phase wrappers measure full-file SHA-256 calls, frame decode and nearest-PTS selection, and crop. The remaining time includes JPEG/array writes, manifest work, and overhead. Peak RSS is measured before the correctness pass, per profile process, via `resource.getrusage`; it includes the Python process baseline. After measurement, the script compares every persisted source PTS and RGB frame SHA-256 with a fresh full-scan nearest-frame selection. The cache hit must return the same prepared ID and hashes as the preceding late window.

Measured on an Apple M3 Pro, 18 GiB RAM, macOS 15.7.3 arm64, Python 3.12.14, FFmpeg 9.0.1, using the repository's pinned Python environment. Results below are one run, not statistical latency bounds. Generated `testsrc2` compresses differently from camera footage.

| Input | Window start | Total s | SHA-256 s | Decode/select s | Crop s | Other s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1280×720, 60 s, 15 FPS, 27.9 MB | 1 s | 0.205 | 0.031 | 0.052 | 0.085 | 0.036 |
| same | 56 s | 0.734 | 0.024 | 0.609 | 0.077 | 0.023 |
| same, cache hit | 56 s | 0.013 | 0.012 | 0 | 0 | 0.001 |
| same, rapid change | 2 s | 0.165 | 0.024 | 0.044 | 0.077 | 0.021 |
| same, rapid change | 30 s | 0.443 | 0.023 | 0.323 | 0.076 | 0.021 |
| same, rapid change | 55 s | 0.697 | 0.023 | 0.581 | 0.073 | 0.020 |
| 1920×1080, 120 s, 10 FPS, 80.8 MB | 1 s | 0.286 | 0.064 | 0.051 | 0.142 | 0.029 |
| same | 116 s | 1.903 | 0.069 | 1.645 | 0.160 | 0.029 |
| same, cache hit | 116 s | 0.034 | 0.034 | 0 | 0 | 0 |
| same, rapid change | 2 s | 0.334 | 0.078 | 0.081 | 0.151 | 0.024 |
| same, rapid change | 60 s | 1.091 | 0.066 | 0.865 | 0.139 | 0.022 |
| same, rapid change | 115 s | 1.868 | 0.065 | 1.633 | 0.145 | 0.024 |

Peak process RSS was **212.5 MiB** for the 720p profile and **320.8 MiB** for 1080p. All selected PTS and frame hashes matched the existing full-scan path. The JSON output includes the individual bundle and PTS-list hashes for repeat runs.

### Decision and limits (superseded for seeking on 2026-10-12)

Late-window decode dominates (0.61 s at 720p; 1.65 s at 1080p), while full-file hashing is 0.01–0.08 s for these generated inputs. Keep full source hashing for content identity and change detection. Keep the existing full-scan nearest-PTS selection and synchronous preparation for the currently measured envelope: generated clips up to 1080p, 120 s, 16 frames at 448 pixels. A seek path adds PTS and boundary correctness risk for a measured saving below two seconds here. No seek change was made, so variable-frame-rate and boundary-window behavior remains on the existing selection path. Before extending this decision to longer, larger, or variable-frame-rate recordings, benchmark those inputs and compare PTS and output hashes at early, late, and boundary windows.

The application limits each prepared window to 30 s and the upload body to 512 MiB by default. The supported API deployment is **one API process**, with two simultaneous distinct preparation requests by default and a ten-second queue wait. These are limits on work admitted and coordinated, not a promise of latency for every uploaded file. If real workloads regularly exceed the measured latency or queue wait, an asynchronous preparation lifecycle can be evaluated with representative recordings and user-visible progress.
