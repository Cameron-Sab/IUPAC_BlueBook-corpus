from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.build_compact_semantic_tasks import canonical_json_bytes, load_json
    from scripts.local_semantic_authoring import (
        AUTHORING_VALIDATOR_VERSION,
        DEFAULT_BOOTSTRAP_DIR,
        DEFAULT_EXAMPLE,
        DEFAULT_MODEL,
        DEFAULT_OUTPUT_DIR,
        DEFAULT_SEED,
        SYSTEM_PROMPT,
        _sha256,
        assemble_authoring,
        validate_authoring,
    )
    from scripts.local_semantic_compaction import _request_model, build_candidate_view
else:
    from build_compact_semantic_tasks import (  # type: ignore[no-redef]
        canonical_json_bytes,
        load_json,
    )
    from local_semantic_authoring import (  # type: ignore[no-redef]
        AUTHORING_VALIDATOR_VERSION,
        DEFAULT_BOOTSTRAP_DIR,
        DEFAULT_EXAMPLE,
        DEFAULT_MODEL,
        DEFAULT_OUTPUT_DIR,
        DEFAULT_SEED,
        SYSTEM_PROMPT,
        _sha256,
        assemble_authoring,
        validate_authoring,
    )
    from local_semantic_compaction import (  # type: ignore[no-redef]
        _request_model,
        build_candidate_view,
    )


def response_schema(maximum_clauses: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "task_id",
            "clauses",
            "symbols",
            "units",
            "exceptions",
            "examples",
        ],
        "properties": {
            "task_id": {"type": "string"},
            "clauses": {
                "type": "array",
                "minItems": maximum_clauses,
                "maxItems": maximum_clauses,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["i", "decision"],
                    "properties": {
                        "i": {"type": "integer"},
                        "decision": {
                            "oneOf": [
                                {"type": "null"},
                                {"type": "array", "minItems": 3, "maxItems": 5},
                            ]
                        },
                    },
                },
            },
            "symbols": {
                "type": "array",
                "maxItems": maximum_clauses,
                "items": {"type": "object"},
            },
            "units": {
                "type": "array",
                "maxItems": maximum_clauses,
                "items": {"type": "object"},
            },
            "exceptions": {
                "type": "array",
                "maxItems": maximum_clauses,
                "items": {"type": "object"},
            },
            "examples": {
                "type": "array",
                "maxItems": maximum_clauses,
                "items": {"type": "object"},
            },
        },
    }


def partition_indexes(candidate: Mapping[str, Any], size: int) -> list[list[int]]:
    indexes = [
        clause["i"] for rule in candidate["rules"] for clause in rule["clauses"]
    ]
    return [indexes[offset : offset + size] for offset in range(0, len(indexes), size)]


def focused_candidate(
    candidate: Mapping[str, Any], target_indexes: Sequence[int]
) -> dict[str, Any]:
    targets = set(target_indexes)
    rules = []
    for rule in candidate["rules"]:
        indexes = [clause["i"] for clause in rule["clauses"]]
        selected: set[int] = set()
        for position, index in enumerate(indexes):
            if index not in targets:
                continue
            selected.add(index)
            if position:
                selected.add(indexes[position - 1])
            if position + 1 < len(indexes):
                selected.add(indexes[position + 1])
        if not selected:
            continue
        rules.append(
            {
                "rule_id": rule["rule_id"],
                "parent": rule.get("parent"),
                "clauses": [
                    {**clause, "authoring_target": clause["i"] in targets}
                    for clause in rule["clauses"]
                    if clause["i"] in selected
                ],
                "references": [
                    reference
                    for reference in rule.get("references", [])
                    if reference["i"] in selected
                ],
            }
        )
    return {
        "task_id": candidate["task_id"],
        "target_clause_indexes": list(target_indexes),
        "rules": rules,
        "rough_groups": [
            {
                **group,
                "clauses": [
                    index for index in group.get("clauses", []) if index in targets
                ],
            }
            for group in candidate.get("rough_groups", [])
            if set(group.get("clauses", [])).intersection(targets)
        ],
        "retained_table_clause_sets": candidate.get("rough_tables", []),
    }


