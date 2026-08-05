from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

if __package__:
    from scripts import validate_normalized_rule_chunks as chunk_validator
    from scripts.build_compact_semantic_tasks import (
        canonical_json_bytes as compact_json_bytes,
        load_json,
    )
    from scripts.render_compact_semantic_task import validate_task
else:
    import validate_normalized_rule_chunks as chunk_validator
    from build_compact_semantic_tasks import (  # type: ignore[no-redef]
        canonical_json_bytes as compact_json_bytes,
        load_json,
    )
    from render_compact_semantic_task import validate_task  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[1]
DELTA_SCHEMA_PATH = ROOT / "data" / "normalized_rule_delta.schema.json"
DEFAULT_OUTPUT_DIR = ROOT / "work" / "compiled_semantic_chunks"
OBJECT_COLLECTIONS = (
    ("semantic_units", "semantic_unit", "unit_id", "semantic_unit_ids"),
    ("exceptions", "exception", "exception_id", "exception_ids"),
    ("tables", "table", "table_id", "table_ids"),
    ("figures", "figure", "figure_id", "figure_ids"),
    ("examples", "example", "example_id", "example_ids"),
    (
        "correction_applications",
        "correction_application",
        "correction_application_id",
        "correction_application_ids",
    ),
)


def validate_delta_schema(delta: Mapping[str, Any]) -> None:
    schema = load_json(DELTA_SCHEMA_PATH)
    language = load_json(chunk_validator.LANGUAGE_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    registry = Registry().with_resource(
        language["$id"], Resource.from_contents(language)
    )
    validator = Draft202012Validator(
        schema, registry=registry, format_checker=FormatChecker()
    )
    errors = sorted(validator.iter_errors(delta), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        raise ValueError(
            f"Delta fails schema at /{'/'.join(map(str, first.path))}: {first.message}"
        )


def finalize_delta(delta: dict[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    delta["format"] = "iupac-bluebook-semantic-decision-delta"
    delta["format_version"] = "1.0.0"
    delta["task_id"] = task["task_id"]
    delta["task_sha256"] = task["task_sha256"]
    delta["delta_sha256"] = chunk_validator.digest_without_field(
        delta, "delta_sha256"
    )
    return delta


def _reference_id(rule_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", rule_id.lower()).strip("_")
    return f"reference.{slug}.hierarchy_parent"


def _task_layout(
    task: Mapping[str, Any],
) -> tuple[list[str], dict[str, str], dict[str, Mapping[str, Any]]]:
    ordered_clauses: list[str] = []
    clause_owner: dict[str, str] = {}
    rules: dict[str, Mapping[str, Any]] = {}
    for rule in task["rules"]:
        rule_id = rule["rule_id"]
        rules[rule_id] = rule
        for unit in rule["source_units"]:
            clause_id = unit["clause_id"]
            if clause_id in clause_owner:
                raise ValueError(f"Task duplicates clause id: {clause_id}")
            clause_owner[clause_id] = rule_id
            ordered_clauses.append(clause_id)
    return ordered_clauses, clause_owner, rules


def _compatibility_packet(task: Mapping[str, Any]) -> dict[str, Any]:
    assignments = []
    relation_edges = []
    context_ids: set[str] = set()
    for rule in task["rules"]:
        resolutions = []
        occurrences = []
        for reference in rule["references"]:
            occurrences.append({"occurrence_id": reference["occurrence_id"]})
            if reference["resolution_id"] is not None:
                resolutions.append(
                    {
                        "occurrence_id": reference["occurrence_id"],
                        "resolution_id": reference["resolution_id"],
                    }
                )
            context_ids.add(reference["effective_target_rule_id"])
            relation_edges.append(
                {
                    "source": rule["rule_id"],
                    "relation": "source_citation",
                    "target": reference["effective_target_rule_id"],
                    "target_kind": reference["effective_target_kind"],
                }
            )
        assignments.append(
            {
                "source_rule_id": rule["rule_id"],
                "clause_inventory_record": {
                    "source_rule_id": rule["rule_id"],
                    "record_id": rule["record_id"],
                    "source_units": [
                        {
                            "unit_id": unit["clause_id"],
                            "unit_kind": unit["unit_kind"],
                            "ancestor_node_kinds": unit["ancestor_node_kinds"],
                            "semantic_cue": unit["semantic_cue"],
                        }
                        for unit in rule["source_units"]
                    ],
                },
                "immediate_parent": rule["immediate_parent"],
                "reference_occurrences": occurrences,
                "reference_resolutions": resolutions,
            }
        )
        relation_edges.append(
            {
                "source": rule["rule_id"],
                "relation": "hierarchy_parent",
                "target": rule["immediate_parent"],
                "target_kind": (
                    "chapter"
                    if rule["immediate_parent"].startswith("chapter:")
                    else "rule"
                ),
            }
        )
    packet: dict[str, Any] = {
        "format": "iupac-bluebook-compact-task-validation-adapter",
        "format_version": "1.0.0",
        "packet_id": task["task_id"],
        **task["source_hashes"],
        "assigned_rule_ids": task["assigned_rule_ids"],
        "assigned": assignments,
        "context_records": [
            {"source_rule_id": rule_id} for rule_id in sorted(context_ids)
        ],
        "relation_edges": relation_edges,
        "task_sha256": task["task_sha256"],
    }
    packet["packet_sha256"] = chunk_validator.digest_without_field(
        packet, "packet_sha256"
    )
    return packet


def _compile_references(
    delta: Mapping[str, Any],
    task: Mapping[str, Any],
    rules: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for rule in task["rules"]:
        references.append(
            {
                "reference_id": _reference_id(rule["rule_id"]),
                "clause_ids": [rule["source_units"][0]["clause_id"]],
                "relation": "hierarchy_parent",
                "source": {"kind": "record", "id": rule["record_id"]},
                "target": {
                    "kind": (
                        "chapter"
                        if rule["immediate_parent"].startswith("chapter:")
                        else "rule"
                    ),
                    "id": rule["immediate_parent"],
                },
                "resolution": "exact",
                "ordered_member_refs": [],
                "source_occurrence_ids": [],
                "resolution_overlay_ids": [],
            }
        )

    occurrence_by_id: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    expected_occurrence_ids: list[str] = []
    for rule in task["rules"]:
        for occurrence in rule["references"]:
            occurrence_by_id[occurrence["occurrence_id"]] = (rule, occurrence)
            expected_occurrence_ids.append(occurrence["occurrence_id"])

    observed_occurrence_ids: list[str] = []
    for binding in delta["citation_bindings"]:
        pairs = []
        for occurrence_id in binding["occurrence_ids"]:
            if occurrence_id not in occurrence_by_id:
                raise ValueError(
                    f"Citation binding names an occurrence outside the task: {occurrence_id}"
                )
            pairs.append(occurrence_by_id[occurrence_id])
            observed_occurrence_ids.append(occurrence_id)
        source_rule_ids = {rule["rule_id"] for rule, _ in pairs}
        targets = {
            (occurrence["effective_target_rule_id"], occurrence["effective_target_kind"])
            for _, occurrence in pairs
        }
        if len(source_rule_ids) != 1 or len(targets) != 1:
            raise ValueError(
                f"Citation binding {binding['reference_id']} merges different sources or targets"
            )
        source_rule_id = next(iter(source_rule_ids))
        target_id, compact_target_kind = next(iter(targets))
        target_override = binding.get("target_override")
        if compact_target_kind == "external_or_historical":
            if target_override is None:
                raise ValueError(
                    f"Citation binding {binding['reference_id']} needs target_override "
                    "because the source target is not an active rule"
                )
            target = target_override
        else:
            if target_override is not None:
                raise ValueError(
                    f"Citation binding {binding['reference_id']} cannot override an exact target"
                )
            target = {
                "kind": (
                    "rule" if compact_target_kind == "rule" else "historical_rule"
                ),
                "id": target_id,
            }
        resolution_overlay_ids = [
            occurrence["resolution_id"]
            for _, occurrence in pairs
            if occurrence["resolution_id"] is not None
        ]
        references.append(
            {
                "reference_id": binding["reference_id"],
                "clause_ids": binding["clause_ids"],
                "relation": binding["relation"],
                "source": binding.get(
                    "source",
                    {"kind": "record", "id": rules[source_rule_id]["record_id"]},
                ),
                "target": target,
                "resolution": binding["resolution"],
                "ordered_member_refs": binding["ordered_member_refs"],
                "source_occurrence_ids": binding["occurrence_ids"],
                "resolution_overlay_ids": resolution_overlay_ids,
            }
        )
    if observed_occurrence_ids != expected_occurrence_ids:
        raise ValueError(
            "Citation bindings must cover every task occurrence exactly once in source order"
        )

    for reference in delta["additional_references"]:
        if reference["relation"] == "hierarchy_parent":
            raise ValueError("Hierarchy references are generated and cannot be authored")
        if reference["source_occurrence_ids"] or reference["resolution_overlay_ids"]:
            raise ValueError(
                "Raw occurrence evidence belongs in citation_bindings, not additional_references"
            )
        references.append(dict(reference))
    return references


def _object_owners(
    delta: Mapping[str, Any], clause_owner: Mapping[str, str]
) -> tuple[dict[str, str], dict[str, dict[str, list[str]]]]:
    owner_by_object: dict[str, str] = {}
    links: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {field: [] for *_, field in OBJECT_COLLECTIONS}
    )
    for collection, _kind, id_field, link_field in OBJECT_COLLECTIONS:
        for obj in delta[collection]:
            clause_ids = obj.get("clause_ids", [])
            if not clause_ids:
                raise ValueError(f"Semantic object has no source clauses: {obj[id_field]}")
            owners = {clause_owner.get(clause_id) for clause_id in clause_ids}
            if None in owners or len(owners) != 1:
                raise ValueError(
                    f"Semantic object crosses task records or cites unknown clauses: {obj[id_field]}"
                )
            owner = next(iter(owners))
            owner_by_object[obj[id_field]] = owner
            links[owner][link_field].append(obj[id_field])
    return owner_by_object, links


def _reference_owner(
    reference: Mapping[str, Any],
    rules: Mapping[str, Mapping[str, Any]],
    clause_owner: Mapping[str, str],
    object_owner: Mapping[str, str],
) -> str:
    source = reference["source"]
    if source["kind"] == "record":
        for rule_id, rule in rules.items():
            if rule["record_id"] == source["id"]:
                return rule_id
    if source["id"] in object_owner:
        return object_owner[source["id"]]
    owners = {clause_owner.get(clause_id) for clause_id in reference["clause_ids"]}
    if None not in owners and len(owners) == 1:
        return next(iter(owners))
    raise ValueError(f"Cannot assign reference to a source record: {reference['reference_id']}")


def compile_delta(
    delta: dict[str, Any], task: dict[str, Any], *, finalize_draft: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_task(task)
    if finalize_draft:
        finalize_delta(delta, task)
    validate_delta_schema(delta)
    if delta["task_id"] != task["task_id"]:
        raise ValueError("Delta task_id does not match the compact task")
    if delta["task_sha256"] != task["task_sha256"]:
        raise ValueError("Delta task_sha256 does not match the compact task")
    if delta["delta_sha256"] != chunk_validator.digest_without_field(
        delta, "delta_sha256"
    ):
        raise ValueError("Delta SHA-256 does not reproduce from delta content")

    ordered_clauses, clause_owner, rules = _task_layout(task)
    disposition_ids = [item["clause_id"] for item in delta["clause_dispositions"]]
    if disposition_ids != ordered_clauses:
        raise ValueError(
            "Clause dispositions must exactly cover task clauses in source order"
        )

    references = _compile_references(delta, task, rules)
    object_owner, links = _object_owners(delta, clause_owner)
    reference_links: dict[str, list[str]] = defaultdict(list)
    for reference in references:
        owner = _reference_owner(
            reference, rules, clause_owner, object_owner
        )
        reference_links[owner].append(reference["reference_id"])

    compiled_kinds = {
        item["clause_id"]: item["disposition"]["kind"]
        for item in delta["clause_dispositions"]
    }
    records = []
    for rule in task["rules"]:
        rule_id = rule["rule_id"]
        clause_ids = [unit["clause_id"] for unit in rule["source_units"]]
        record_links = links[rule_id]
        records.append(
            {
                "record_id": rule["record_id"],
                "source_rule_id": rule_id,
                "chapter": rule["chapter"],
                "clause_ids": clause_ids,
                "operative": any(
                    compiled_kinds[clause_id] == "compiled"
                    for clause_id in clause_ids
                ),
                **record_links,
                "reference_ids": reference_links[rule_id],
            }
        )

    packet = _compatibility_packet(task)
    chunk: dict[str, Any] = {
        "format": "iupac-bluebook-normalized-rule-chunk",
        "format_version": "1.0.0",
        "packet_id": task["task_id"],
        "packet_sha256": packet["packet_sha256"],
        "schema_sha256": chunk_validator.language_schema_sha256(),
        **task["source_hashes"],
        "assigned_rule_ids": task["assigned_rule_ids"],
        "symbol_declarations": delta["symbol_declarations"],
        "clause_dispositions": delta["clause_dispositions"],
        "records": records,
        "semantic_units": delta["semantic_units"],
        "exceptions": delta["exceptions"],
        "tables": delta["tables"],
        "figures": delta["figures"],
        "examples": delta["examples"],
        "correction_applications": delta["correction_applications"],
        "references": references,
    }
    chunk["chunk_metrics"] = chunk_validator._expected_metrics(chunk)
    chunk["chunk_sha256"] = chunk_validator.digest_without_field(
        chunk, "chunk_sha256"
    )
    result = chunk_validator.validate_chunk(
        chunk,
        packet,
        chunk_bytes=chunk_validator.canonical_json_bytes(chunk),
        packet_bytes=chunk_validator.canonical_json_bytes(packet),
        validate_packet_schema=False,
    )
    return chunk, result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile a semantic decision delta into a fully validated rule chunk"
    )
    parser.add_argument("delta", type=Path)
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--finalize-draft",
        action="store_true",
        help="Stamp missing or stale delta envelope fields before strict compilation",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    delta = load_json(args.delta)
    task = load_json(args.task)
    try:
        chunk, result = compile_delta(
            delta, task, finalize_draft=args.finalize_draft
        )
    except (KeyError, TypeError, ValueError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1
    if result["passed"] and not args.check_only:
        args.delta.write_bytes(compact_json_bytes(delta))
        output = args.output or DEFAULT_OUTPUT_DIR / f"{task['task_id']}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(chunk_validator.canonical_json_bytes(chunk))
        result["output"] = str(output)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
