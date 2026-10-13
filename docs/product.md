# Product behavior

## Clip analysis — implemented local MVP

- Upload a supported video and inspect duration.
- Select a sampled analysis window of up to 30 seconds within the clip; changing FPS or frame count remains an experiment within this hard limit.
- Preview sampled frames and timestamps, including the center crop.
- Settings are grouped into preprocessing (frames, FPS, crop), experiment (served model, prompt strategy, exact prompt text) and LLM sampling (temperature, top-p, top-k, seed, max tokens, with an estimate of the context window used). The prompt strategy chooses between the research zero-shot and chain-of-thought presets, each with its research sampling values. Reset restores the research defaults of the chosen strategy. The current deployment advertises one model.
- Show the fully resolved prompt before running. The label is read from the answer after the research answer marker, or from a bare label, as the research parsers do. Answers naming no label or several labels fail explicitly instead of becoming `other`. The result shows the model's reasoning, expanded for chain-of-thought runs, and the answer the label was read from. New runs require at least 16 output tokens, a conservative budget rather than a guarantee against truncation.
- Display label, raw response, processing time, status, and configuration snapshot.
- Rerun with changed settings without overwriting the previous run.
- Compare saved runs for the same source and time window; highlight configuration differences and report whether prepared-frame identity matches. Recent terminal history is limited to 50 runs.

Editable experiments are allowed, but the research presets are named and reproducible. Do not present edited prompts, sampling or preprocessing as equivalent to a research preset.

## Monitoring — proposed, not implemented

- Start/stop a recorded video replay; real cameras follow later.
- Show playback, latest timestamped prediction, prediction history, and processing lag.
- Distinguish queued, processing, failed, skipped, disconnected, and stale states.
- Highlight `fall` and `fallen` while preserving the other activity labels.
- Make clear that a prediction refers to an earlier input window and, under the zero-shot preset, its first part.
- Keep session configuration fixed until the user explicitly starts a new configuration segment.

## Scope boundaries

The first version classifies one primary action per clip. It does not provide person tracking or separate predictions for every person. No calibrated confidence percentage is planned. `other` is an activity label, not an uncertainty or system-error indicator.

Automated notifications, incident acknowledgment, multi-camera management, and pilot workflows are deferred. Before a real-setting pilot, define intended users, access controls, retention/deletion, consent and data handling requirements, and operational acceptance criteria.
