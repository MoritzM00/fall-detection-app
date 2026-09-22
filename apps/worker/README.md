# Processing worker

Implemented as a separate Python polling process sharing `fall_detection`. It atomically claims SQLite jobs, sends the vLLM-compatible request, validates the generated label, and persists the result or explicit failure.

Next areas: leases, bounded retries, actual video decoding/preprocessing, and monitoring window scheduling.

Must preserve input/configuration identity, avoid duplicate results, bound monitoring backlog, and prevent expired playback generations from updating current state.
