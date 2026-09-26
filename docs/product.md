# Product behavior

## Clip analysis — implemented local MVP

- Upload a supported video and inspect duration.
- Select a sampled analysis window of up to 30 seconds within the clip; longer selections require changed FPS/frame count and remain experiments.
- Preview sampled frames and timestamps, including the center crop.
- Choose the advertised served model, edit temperature/max tokens and prompt text, reset to the baseline prompt/generation settings, and edit sampling settings. The current deployment advertises one model.
- Show the fully resolved prompt before running.
- Display label, raw response, processing time, status, and configuration snapshot.
- Rerun with changed settings without overwriting the previous run.
- Compare saved runs for the same source and time window; highlight configuration differences and report whether prepared-frame identity matches. Recent terminal history is limited to 50 runs.

Editable experiments are allowed, but thesis defaults form a named reproducible preset. Do not present edited prompts or preprocessing as equivalent to the validated baseline.

## Monitoring — proposed, not implemented

- Start/stop a recorded video replay; real cameras follow later.
- Show playback, latest timestamped prediction, prediction history, and processing lag.
- Distinguish queued, processing, failed, skipped, disconnected, and stale states.
- Highlight `fall` and `fallen` while preserving the other activity labels.
- Make clear that a prediction refers to an earlier input window and, under the baseline prompt, its first part.
- Keep session configuration fixed until the user explicitly starts a new configuration segment.

## Scope boundaries

The first version classifies one primary action per clip. It does not provide person tracking or separate predictions for every person. No calibrated confidence percentage is planned. `other` is an activity label, not an uncertainty or system-error indicator.

Automated notifications, incident acknowledgment, multi-camera management, and pilot workflows are deferred. Before a real-setting pilot, define intended users, access controls, retention/deletion, consent and data handling requirements, and operational acceptance criteria.
