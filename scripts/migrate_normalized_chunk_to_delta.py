from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

if __package__:
    from scripts import validate_normalized_rule_chunks as chunk_validator
    from scripts.build_compact_semantic_tasks import (
        canonical_json_bytes as compact_json_bytes,
        load_json,
    )
    from scripts.compile_semantic_delta import finalize_delta, validate_delta_schema
    from scripts.render_compact_semantic_task import validate_task
else:
    import validate_normalized_rule_chunks as chunk_validator
    from build_compact_semantic_tasks import (  # type: ignore[no-redef]
        canonical_json_bytes as compact_json_bytes,
        load_json,
    )
    from compile_semantic_delta import (  # type: ignore[no-redef]
        finalize_delta,
        validate_delta_schema,
    )
    from render_compact_semantic_task import validate_task  # type: ignore[no-redef]


def migrate_chunk(chunk: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    validate_task(task)
    if chunk.get("packet_id") != task["task_id"]:
        raise ValueError("Chunk and compact task IDs differ")
    if chunk.get("assigned_rule_ids") != task["assigned_rule_ids"]:
        raise ValueError("Chunk and compact task rule assignments differ")
    occurrence_by_id = {
        occurrence["occurrence_id"]: occurrence
        for rule in task["rules"]
        for occurrence in rule["references"]
    }
    citation_bindings = []
    additional_references = []
    for reference in chunk["references"]:
        if reference["relation"] == "hierarchy_parent":
            continue
        occurrence_ids = reference["source_occurrence_ids"]
        if not occurrence_ids:
            additional_references.append(reference)
            continue
        unknown = set(occurrence_ids).difference(occurrence_by_id)
        if unknown:
            raise ValueError(f"Chunk has unknown source occurrences: {sorted(unknown)}")
        binding = {
            "reference_id": reference["reference_id"],
            "clause_ids": reference["clause_ids"],
            "relation": reference["relation"],
            "source": reference["source"],
            "occurrence_ids": occurrence_ids,
            "resolution": reference["resolution"],
            "ordered_member_refs": reference["ordered_member_refs"],
        }
        if any(
            occurrence_by_id[occurrence_id]["effective_target_kind"]
            == "external_or_historical"
            for occurrence_id in occurrence_ids
        ):
            binding["target_override"] = reference["target"]
        citation_bindings.append(binding)

    delta = {
        key: chunk[key]
        for key in (
            "symbol_declarations",
            "clause_dispositions",
            "semantic_units",
            "exceptions",
            "tables",
            "figures",
            "examples",
            "correction_applications",
        )
    }
    delta["citation_bindings"] = citation_bindings
    delta["additional_references"] = additional_references
    finalize_delta(delta, task)
    validate_delta_schema(delta)
    return delta


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate a legacy normalized chunk to a compact semantic delta"
    )
    parser.add_argument("chunk", type=Path)
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        delta = migrate_chunk(load_json(args.chunk), load_json(args.task))
        rendered = compact_json_bytes(delta)
        if args.check_only:
            if not args.output.exists() or args.output.read_bytes() != rendered:
                raise ValueError("Migrated delta output is missing, stale, or altered")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(rendered)
    except (KeyError, TypeError, ValueError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1
    print(
        json.dumps(
            {
                "passed": True,
                "task_id": delta["task_id"],
                "task_sha256": delta["task_sha256"],
                "delta_sha256": delta["delta_sha256"],
                "output": str(args.output),
                "bytes": len(rendered),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
