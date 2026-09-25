# Preparation benchmark (2026-09-25)

## Procedure

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

## Decision and limits

Late-window decode dominates (0.61 s at 720p; 1.65 s at 1080p), while full-file hashing is 0.01–0.08 s for these generated inputs. Keep full source hashing for content identity and change detection. Keep the existing full-scan nearest-PTS selection and synchronous preparation for the currently measured envelope: generated clips up to 1080p, 120 s, 16 frames at 448 pixels. A seek path adds PTS and boundary correctness risk for a measured saving below two seconds here. No seek change was made, so variable-frame-rate and boundary-window behavior remains on the existing selection path. Before extending this decision to longer, larger, or variable-frame-rate recordings, benchmark those inputs and compare PTS and output hashes at early, late, and boundary windows.

The application limits each prepared window to 30 s and the upload body to 512 MiB by default. The supported API deployment is **one API process**, with two simultaneous distinct preparation requests by default and a ten-second queue wait. These are limits on work admitted and coordinated, not a promise of latency for every uploaded file. If real workloads regularly exceed the measured latency or queue wait, an asynchronous preparation lifecycle can be evaluated with representative recordings and user-visible progress.