def build_prompt(
    candidate: Mapping[str, Any],
    bootstrap: Mapping[str, Any],
    example: Mapping[str, Any],
    target_indexes: Sequence[int],
) -> str:
    focused = focused_candidate(candidate, target_indexes)
    retained = {
        "tables": [
            {"id": item["id"], "c": item["c"]} for item in bootstrap["tables"]
        ],
        "figures": [
            {"id": item["id"], "c": item["c"]} for item in bootstrap["figures"]
        ],
        "mechanical_clause_indexes": [
            index
            for index, decision in enumerate(bootstrap["clauses"], 1)
            if decision is None
        ],
    }
    return (
        SYSTEM_PROMPT
        + "\nPARTITION MODE: Author only target_clause_indexes. The clauses array "
        "must contain one {i,decision} object per target in the exact given order. "
        "All c arrays in units, exceptions, and examples must be subsets of the "
        "targets. Use IDs specific to the shown source rule so independently authored "
        "partitions merge without collisions. Neighbor clauses are context only.\n"
        + "RESPONSE SCHEMA:\n"
        + json.dumps(
            response_schema(len(target_indexes)), separators=(",", ":"), ensure_ascii=False
        )
        + "\nCOMPACT AUTHORING EXAMPLE:\n"
        + json.dumps(example, separators=(",", ":"), ensure_ascii=False)
        + "\nMECHANICALLY RETAINED OBJECTS:\n"
        + json.dumps(retained, separators=(",", ":"), ensure_ascii=False)
        + "\nFOCUSED SOURCE PARTITION:\n"
        + json.dumps(focused, separators=(",", ":"), ensure_ascii=False)
    )


def validate_patch(
    patch: Mapping[str, Any], task_id: str, target_indexes: Sequence[int]
) -> dict[str, Any]:
    errors = []
    if patch.get("task_id") != task_id:
        errors.append("task_id does not match")
    clauses = patch.get("clauses")
    expected = list(target_indexes)
    observed = []
    if not isinstance(clauses, list):
        errors.append("clauses must be an array")
    else:
        for item in clauses:
            if not isinstance(item, Mapping) or not isinstance(item.get("i"), int):
                errors.append("clause entry must contain an integer i")
                continue
            observed.append(item["i"])
            decision = item.get("decision")
            if decision is not None and (
                not isinstance(decision, list) or not 3 <= len(decision) <= 5
            ):
                errors.append(
                    f"clause {item['i']} decision must be null or a 3-5 item array"
                )
        if observed != expected:
            errors.append(f"clause indexes must exactly equal {expected}")
    target_set = set(expected)
    object_ids: set[str] = set()
    for collection in ("units", "exceptions", "examples"):
        values = patch.get(collection)
        if not isinstance(values, list):
            errors.append(f"{collection} must be an array")
            continue
        outside = sorted(
            {
                index
                for item in values
                if isinstance(item, Mapping) and isinstance(item.get("c"), list)
                for index in item["c"]
                if isinstance(index, int) and index not in target_set
            }
        )
        if outside:
            errors.append(f"{collection} contains non-target clauses: {outside}")
        for item in values:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            if item["id"] in object_ids:
                errors.append(f"duplicate authored id in partition: {item['id']}")
            object_ids.add(item["id"])
    if not isinstance(patch.get("symbols"), list):
        errors.append("symbols must be an array")
    else:
        for item in patch["symbols"]:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            if item["id"] in object_ids:
                errors.append(f"duplicate authored id in partition: {item['id']}")
            object_ids.add(item["id"])
    return {"passed": not errors, "errors": errors}


