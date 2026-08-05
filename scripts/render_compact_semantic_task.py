from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator, FormatChecker

if __package__:
    from scripts.build_compact_semantic_tasks import (
        ROOT,
        SCHEMA_PATH,
        digest_without_field,
        load_json,
        model_view_bytes,
    )
else:
    from build_compact_semantic_tasks import (  # type: ignore[no-redef]
        ROOT,
        SCHEMA_PATH,
        digest_without_field,
        load_json,
        model_view_bytes,
    )


DEFAULT_TASK_DIR = ROOT / "work" / "compact_semantic_tasks"


def validate_task(task: dict[str, Any]) -> None:
    schema = load_json(SCHEMA_PATH)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(task), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        raise ValueError(
            f"Task fails schema at /{'/'.join(map(str, first.path))}: {first.message}"
        )
    if task["task_sha256"] != digest_without_field(task, "task_sha256"):
        raise ValueError("Task SHA-256 does not reproduce from task content")


def select_rules(task: dict[str, Any], rule_ids: Sequence[str]) -> dict[str, Any]:
    if not rule_ids:
        return task
    requested = set(rule_ids)
    available = {rule["rule_id"] for rule in task["rules"]}
    missing = requested.difference(available)
    if missing:
        raise ValueError(f"Rules are not assigned to this task: {sorted(missing)}")
    selected = dict(task)
    selected["rules"] = [
        rule for rule in task["rules"] if rule["rule_id"] in requested
    ]
    selected_corrections = {
        overlay_id
        for rule in selected["rules"]
        for overlay_id in rule["correction_overlay_ids"]
    }
    selected_corrections.update(
        reference["correction_overlay_id"]
        for rule in selected["rules"]
        for reference in rule["references"]
        if reference["correction_overlay_id"] is not None
    )
    selected["corrections"] = [
        correction
        for correction in task["corrections"]
        if correction["overlay_id"] in selected_corrections
    ]
    return selected


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a verified compact task as token-efficient JSON Lines"
    )
    parser.add_argument("task", type=Path)
    parser.add_argument("--rule", action="append", default=[])
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    task = load_json(args.task)
    validate_task(task)
    rendered = model_view_bytes(select_rules(task, args.rule))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(rendered)
        print(
            json.dumps(
                {
                    "task_id": task["task_id"],
                    "task_sha256": task["task_sha256"],
                    "output": str(args.output),
                    "bytes": len(rendered),
                },
                indent=2,
            )
        )
    else:
        sys.stdout.buffer.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
