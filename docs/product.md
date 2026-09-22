# Product behavior

## Clip analysis

- Upload a supported video and inspect duration and dimensions.
- Select an analysis time range; exact treatment of ranges longer than the baseline window remains to be decided.
- Preview sampled frames and timestamps, including the center crop.
- Choose a served model, generation settings, prompt preset/editable prompt, and preprocessing settings.
- Show the fully resolved prompt before running.
- Display label, raw response, processing time, status, and configuration snapshot.
- Rerun with changed settings without overwriting the previous run.
- Compare runs on the same selected input; highlight configuration differences.

Editable experiments are allowed, but thesis defaults form a named reproducible preset. Do not present edited prompts or preprocessing as equivalent to the validated baseline.

## Monitoring

- Start/stop a recorded video replay; real cameras follow later.
- Show playback, latest timestamped prediction, prediction history, and processing lag.
- Distinguish queued, processing, failed, skipped, disconnected, and stale states.
- Highlight `fall` and `fallen` while preserving the other activity labels.
- Make clear that a prediction refers to an earlier input window and, under the baseline prompt, its first part.
- Keep session configuration fixed until the user explicitly starts a new configuration segment.

## Scope boundaries

The first version classifies one primary action per clip. It does not provide person tracking or separate predictions for every person. No calibrated confidence percentage is planned. `other` is an activity label, not an uncertainty or system-error indicator.

Automated notifications, incident acknowledgment, multi-camera management, and pilot workflows are deferred. Before a real-setting pilot, define intended users, access controls, retention/deletion, consent and data handling requirements, and operational acceptance criteria.