def deduplicate_patch_ids(
    patches: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Namespace only colliding authored IDs and update references in that patch."""
    seen: set[str] = set()
    result = []

    def replace(value: Any, replacements: Mapping[str, str]) -> Any:
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [replace(item, replacements) for item in value]
        if isinstance(value, Mapping):
            return {key: replace(item, replacements) for key, item in value.items()}
        return value

    for number, patch in enumerate(patches, 1):
        replacements: dict[str, str] = {}
        for collection in ("symbols", "units", "exceptions", "examples"):
            for item in patch.get(collection, []):
                if not isinstance(item, Mapping):
                    continue
                object_id = item.get("id")
                if not isinstance(object_id, str) or object_id not in seen:
                    if isinstance(object_id, str):
                        seen.add(object_id)
                    continue
                suffix = f"_partition_{number:03}"
                replacement = object_id + suffix
                counter = 2
                while replacement in seen:
                    replacement = f"{object_id}{suffix}_{counter}"
                    counter += 1
                replacements[object_id] = replacement
                seen.add(replacement)
        result.append(replace(patch, replacements))
    return result


def assemble_patches(
    patches: Sequence[Mapping[str, Any]],
    bootstrap: Mapping[str, Any],
) -> dict[str, Any]:
    clause_count = len(bootstrap["clauses"])
    clauses: list[Any] = [None] * clause_count
    symbols = []
    units = []
    exceptions = []
    examples = []
    task_id = bootstrap["task_id"]
    for patch in deduplicate_patch_ids(patches):
        if patch.get("task_id") != task_id:
            raise ValueError("Patch task_id does not match bootstrap")
        for item in patch["clauses"]:
            index = item["i"]
            if index < 1 or index > clause_count:
                raise ValueError(f"Patch clause index is out of range: {index}")
            if clauses[index - 1] is not None:
                raise ValueError(f"Patch clause index is duplicated: {index}")
            clauses[index - 1] = item["decision"]
        symbols.extend(patch["symbols"])
        units.extend(patch["units"])
        exceptions.extend(patch["exceptions"])
        examples.extend(patch["examples"])
    plan = {
        "task_id": task_id,
        "clauses": clauses,
        "symbols": symbols,
        "units": units,
        "exceptions": exceptions,
        "examples": examples,
    }
    return assemble_authoring(plan, bootstrap)


def process_task(
    task_path: Path,
    *,
    bootstrap_dir: Path,
    output_dir: Path,
    example_path: Path,
    model: str,
    backend: str,
    endpoint: str,
    context_tokens: int,
    maximum_output_tokens: int,
    partition_clauses: int,
    timeout: int,
    repair_attempts: int,
    seed: int,
    force: bool,
) -> dict[str, Any]:
    task = load_json(task_path)
    task_id = task["task_id"]
    bootstrap = load_json(bootstrap_dir / f"{task_id}.json")
    example = load_json(example_path)
    candidate = build_candidate_view(task, bootstrap)
    partitions = partition_indexes(candidate, partition_clauses)
    patch_dir = output_dir / "patches" / task_id
    report_dir = output_dir / "partition_reports" / task_id
    patch_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    patches = []
    partition_reports = []

    for number, indexes in enumerate(partitions, 1):
        prompt = build_prompt(candidate, bootstrap, example, indexes)
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper()
        schema = response_schema(len(indexes))
        patch_path = patch_dir / f"part-{number:03}.json"
        part_report_path = report_dir / f"part-{number:03}.json"
        if not force and patch_path.exists() and part_report_path.exists():
            patch = load_json(patch_path)
            part_report = load_json(part_report_path)
            validation = validate_patch(patch, task_id, indexes)
            if (
                part_report.get("prompt_sha256") == prompt_sha256
                and part_report.get("model") == model
                and part_report.get("backend") == backend
                and part_report.get("endpoint") == endpoint
                and validation["passed"]
            ):
                patches.append(patch)
                partition_reports.append({**part_report, "cached": True})
                continue

        output_tokens = min(
            maximum_output_tokens, max(4096, 768 + 384 * len(indexes))
        )
        request_prompt = prompt
        attempts = []
        best_patch: dict[str, Any] = {}
        best_validation = {"passed": False, "errors": ["not generated"]}
        for attempt in range(repair_attempts + 1):
            estimated = math.ceil(len(request_prompt.encode("utf-8")) / 2)
            if estimated + output_tokens > context_tokens:
                validation = {
                    "passed": False,
                    "errors": [
                        f"conservative context estimate {estimated + output_tokens} "
                        f"exceeds {context_tokens}"
                    ],
                }
                attempts.append({"attempt": attempt + 1, "validation": validation})
                break
            patch, metrics = _request_model(
                backend=backend,
                endpoint=endpoint,
                model=model,
                prompt=request_prompt,
                context_tokens=context_tokens,
                output_tokens=output_tokens,
                timeout=timeout,
                seed=seed + number - 1,
                schema=schema,
            )
            validation = validate_patch(patch, task_id, indexes)
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "metrics": metrics,
                    "validation": validation,
                }
            )
            if validation["passed"]:
                best_patch = patch
                best_validation = validation
                break
            best_patch = patch
            best_validation = validation
            if attempt < repair_attempts:
                request_prompt = (
                    prompt
                    + "\nPREVIOUS PARTITION FAILED VALIDATION:\n"
                    + json.dumps(
                        validation["errors"], separators=(",", ":"), ensure_ascii=False
                    )
                    + "\nReturn the complete corrected partition as JSON only."
                )
        if best_patch:
            patch_path.write_bytes(canonical_json_bytes(best_patch))
        part_report = {
            "format": "iupac-bluebook-local-authoring-partition-report",
            "format_version": "1.0.0",
            "task_id": task_id,
            "partition": number,
            "target_clause_indexes": indexes,
            "model": model,
            "backend": backend,
            "endpoint": endpoint,
            "seed": seed + number - 1,
            "prompt_sha256": prompt_sha256,
            "prompt_bytes": len(prompt.encode("utf-8")),
            "schema_sha256": _sha256(schema),
            "attempts": attempts,
            "validation": best_validation,
        }
        part_report_path.write_bytes(canonical_json_bytes(part_report))
        partition_reports.append(part_report)
        if not best_validation["passed"]:
            report = {
                "format": "iupac-bluebook-local-semantic-authoring-chunked-report",
                "format_version": "1.0.0",
                "validator_version": AUTHORING_VALIDATOR_VERSION,
                "task_id": task_id,
                "passed": False,
                "failed_partition": number,
                "partition_reports": partition_reports,
            }
            (output_dir / "reports").mkdir(parents=True, exist_ok=True)
            (output_dir / "reports" / f"{task_id}.json").write_bytes(
                canonical_json_bytes(report)
            )
            return report
        patches.append(best_patch)

    source = assemble_patches(patches, bootstrap)
    validation, chunk, compile_report = validate_authoring(source, task)
    authoring_dir = output_dir / "authoring"
    chunk_dir = output_dir / "chunks"
    report_output_dir = output_dir / "reports"
    for directory in (authoring_dir, chunk_dir, report_output_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (authoring_dir / f"{task_id}.json").write_bytes(canonical_json_bytes(source))
    if validation["passed"] and chunk is not None:
        (chunk_dir / f"{task_id}.json").write_bytes(canonical_json_bytes(chunk))
    report = {
        "format": "iupac-bluebook-local-semantic-authoring-chunked-report",
        "format_version": "1.0.0",
        "validator_version": AUTHORING_VALIDATOR_VERSION,
        "task_id": task_id,
        "task_sha256": task["task_sha256"],
        "passed": validation["passed"],
        "partition_count": len(partitions),
        "authoring_sha256": _sha256(source),
        "validation": validation,
        "compile_report": compile_report,
        "partition_reports": partition_reports,
    }
    (report_output_dir / f"{task_id}.json").write_bytes(canonical_json_bytes(report))
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Author semantic packets in resumable, strictly merged partitions"
    )
    parser.add_argument("tasks", nargs="+", type=Path)
    parser.add_argument("--bootstrap-dir", type=Path, default=DEFAULT_BOOTSTRAP_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--example", type=Path, default=DEFAULT_EXAMPLE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--backend", choices=("ollama", "openai"), default="openai")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument("--context-tokens", type=int, default=49152)
    parser.add_argument("--maximum-output-tokens", type=int, default=12288)
    parser.add_argument("--partition-clauses", type=int, default=24)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--repair-attempts", type=int, default=2)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    reports = []
    try:
        for task_path in args.tasks:
            reports.append(
                process_task(
                    task_path,
                    bootstrap_dir=args.bootstrap_dir,
                    output_dir=args.output_dir,
                    example_path=args.example,
                    model=args.model,
                    backend=args.backend,
                    endpoint=args.endpoint,
                    context_tokens=args.context_tokens,
                    maximum_output_tokens=args.maximum_output_tokens,
                    partition_clauses=args.partition_clauses,
                    timeout=args.timeout,
                    repair_attempts=args.repair_attempts,
                    seed=args.seed,
                    force=args.force,
                )
            )
    except (KeyError, OSError, TypeError, ValueError, RuntimeError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1
    output = {
        "format": "iupac-bluebook-local-semantic-authoring-chunked-run",
        "format_version": "1.0.0",
        "passed": all(report["passed"] for report in reports),
        "reports": reports,
    }
    rendered = json.dumps(output, indent=2, ensure_ascii=False) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
