# Validation plan

The first automated tests cover the complete taxonomy parser, timestamp sampling, durable job lifecycle, mock completion contract, and malformed mock inputs. `make check` also runs linting, type checking, formatting verification, and the frontend production build.

Future meaningful checks:

- Mock HTTP contract, deterministic concurrent scenarios, simulated provenance, and endpoint switching without pipeline changes.

- Reference sampling/frame timestamps and prompt fidelity against thesis behavior.
- Valid labels, malformed/ambiguous output, and separation of errors from `other`.
- Online versus in-process inference on fixed reference inputs.
- Job reclaim/retry behavior and idempotent result persistence.
- Upload-to-result flow and reconnect recovery.
- Monitoring backlog, skipped coverage, seek/restart generations, and out-of-order results.
- Sustained processing measurements and continuous-video evaluation described in the roadmap.

Use controlled/synthetic fixtures for ordinary tests. Keep research and personal videos outside Git; GPU checks should be explicit integration tests when implemented.
