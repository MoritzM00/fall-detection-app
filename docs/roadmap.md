# Implementation roadmap

Implementation started with a local clip-analysis vertical slice. Milestones below remain the delivery sequence.

## 0. Resolve local contract and baseline

Confirm reference clips, prompt preset, generation defaults, local request/response contract, and mock scenarios. Choose supported upload formats and initial limits.

Done when the local development plan is reproducible without GPU access. Record unknown GPU environment details for the later integration gate; adapter availability is not a blocker.

## 1. Establish local mock HTTP serving — MVP implemented

Connect the planned inference client to a local mock service with the selected serving contract. Add deterministic valid responses, delays, transient failures, and invalid-output scenarios. Preserve the real preprocessing and parsing path.

Done when requests and response/error handling work locally without GPU dependencies, and every mock result carries simulated provenance.

## 2. Complete clip analysis flow — in progress

Introduce media storage, configuration snapshots, persisted jobs, worker processing, API status events, and a minimal upload/result interface. Include sampled-frame preview and raw response.

Done when an uploaded clip can be analyzed and every result can be traced to its input/configuration, including after a page reload or transient disconnect.

## 3. Add debugging and comparison

Expose prompt presets/editing and supported inference/preprocessing settings. Preserve independent runs and compare the same input under different configurations.

Done when configuration changes are visible and previous results remain reproducible and unchanged.

## 4. Add monitoring replay

Create session scheduling, overlapping windows, playback alignment, bounded pending work, generation tracking, latest-result selection, and skipped-coverage reporting.

Done when sustained replay has measured processing lag and handles seek, stop/restart, slow inference, and out-of-order completion correctly.

Milestones 2–4 can run entirely against mock serving. Their timings are simulated, not hardware performance measurements.

## 4a. Validate real GPU integration

Confirm GPU hardware, vLLM version, SSH/network access, and serving configuration. Switch the same worker client to base Qwen3-VL-8B-Instruct. Compare fixed reference inputs against in-process research inference, preserving selected frames, prompt, resizing, and temporal metadata. Investigate differences rather than assuming equivalence.

Done when real-model results, input parity, failure handling, and measured latency/capacity are established. This gate can happen earlier when GPU access is convenient; it does not block local application development, but is required before model-quality evaluation.

## 5. Evaluate continuous behavior

Measure per-class performance, fall/fallen errors, event-level missed falls, false alarms per hour, detection delay, and unprocessed coverage. Include long negatives, falls crossing boundaries, crop-edge subjects, and multiple people. Keep tuning and final evaluation data separate.

Done when limitations and numerical acceptance targets for the intended setting are documented and assessed. Targets are not yet agreed.

## 6. Connect camera and prepare a pilot

Choose camera protocol and preview transport; add ingestion/reconnection. Design event aggregation and any human review/notification workflow. Define access, retention/deletion, operational monitoring, and deployment ownership.

Done when the intended pilot's explicit acceptance criteria are met. No real-world readiness claim follows from clip-level accuracy alone.
