# Prediction package

The implemented shared package is [`../../src/fall_detection`](../../src/fall_detection). The original planned boundaries map there as follows:

| Module | Boundary |
| --- | --- |
| taxonomy | Canonical labels and display names |
| sampling/media | Deterministic timestamps and media transport; real decoding/crop remains next |
| prompts | Presets, edits, resolved prompt construction |
| inference | vLLM HTTP transport and generation settings |
| parsing | Validated label extraction and explicit parse failures |
| pipeline | One input/configuration to one validated prediction result |

This package should not depend on the frontend, web routes, database models, or thesis experiment orchestration. Storage and persistence belong to application components.
