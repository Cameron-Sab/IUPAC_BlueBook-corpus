from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

if __package__:
    from scripts.build_compact_semantic_tasks import (
        DEFAULT_OUTPUT as DEFAULT_TASK_DIR,
        digest_without_field,
        load_json,
    )
    from scripts.compile_semantic_delta import compile_delta
    from scripts.render_compact_semantic_task import validate_task
else:
    from build_compact_semantic_tasks import (  # type: ignore[no-redef]
        DEFAULT_OUTPUT as DEFAULT_TASK_DIR,
        digest_without_field,
        load_json,
    )
    from compile_semantic_delta import compile_delta  # type: ignore[no-redef]
    from render_compact_semantic_task import validate_task  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DELTA_DIR = ROOT / "data" / "bluebook_v3" / "semantic_deltas"


def audit_progress(
    task_dir: Path = DEFAULT_TASK_DIR,
    delta_dir: Path = DEFAULT_DELTA_DIR,
) -> dict[str, Any]:
    manifest = load_json(task_dir / "manifest.json")
    if manifest.get("manifest_sha256") != digest_without_field(
        manifest, "manifest_sha256"
    ):
        raise ValueError("Compact task manifest hash is invalid")
    expected_entries = manifest["tasks"]
    expected_ids = [entry["task_id"] for entry in expected_entries]
    expected_set = set(expected_ids)
    delta_paths = {
        path.stem: path for path in delta_dir.glob("*.json")
    } if delta_dir.exists() else {}
    unexpected = sorted(set(delta_paths).difference(expected_set))
    if unexpected:
        raise ValueError(f"Unexpected semantic delta files: {unexpected}")

    completed = []
    missing = []
    invalid = []
    completed_rules = 0
    completed_clauses = 0
    for entry in expected_entries:
        task_id = entry["task_id"]
        task = load_json(task_dir / f"{task_id}.json")
        validate_task(task)
        if task["task_sha256"] != entry["task_sha256"]:
            raise ValueError(f"Task differs from manifest: {task_id}")
        delta_path = delta_paths.get(task_id)
        if delta_path is None:
            missing.append(task_id)
            continue
        try:
            _chunk, result = compile_delta(load_json(delta_path), task)
        except (KeyError, TypeError, ValueError) as error:
            invalid.append({"task_id": task_id, "error": str(error)})
            continue
        if not result["passed"]:
            invalid.append(
                {
                    "task_id": task_id,
                    "error": "compiled chunk failed strict validation",
                    "errors": result["errors"],
                }
            )
            continue
        completed.append(task_id)
        completed_rules += len(task["assigned_rule_ids"])
        completed_clauses += task["metrics"]["clause_count"]

    return {
        "format": "iupac-bluebook-semantic-delta-progress",
        "format_version": "1.0.0",
        "complete": not missing and not invalid,
        "passed": not invalid,
        "task_manifest_sha256": manifest["manifest_sha256"],
        "expected_task_count": len(expected_ids),
        "completed_task_count": len(completed),
        "missing_task_count": len(missing),
        "invalid_task_count": len(invalid),
        "completed_rule_count": completed_rules,
        "expected_rule_count": manifest["assigned_rule_count"],
        "completed_clause_count": completed_clauses,
        "expected_clause_count": manifest["assigned_clause_count"],
        "completed_task_ids": completed,
        "missing_task_ids": missing,
        "invalid_tasks": invalid,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit compact semantic-delta conversion progress"
    )
    parser.add_argument("--task-dir", type=Path, default=DEFAULT_TASK_DIR)
    parser.add_argument("--delta-dir", type=Path, default=DEFAULT_DELTA_DIR)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = audit_progress(args.task_dir, args.delta_dir)
    except (KeyError, TypeError, ValueError, OSError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1
    full_rendered = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(full_rendered)
    displayed = report if args.verbose else {
        key: value
        for key, value in report.items()
        if key not in {"completed_task_ids", "missing_task_ids", "invalid_tasks"}
    }
    if not args.verbose and report["invalid_tasks"]:
        displayed["invalid_tasks"] = report["invalid_tasks"]
    print(json.dumps(displayed, indent=2, ensure_ascii=False))
    if not report["passed"]:
        return 1
    if args.require_complete and not report["complete"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
