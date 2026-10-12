# Managed media retention

The application keeps uploaded media, prepared frame bundles and live camera frames by default. Maintenance is manual and optional; nothing is deleted automatically. This is proposed application behavior, not verified research behavior. Preview candidates first:

```sh
.venv/bin/python scripts/prune_media.py
```

The command reads `FALL_DETECTION_DATA_DIR` and `FALL_DETECTION_DATABASE_PATH` using the same settings as the API. Its default minimum age is 168 hours, and its default age for ended monitoring sessions is 30 days. To choose longer ages and then remove exactly the resources that remain eligible at apply time:

```sh
.venv/bin/python scripts/prune_media.py --min-age-hours 720 --session-age-days 90
.venv/bin/python scripts/prune_media.py --min-age-hours 720 --session-age-days 90 --apply
```

`--keep-sessions` leaves all monitoring session data in place, which restores the behavior before ended-session pruning. The preview lists each eligible resource with its file count and size, one summary line per ended session (prepared bundles and live frames with their sizes), a warning for each eligible session with real (vLLM) predictions, and the total.

## Unreferenced uploads and bundles

Only direct, regular, UUID-named upload files in `media/` and hash-named prepared bundles in `prepared/` are considered. Every file in a bundle, the bundle directory, and the upload database record must be older than the minimum age. The command protects resources referenced by **any** retained clip job, regardless of state, including failed jobs that can be retried. A fresh or unrecognized bundle prevents removal of a source upload it might use. Symlinks, outside-root paths, dataset videos, and unrecognized files are never removed. The maintenance lock coordinates with API uploads, job creation, frame ingest and preparation; the database write transaction prevents job references being added during apply. Candidates are checked again before removal.

## Ended monitoring sessions

A monitoring session's data becomes eligible once the session is **stopped** and its last lifecycle change is older than `--session-age-days`. For such a session the command removes:

- prepared bundles that only its windows and their jobs reference, and
- the raw live frame log `live/<source id>/` of a live session.

It never touches:

- data of running or paused sessions, however old;
- inputs of windows that are still `pending` or `preparing`, or whose job is queued or running;
- bundles also referenced by a clip job or by another session that is not eligible;
- recorded sources (uploads) of any retained session, because a stopped session can still be restarted.

Every removed file must also be older than `--min-age-hours`. A frame log containing anything but regular files (subdirectories or symlinks) is left alone. Monitoring jobs cannot be retried, so a finished window never needs its input again.

Provenance stays in the database: windows and jobs keep their prepared input IDs, configurations keep their preprocessing settings, and the `live_frames` rows keep each frame's sequence number, capture time and SHA-256. Run and session exports then report `input_status: unavailable` for a pruned input. This is permanent for evaluation: a pruned session can no longer feed a real [evaluation](evaluation.md) report, which requires online provenance and `input_status: recorded`, and the bundles cannot be recovered. An export taken before pruning keeps the manifests and still qualifies, so export the session ([exports](exports.md)) before `--apply`, or use `--keep-sessions` while evaluation is pending. The preview prints a warning for each eligible session with real (vLLM) predictions; mock-only sessions are not flagged. With `--apply`, the warning instead names each such session whose inputs were removed. Because the frame-log rows remain, a restarted live session continues the sequence numbering, and preparation that would need a pruned frame fails with a reason instead of reading other pixels. Removal runs under the exclusive storage lock, which frame ingest and preparation also take (shared), so no frame is read or written while it is deleted.

## RGB arrays in prepared bundles

New bundles store the transport JPEGs and the manifest only. The raw cropped RGB stack (`frames.npy`) is written only when `FALL_DETECTION_RGB_ARRAYS=1`, for research-parity checks (#41). `bundle_sha256` is still computed over the raw pixel stack, so a parity check can recompute it without the stored array. Before transport, each JPEG is checked against its manifest `jpeg_sha256`; a stored array (older bundles, or the flag on) is verified as well.

The flag is a storage option, not part of the content-derived bundle ID. A bundle that already exists is reused unchanged: one prepared without the array does not gain it when the flag is later turned on, and one prepared with it keeps it, and keeps verifying it, when the flag is off. To obtain arrays for a parity check, prepare into a fresh data directory with the flag on.

### Measured sizes

Per-window bundle sizes, measured on 12 October 2026 with generated, non-sensitive 1280×720 H.264 clips (ffmpeg `testsrc2` and `mandelbrot`, CRF 23), `prepare_video` at 7.5 fps from 1 s, without inspection PNGs:

| Window | Before (with `frames.npy`) | After (default) | of which RGB array |
|---|---|---|---|
| 16 frames × 448² | 10.1–10.6 MB | 0.46–0.94 MB | 9.6 MB |
| 32 frames × 672² | 45.1–47.3 MB | 1.7–3.9 MB | 43.4 MB |

The JPEG share depends on image content (the range spans the two sources); the array size does not. At the default 2 s monitoring stride (1,800 windows per hour) the default window drops from about 18–19 GB to about 0.8–1.7 GB per hour of monitoring. Raw live frames are separate: generated 1280×720 frames at JPEG quality 85 (the browser's setting) average 62–93 kB, about 3.3–5.0 GB per hour at 15 fps. Real camera footage may differ.

## Applying and recovery

Applying removal deletes eligible prepared bundles and live frame logs, and unreferenced uploaded video records with their media files. It does not delete jobs, predictions, sessions, windows or frame-log rows. Deleting run history separately removes its protection: a later prune can then delete that run's source and prepared frames once they meet the age threshold. A video record without a retained job or session disappears from video history after removal; the underlying frames and upload cannot be recovered through this application. Back up the data directory and database according to your normal policy before applying cleanup. A file deletion interrupted after its database record is removed can leave an orphan upload, which a later run can remove. The command does not touch `omnifall/` or other datasets.

Run the command against a stopped application if any process accesses managed media without the repository's storage lock. The maintenance command itself does not run automatically. Its test suite uses disposable storage fixtures and generated frames; no user media was cleaned during development.
