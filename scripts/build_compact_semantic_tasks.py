from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

try:
    from scripts.build_semantic_work_packets import (
        EXPECTED_REFERENCE_OCCURRENCE_COUNT,
        EXPECTED_REFERENCE_RESOLUTION_COUNT,
        EXPECTED_RULE_COUNT,
        effective_reference_target,
        immediate_parent,
        partition_records,
        validate_clause_inventory,
        validate_reference_occurrences,
        validate_reference_resolutions,
    )
    from scripts.document_node_store import DEFAULT_STORE, hash_document_nodes
except ModuleNotFoundError:  # Support direct script execution.
    from build_semantic_work_packets import (  # type: ignore[no-redef]
        EXPECTED_REFERENCE_OCCURRENCE_COUNT,
        EXPECTED_REFERENCE_RESOLUTION_COUNT,
        EXPECTED_RULE_COUNT,
        effective_reference_target,
        immediate_parent,
        partition_records,
        validate_clause_inventory,
        validate_reference_occurrences,
        validate_reference_resolutions,
    )
    from document_node_store import (  # type: ignore[no-redef]
        DEFAULT_STORE,
        hash_document_nodes,
    )


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "bluebook_v3"
DEFAULT_SOURCE = BASE / "bluebook_v3_source_corpus.json"
DEFAULT_CORRECTIONS = BASE / "bluebook_v3_correction_overlays.json"
DEFAULT_CLAUSES = BASE / "bluebook_v3_clause_inventory.json"
DEFAULT_OCCURRENCES = BASE / "bluebook_v3_reference_occurrences.json"
DEFAULT_RESOLUTIONS = BASE / "bluebook_v3_reference_resolutions.json"
DEFAULT_OUTPUT = ROOT / "work" / "compact_semantic_tasks"
SCHEMA_PATH = ROOT / "data" / "bluebook_compact_semantic_task.schema.json"
SOURCE_HASH_FIELDS = (
    "source_corpus_sha256",
    "document_nodes_sha256",
    "correction_overlays_sha256",
    "clause_inventory_sha256",
    "reference_occurrences_sha256",
    "reference_resolutions_sha256",
)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def digest_without_field(value: Mapping[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return sha256_bytes(canonical_json_bytes(payload))


def model_view_bytes(task: Mapping[str, Any]) -> bytes:
    header = {
        "format": "iupac-bluebook-semantic-model-view",
        "format_version": "1.0.0",
        "task_id": task["task_id"],
        "task_sha256": task["task_sha256"],
        "row_fields": {
            "C": [
                "clause_id",
                "unit_kind",
                "node_kind",
                "ancestor_node_kinds",
                "semantic_cue",
                "text",
                "payload",
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
            "R": ["rule_id", "immediate_parent"],
            "X": [
                "occurrence_id",
                "reference_text",
                "effective_target_rule_id",
                "effective_target_kind",
                "resolution_id",
                "context_text",
            ],
        },
    }
    lines = [
        json.dumps(header, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    ]
    for rule in task["rules"]:
        lines.append(
            json.dumps(
                ["R", rule["rule_id"], rule["immediate_parent"]],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        for unit in rule["source_units"]:
            lines.append(
                json.dumps(
                    [
                        "C",
                        unit["clause_id"],
                        unit["unit_kind"],
                        unit["node_kind"],
                        unit["ancestor_node_kinds"],
                        unit["semantic_cue"],
                        unit["text"],
                        unit["payload"],
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        for reference in rule["references"]:
            lines.append(
                json.dumps(
                    [
                        "X",
                        reference["occurrence_id"],
                        reference["reference_text"],
                        reference["effective_target_rule_id"],
                        reference["effective_target_kind"],
                        reference["resolution_id"],
                        reference["context_text"],
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
    for correction in task["corrections"]:
        for operation in correction["operations"]:
            lines.append(
                json.dumps(
                    [
                        "K",
                        correction["overlay_id"],
                        operation["operation_id"],
                        operation["kind"],
                        operation["before_text"],
                        operation["after_text"],
                        operation["source_text"],
                        operation["assets"],
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
    return ("\n".join(lines) + "\n").encode("utf-8")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def file_hash(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def unit_ancestor_kinds(
    inventory_record: Mapping[str, Any],
) -> dict[str, list[str]]:
    coverage = inventory_record["node_coverage"]
    result: dict[str, list[str]] = {}
    for node in coverage:
        path = node["component_path"]
        ancestors = [
            candidate["node_kind"]
            for candidate in coverage
            if candidate["component_path"] != path
            and path.startswith(candidate["component_path"] + "/")
        ]
        for unit_id in node["unit_ids"]:
            if unit_id in result:
                raise ValueError(f"Source unit occurs under multiple nodes: {unit_id}")
            result[unit_id] = ancestors
    return result


def compact_source_unit(
    unit: Mapping[str, Any], ancestor_node_kinds: Sequence[str]
) -> dict[str, Any]:
    return {
        "clause_id": unit["unit_id"],
        "ordinal": unit["ordinal"],
        "unit_kind": unit["unit_kind"],
        "node_kind": unit["node_kind"],
        "ancestor_node_kinds": list(ancestor_node_kinds),
        "semantic_cue": unit["semantic_cue"],
        "text": unit["text"],
        "text_sha256": unit["text_sha256"],
        "payload": unit["payload"],
        "payload_sha256": unit["payload_sha256"],
        "source_node_id": unit["source_node_id"],
    }


def compact_reference(
    occurrence: Mapping[str, Any],
    resolution: Mapping[str, Any] | None,
    active_ids: set[str],
) -> dict[str, Any]:
    effective_target, target_kind = effective_reference_target(
        dict(occurrence), dict(resolution) if resolution is not None else None, active_ids
    )
    if target_kind == "historical_deleted_rule":
        target_kind = "historical_rule"
    context = occurrence["context"]
    return {
        "occurrence_id": occurrence["occurrence_id"],
        "reference_kind": occurrence["reference_kind"],
        "reference_text": occurrence["reference_text"],
        "cited_rule_id": occurrence["cited_rule_id"],
        "raw_target_rule_id": occurrence["target"]["rule_id"],
        "effective_target_rule_id": effective_target,
        "effective_target_kind": target_kind,
        "resolution_id": resolution["resolution_id"] if resolution else None,
        "resolution_kind": resolution["resolution_kind"] if resolution else None,
        "correction_overlay_id": (
            resolution["correction_overlay_id"] if resolution else None
        ),
        "context_text": context["text"],
        "context_sha256": context["text_sha256"],
    }


def compact_correction(correction: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "overlay_id": correction["overlay_id"],
        "effective_date": correction["effective_date"],
        "status": correction["status"],
        "operations": [
            {
                "operation_id": operation["operation_id"],
                "kind": operation["kind"],
                "before_text": operation.get("before_text"),
                "after_text": operation.get("after_text"),
                "source_text": operation["source_text"],
                "assets": operation.get("assets", []),
            }
            for operation in correction["operations"]
        ],
    }


def build_tasks(
    source: dict[str, Any],
    corrections: dict[str, Any],
    clause_inventory: dict[str, Any],
    reference_occurrences: dict[str, Any],
    reference_resolutions: dict[str, Any],
    source_hashes: dict[str, str],
    max_assigned_records: int = 24,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = source["records"]
    if len(records) != EXPECTED_RULE_COUNT:
        raise ValueError(f"Expected {EXPECTED_RULE_COUNT} source records")

    clause_by_rule = validate_clause_inventory(
        records,
        clause_inventory,
        source_hashes["source_corpus_sha256"],
        source_hashes["document_nodes_sha256"],
        source_hashes["correction_overlays_sha256"],
        source_hashes["clause_inventory_sha256"],
    )
    occurrences = validate_reference_occurrences(
        records,
        reference_occurrences,
        source_hashes["reference_occurrences_sha256"],
    )
    resolutions = validate_reference_resolutions(
        records,
        corrections,
        occurrences,
        reference_resolutions,
        source_hashes["source_corpus_sha256"],
        source_hashes["correction_overlays_sha256"],
        source_hashes["reference_occurrences_sha256"],
        source_hashes["reference_resolutions_sha256"],
    )

    active_ids = {record["source_rule_id"] for record in records}
    records_by_id = {record["source_rule_id"]: record for record in records}
    position = {record["source_rule_id"]: index for index, record in enumerate(records)}
    occurrences_by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        occurrences_by_rule[occurrence["source_rule_id"]].append(occurrence)
    corrections_by_id = {
        correction["overlay_id"]: correction for correction in corrections["records"]
    }

    tasks: list[dict[str, Any]] = []
    major_part_numbers: dict[str, int] = defaultdict(int)
    for partition in partition_records(records, max_assigned_records):
        assigned_rule_ids = [record["source_rule_id"] for record in partition]
        major = assigned_rule_ids[0].split(".", 1)[0]
        major_part_numbers[major] += 1
        task_id = f"{major}-part-{major_part_numbers[major]:03d}"
        compact_rules: list[dict[str, Any]] = []
        relevant_correction_ids: set[str] = set()

        for source_record in partition:
            rule_id = source_record["source_rule_id"]
            inventory_record = clause_by_rule[rule_id]
            ancestors_by_unit = unit_ancestor_kinds(inventory_record)
            rule_occurrences = occurrences_by_rule.get(rule_id, [])
            compact_references = []
            for occurrence in rule_occurrences:
                resolution = resolutions.get(occurrence["occurrence_id"])
                compact_references.append(
                    compact_reference(occurrence, resolution, active_ids)
                )
                if resolution and resolution["correction_overlay_id"]:
                    relevant_correction_ids.add(resolution["correction_overlay_id"])
            relevant_correction_ids.update(inventory_record["correction_overlay_ids"])
            index = position[rule_id]
            compact_rules.append(
                {
                    "rule_id": rule_id,
                    "record_id": inventory_record["record_id"],
                    "record_sha256": inventory_record["record_sha256"],
                    "chapter": inventory_record["chapter"],
                    "immediate_parent": immediate_parent(
                        rule_id, records_by_id[rule_id], active_ids
                    ),
                    "preceding_rule_ids": [
                        record["source_rule_id"]
                        for record in records[max(0, index - 2) : index]
                    ],
                    "following_rule_ids": [
                        record["source_rule_id"]
                        for record in records[index + 1 : index + 3]
                    ],
                    "source_units": [
                        compact_source_unit(
                            unit, ancestors_by_unit[unit["unit_id"]]
                        )
                        for unit in inventory_record["source_units"]
                    ],
                    "references": compact_references,
                    "correction_overlay_ids": inventory_record[
                        "correction_overlay_ids"
                    ],
                }
            )

        missing_corrections = relevant_correction_ids.difference(corrections_by_id)
        if missing_corrections:
            raise ValueError(
                f"Task {task_id} has unknown correction overlays: "
                f"{sorted(missing_corrections)}"
            )
        task: dict[str, Any] = {
            "format": "iupac-bluebook-compact-semantic-task",
            "format_version": "1.0.0",
            "task_id": task_id,
            "source_hashes": source_hashes,
            "assigned_rule_ids": assigned_rule_ids,
            "rules": compact_rules,
            "corrections": [
                compact_correction(corrections_by_id[correction_id])
                for correction_id in sorted(relevant_correction_ids)
            ],
            "metrics": {
                "rule_count": len(compact_rules),
                "clause_count": sum(
                    len(rule["source_units"]) for rule in compact_rules
                ),
                "reference_count": sum(
                    len(rule["references"]) for rule in compact_rules
                ),
                "correction_count": len(relevant_correction_ids),
            },
        }
        task["task_sha256"] = digest_without_field(task, "task_sha256")
        tasks.append(task)

    assigned = [rule_id for task in tasks for rule_id in task["assigned_rule_ids"]]
    expected = [record["source_rule_id"] for record in records]
    if assigned != expected or len(set(assigned)) != EXPECTED_RULE_COUNT:
        raise ValueError("Compact tasks do not exactly cover source rules in order")
    clause_count = sum(task["metrics"]["clause_count"] for task in tasks)
    reference_count = sum(task["metrics"]["reference_count"] for task in tasks)
    if clause_count != clause_inventory["counters"]["source_unit_count"]:
        raise ValueError("Compact task clause coverage is not exact")
    if reference_count != EXPECTED_REFERENCE_OCCURRENCE_COUNT:
        raise ValueError("Compact task reference coverage is not exact")
    resolution_count = sum(
        reference["resolution_id"] is not None
        for task in tasks
        for rule in task["rules"]
        for reference in rule["references"]
    )
    if resolution_count != EXPECTED_REFERENCE_RESOLUTION_COUNT:
        raise ValueError("Compact task resolution coverage is not exact")

    task_bytes = [canonical_json_bytes(task) for task in tasks]
    manifest: dict[str, Any] = {
        "format": "iupac-bluebook-compact-semantic-task-manifest",
        "format_version": "1.0.0",
        "source_hashes": source_hashes,
        "task_count": len(tasks),
        "assigned_rule_count": len(assigned),
        "assigned_clause_count": clause_count,
        "assigned_reference_count": reference_count,
        "assigned_reference_resolution_count": resolution_count,
        "task_file_bytes": sum(map(len, task_bytes)),
        "model_view_bytes": sum(len(model_view_bytes(task)) for task in tasks),
        "tasks": [
            {
                "task_id": task["task_id"],
                "task_sha256": task["task_sha256"],
                "assigned_rule_ids": task["assigned_rule_ids"],
                "clause_count": task["metrics"]["clause_count"],
                "reference_count": task["metrics"]["reference_count"],
            }
            for task in tasks
        ],
    }
    manifest["manifest_sha256"] = digest_without_field(manifest, "manifest_sha256")
    return tasks, manifest


def validate_tasks(
    tasks: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any]
) -> None:
    schema = load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for task in tasks:
        errors = sorted(validator.iter_errors(task), key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            raise ValueError(
                f"Task {task.get('task_id')} fails schema at "
                f"/{'/'.join(map(str, first.path))}: {first.message}"
            )
        if task["task_sha256"] != digest_without_field(task, "task_sha256"):
            raise ValueError(f"Task hash is invalid: {task['task_id']}")
        if task["assigned_rule_ids"] != [rule["rule_id"] for rule in task["rules"]]:
            raise ValueError(f"Task rule assignment order is invalid: {task['task_id']}")
    if manifest["manifest_sha256"] != digest_without_field(
        manifest, "manifest_sha256"
    ):
        raise ValueError("Compact task manifest hash is invalid")
    if manifest["tasks"] != [
        {
            "task_id": task["task_id"],
            "task_sha256": task["task_sha256"],
            "assigned_rule_ids": task["assigned_rule_ids"],
            "clause_count": task["metrics"]["clause_count"],
            "reference_count": task["metrics"]["reference_count"],
        }
        for task in tasks
    ]:
        raise ValueError("Compact task manifest entries do not match tasks")


def source_hashes(args: argparse.Namespace) -> dict[str, str]:
    return {
        "source_corpus_sha256": file_hash(args.source),
        "document_nodes_sha256": hash_document_nodes(args.document_nodes),
        "correction_overlays_sha256": file_hash(args.corrections),
        "clause_inventory_sha256": file_hash(args.clause_inventory),
        "reference_occurrences_sha256": file_hash(args.reference_occurrences),
        "reference_resolutions_sha256": file_hash(args.reference_resolutions),
    }


def write_or_check(
    output_dir: Path,
    tasks: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    check: bool,
) -> None:
    expected = {
        f"{task['task_id']}.json": canonical_json_bytes(task) for task in tasks
    }
    expected["manifest.json"] = canonical_json_bytes(manifest)
    if check:
        actual_names = (
            {path.name for path in output_dir.glob("*.json")}
            if output_dir.exists()
            else set()
        )
        if actual_names != set(expected):
            raise ValueError(
                "Compact task file set differs: "
                f"missing={sorted(set(expected) - actual_names)}, "
                f"extra={sorted(actual_names - set(expected))}"
            )
        for name, content in expected.items():
            if (output_dir / name).read_bytes() != content:
                raise ValueError(f"Compact task is stale or altered: {name}")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("*.json"):
        if stale.name not in expected:
            stale.unlink()
    for name, content in expected.items():
        (output_dir / name).write_bytes(content)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deduplicated, source-bound semantic conversion tasks"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--document-nodes", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--corrections", type=Path, default=DEFAULT_CORRECTIONS)
    parser.add_argument("--clause-inventory", type=Path, default=DEFAULT_CLAUSES)
    parser.add_argument("--reference-occurrences", type=Path, default=DEFAULT_OCCURRENCES)
    parser.add_argument("--reference-resolutions", type=Path, default=DEFAULT_RESOLUTIONS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-assigned-records", type=int, default=24)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate in memory and require byte-identical output files",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    hashes = source_hashes(args)
    tasks, manifest = build_tasks(
        load_json(args.source),
        load_json(args.corrections),
        load_json(args.clause_inventory),
        load_json(args.reference_occurrences),
        load_json(args.reference_resolutions),
        hashes,
        args.max_assigned_records,
    )
    validate_tasks(tasks, manifest)
    write_or_check(args.output_dir, tasks, manifest, args.check)
    print(
        json.dumps(
            {
                "mode": "check" if args.check else "write",
                "task_count": manifest["task_count"],
                "assigned_rule_count": manifest["assigned_rule_count"],
                "assigned_clause_count": manifest["assigned_clause_count"],
                "assigned_reference_count": manifest["assigned_reference_count"],
                "task_file_bytes": manifest["task_file_bytes"],
                "model_view_bytes": manifest["model_view_bytes"],
                "manifest_sha256": manifest["manifest_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
