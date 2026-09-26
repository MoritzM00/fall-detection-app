# Current validation and remaining checks

The automated tests cover the complete taxonomy parser, timestamp sampling, durable job lifecycle, lease ownership and retries, concurrent fresh/legacy database startup, API isolation, bounded preparation/uploads, retention, and mock/protocol failures. API-to-worker checks verify saved prompts, model and generation settings reach the inference payload without changing earlier runs. Frontend tests cover recovery and comparison semantics. `make check` runs Python/frontend tests, linting, type checking, formatting verification, and the production build. `make browser-test` runs upload/previews, recovery/errors/retries, prompt/generation editing, reload-safe saved-run comparison, and mobile overflow against disposable local services and a generated video fixture.

Future meaningful checks:

- Extended scripted/concurrent monitoring mock scenarios.
- Reference sampling/frame timestamps and prompt fidelity against thesis behavior.
- Online versus in-process inference on fixed reference inputs.
- Monitoring backlog, skipped coverage, seek/restart generations, and out-of-order results.
- Sustained processing measurements and continuous-video evaluation described in the roadmap.

Use controlled/synthetic fixtures for ordinary tests. Keep research and personal videos outside Git. The opt-in `scripts/validate_vllm_contract.py` exists for the live GPU transport gate; its fake-HTTP self-test is not GPU validation.
