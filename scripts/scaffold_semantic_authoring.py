from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.build_compact_semantic_tasks import canonical_json_bytes, load_json
    from scripts.render_compact_semantic_task import validate_task
    from scripts.scaffold_semantic_delta import scaffold_delta
else:
    from build_compact_semantic_tasks import canonical_json_bytes, load_json
    from render_compact_semantic_task import validate_task
    from scaffold_semantic_delta import scaffold_delta


def scaffold_authoring(task: dict[str, Any]) -> dict[str, Any]:
    validate_task(task)
    scaffold = scaffold_delta(task)
    prefilled = {
        item["clause_id"] for item in scaffold["clause_dispositions"]
    }
    ordered_clauses = [
        unit["clause_id"]
        for rule in task["rules"]
        for unit in rule["source_units"]
    ]
    return {
        "format": "iupac-bluebook-semantic-authoring",
        "format_version": "1.0.0",
        "task_id": task["task_id"],
        "clauses": [None if clause_id in prefilled else [] for clause_id in ordered_clauses],
        "symbols": [],
        "units": [],
        "exceptions": [],
        "tables": [],
        "figures": [],
        "examples": [],
        "corrections": [],
        "refs": [],
        "additional_refs": [],
    }


def authoring_metrics(
    task: Mapping[str, Any], authoring: Mapping[str, Any]
) -> dict[str, int]:
    clauses = authoring["clauses"]
    citation_count = sum(len(rule["references"]) for rule in task["rules"])
    prefilled_citations = len(scaffold_delta(dict(task))["citation_bindings"])
    return {
        "clause_count": len(clauses),
        "prefilled_clause_count": sum(item is None for item in clauses),
        "unresolved_clause_count": sum(item == [] for item in clauses),
        "citation_count": citation_count,
        "prefilled_citation_count": prefilled_citations,
        "unresolved_citation_count": citation_count - prefilled_citations,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a sparse compact semantic-authoring skeleton"
    )
    parser.add_argument("task", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--metrics", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    task = load_json(args.task)
    authoring = scaffold_authoring(task)
    rendered = canonical_json_bytes(authoring)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(rendered)
    else:
        sys.stdout.buffer.write(rendered)
    if args.metrics:
        stream = sys.stdout if args.output else sys.stderr
        print(json.dumps(authoring_metrics(task, authoring), sort_keys=True), file=stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
