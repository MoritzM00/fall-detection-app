"""Write deterministic evaluation JSON from saved exports and explicit annotations."""

import argparse
import json
from pathlib import Path

from fall_detection.evaluation import evaluate


def _load_json(path: Path) -> dict:
    def unique_keys(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(result, dict):
        raise ValueError("JSON manifest must be an object")
    return result


def main() -> None:
    """Evaluate exports against ground truth and write the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", required=True, type=Path)
    parser.add_argument("--export", required=True, action="append", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--synthetic", action="store_true")
    args = parser.parse_args()
    try:
        if args.output.resolve() in {path.resolve() for path in [args.ground_truth, *args.export]}:
            raise ValueError("output must not overwrite an input manifest")
        truth = _load_json(args.ground_truth)
        exports = [_load_json(path) for path in args.export]
        report = evaluate(truth, exports, synthetic=args.synthetic)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"Evaluation rejected: {exc}\n")


if __name__ == "__main__":
    main()
