from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.build_compact_semantic_tasks import load_json
    from scripts.render_compact_semantic_task import validate_task
    from scripts.scaffold_semantic_delta import scaffold_delta
else:
    from build_compact_semantic_tasks import load_json
    from render_compact_semantic_task import validate_task
    from scaffold_semantic_delta import scaffold_delta


ROW_FIELDS = {
    "R": ["rule_id", "immediate_parent"],
    "U": [
        "clause_index",
        "clause_id",
        "unit_kind",
        "node_kind",
        "ancestor_node_kinds",
        "semantic_cue",
        "text",
        "payload",
    ],
    "X": [
        "occurrence_id",
        "reference_text",
        "effective_target_rule_id",
        "effective_target_kind",
        "resolution_id",
        "context_text",
    ],
    "K": [
        "overlay_id",
        "operation_id",
        "kind",
        "before_text",
        "after_text",
        "source_text",
        "assets",
    ],
}


def authoring_view_rows(task: dict[str, Any]) -> list[list[Any]]:
    validate_task(task)
    scaffold = scaffold_delta(task)
    prefilled_clauses = {
        item["clause_id"] for item in scaffold["clause_dispositions"]
    }
    prefilled_occurrences = {
        occurrence_id
        for binding in scaffold["citation_bindings"]
        for occurrence_id in binding["occurrence_ids"]
    }
    rows: list[list[Any]] = []
    clause_index = 0
    for rule in task["rules"]:
        rows.append(["R", rule["rule_id"], rule["immediate_parent"]])
        for unit in rule["source_units"]:
            clause_index += 1
            if unit["clause_id"] in prefilled_clauses:
                continue
            rows.append(
                [
                    "U",
                    clause_index,
                    unit["clause_id"],
                    unit["unit_kind"],
                    unit["node_kind"],
                    unit["ancestor_node_kinds"],
                    unit["semantic_cue"],
                    unit["text"],
                    unit["payload"],
                ]
            )
        for reference in rule["references"]:
            if reference["occurrence_id"] in prefilled_occurrences:
                continue
            rows.append(
                [
                    "X",
                    reference["occurrence_id"],
                    reference["reference_text"],
                    reference["effective_target_rule_id"],
                    reference["effective_target_kind"],
                    reference["resolution_id"],
                    reference["context_text"],
                ]
            )
    for correction in task["corrections"]:
        for operation in correction["operations"]:
            rows.append(
                [
                    "K",
                    correction["overlay_id"],
                    operation["operation_id"],
                    operation["kind"],
                    operation["before_text"],
                    operation["after_text"],
                    operation["source_text"],
                    operation["assets"],
                ]
            )
    return rows


def authoring_view_bytes(task: dict[str, Any]) -> bytes:
    scaffold = scaffold_delta(task)
    clause_count = sum(len(rule["source_units"]) for rule in task["rules"])
    citation_count = sum(len(rule["references"]) for rule in task["rules"])
    header = {
        "format": "iupac-bluebook-semantic-authoring-view",
        "format_version": "1.0.0",
        "task_id": task["task_id"],
        "task_sha256": task["task_sha256"],
        "clause_count": clause_count,
        "mechanical_clause_indexes": [
            index
            for index, clause_id in enumerate(
                [
                    unit["clause_id"]
                    for rule in task["rules"]
                    for unit in rule["source_units"]
                ],
                1,
            )
            if clause_id
            in {item["clause_id"] for item in scaffold["clause_dispositions"]}
        ],
        "citation_count": citation_count,
        "mechanical_occurrence_ids": [
            occurrence_id
            for binding in scaffold["citation_bindings"]
            for occurrence_id in binding["occurrence_ids"]
        ],
        "row_fields": ROW_FIELDS,
    }
    lines = [header, *authoring_view_rows(task)]
    return b"".join(
        (
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        for value in lines
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render only evidence unresolved by the mechanical semantic scaffold"
    )
    parser.add_argument("task", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    task = load_json(args.task)
    rendered = authoring_view_bytes(task)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(rendered)
        print(
            json.dumps(
                {"task_id": task["task_id"], "output": str(args.output), "bytes": len(rendered)},
                indent=2,
            )
        )
    else:
        sys.stdout.buffer.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
