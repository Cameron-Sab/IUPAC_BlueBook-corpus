from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.bootstrap_semantic_authoring import (
        DEFAULT_AUTHORING_DIR,
        DEFAULT_TASK_DIR,
        _ordered_units,
    )
    from scripts.build_compact_semantic_tasks import canonical_json_bytes, load_json
else:
    from bootstrap_semantic_authoring import (  # type: ignore[no-redef]
        DEFAULT_AUTHORING_DIR,
        DEFAULT_TASK_DIR,
        _ordered_units,
    )
    from build_compact_semantic_tasks import canonical_json_bytes, load_json


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "work" / "local_semantic_compaction"
DEFAULT_REFERENCE_DIR = ROOT / "data" / "bluebook_v3" / "semantic_authoring"
DEFAULT_MODEL = "qwen3:30b-instruct"
DEFAULT_SEED = 3407
VALIDATOR_VERSION = "1.1.0"
PLAN_KINDS = {"rule", "decision", "definition", "mapping", "procedure", "constraint"}
PLAN_FORCES = {"required", "permitted", "prohibited", "preference", "definition"}

SYSTEM_PROMPT = """You convert IUPAC Blue Book source clauses into a machine-readable semantic plan.
The source is authoritative. The deterministic candidate is only a rough draft.

Requirements:
- Preserve every source clause exactly once in either one semantic group or examples.
- Preserve rule order, conditions, actions, prohibitions, permissions, preferences, tie continuation, exceptions, scope, and cross-references.
- Use structured JSON values, not prose summaries standing in for control flow.
- Do not invent chemistry, predicates, dependencies, examples, or rule targets.
- Use source cross-references when interpreting semantics. Their exact graph edges are rebuilt mechanically after generation.
- Semantic groups are task-level objects and may span source-rule records. Source rule hierarchy stays in the input and is reconstructed deterministically.
- Keep JSON compact: group adjacent clauses that express one semantic unit and use concise structured values.
- A later compiler will attach immutable source IDs and reject missing or duplicated clauses.
- Return JSON only and obey the response schema.

Group semantic shapes:
- rule: {"condition": object, "actions": [object], "else_actions": [object]}
- decision: {"candidate_set": string, "ordered_criteria": [object], "terminal_tie": string}
- definition: {"term": string, "entity_type": string, "value": object}
- mapping: {"keys": [string], "results": [string], "entries": [object]}
- procedure: {"steps": [object]}
- constraint: {"assertion": object, "on_violation": object}

An exception is separate from its target group or source rule and has condition, effect, and precedence.
Clause indexes are the integer i values in the input. Do not use source clause IDs in clauses arrays.
"""


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def _compact_source(unit: Mapping[str, Any], index: int, decision: Any) -> dict[str, Any]:
    output: dict[str, Any] = {
        "i": index,
        "id": unit["clause_id"],
        "source_kind": unit.get("unit_kind"),
        "node_kind": unit.get("node_kind"),
        "text": unit.get("text"),
    }
    if unit.get("semantic_cue") is not None:
        output["cue"] = unit["semantic_cue"]
    if unit.get("payload") is not None:
        output["payload"] = unit["payload"]
    if isinstance(decision, list):
        output["draft"] = decision[:3]
    else:
        output["mechanical"] = True
    return output


def _reference_clause_index(
    reference: Mapping[str, Any], source_units: Sequence[Mapping[str, Any]], indexes: Sequence[int]
) -> int:
    needle = str(reference.get("reference_text") or "")
    for index, source in zip(indexes, source_units):
        if needle and needle in str(source.get("text") or ""):
            return index
    return indexes[0]


def build_candidate_view(
    task: Mapping[str, Any], authoring: Mapping[str, Any]
) -> dict[str, Any]:
    ordered_units = _ordered_units(task)
    decisions = authoring.get("clauses", [])
    if len(decisions) != len(ordered_units):
        raise ValueError("Candidate clause slots do not match task source units")

    rule_rows = []
    offset = 0
    for rule in task["rules"]:
        source_units = rule["source_units"]
        indexes = list(range(offset + 1, offset + len(source_units) + 1))
        clauses = [
            _compact_source(source, index, decisions[index - 1])
            for index, source in zip(indexes, source_units)
        ]
        rule_rows.append(
            {
                "rule_id": rule["rule_id"],
                "parent": rule.get("immediate_parent"),
                "clauses": clauses,
                "references": [
                    {
                        "text": reference["reference_text"],
                        "occurrence_id": reference["occurrence_id"],
                        "target": reference["effective_target_rule_id"],
                        "target_kind": reference["effective_target_kind"],
                        "i": _reference_clause_index(reference, source_units, indexes),
                    }
                    for reference in rule.get("references", [])
                ],
            }
        )
        offset += len(source_units)

    groups = [
        {
            "id": unit.get("id"),
            "kind": unit.get("k"),
            "force": unit.get("f"),
            "clauses": unit.get("c", []),
        }
        for unit in authoring.get("units", [])
    ]
    return {
        "task_id": task["task_id"],
        "task_sha256": task["task_sha256"],
        "rules": rule_rows,
        "rough_groups": groups,
        "rough_examples": [example.get("c", []) for example in authoring.get("examples", [])],
        "rough_tables": [table.get("c", []) for table in authoring.get("tables", [])],
    }


def response_schema() -> dict[str, Any]:
    group = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "kind", "force", "clauses", "semantics"],
        "properties": {
            "id": {"type": "string"},
            "kind": {"type": "string", "enum": sorted(PLAN_KINDS)},
            "force": {"type": "string", "enum": sorted(PLAN_FORCES)},
            "clauses": {"type": "array", "items": {"type": "integer"}},
            "semantics": {"type": "object", "minProperties": 1},
        },
    }
    exception = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "clauses", "target", "condition", "effect", "precedence"],
        "properties": {
            "id": {"type": "string"},
            "clauses": {"type": "array", "items": {"type": "integer"}},
            "target": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "id"],
                "properties": {
                    "kind": {"type": "string", "enum": ["group", "rule"]},
                    "id": {"type": "string"},
                },
            },
            "condition": {"type": "object"},
            "effect": {"type": "object"},
            "precedence": {"type": "integer"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id", "groups", "exceptions", "examples"],
        "properties": {
            "task_id": {"type": "string"},
            "groups": {"type": "array", "items": group},
            "exceptions": {"type": "array", "items": exception},
            "examples": {"type": "array", "items": {"type": "integer"}},
        },
    }


def build_prompt(candidate: Mapping[str, Any]) -> str:
    return (
        SYSTEM_PROMPT
        + "\nRESPONSE SCHEMA:\n"
        + json.dumps(response_schema(), separators=(",", ":"), ensure_ascii=False)
        + "\nSOURCE AND ROUGH CANDIDATE:\n"
        + json.dumps(candidate, separators=(",", ":"), ensure_ascii=False)
    )


def _request_ollama(
    *,
    endpoint: str,
    model: str,
    prompt: str,
    context_tokens: int,
    output_tokens: int,
    timeout: int,
    seed: int,
    schema: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = schema or response_schema()
    body = {
        "model": model,
        "stream": False,
        "think": False,
        "format": schema,
        "messages": [{"role": "user", "content": prompt}],
        "options": {
            "temperature": 0.0,
            "top_p": 1.0,
            "repeat_penalty": 1.05,
            "num_ctx": context_tokens,
            "num_predict": output_tokens,
            "seed": seed,
        },
    }
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise RuntimeError(f"Ollama request failed: {error}") from error
    content = raw.get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("Ollama response has no message content")
    metrics = {
        "backend": "ollama",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "prompt_tokens": raw.get("prompt_eval_count"),
        "output_tokens": raw.get("eval_count"),
        "finish_reason": raw.get("done_reason"),
        "load_duration_ns": raw.get("load_duration"),
        "prompt_duration_ns": raw.get("prompt_eval_duration"),
        "output_duration_ns": raw.get("eval_duration"),
    }
    if raw.get("done_reason") == "length":
        raise ValueError(
            f"Ollama output reached the {output_tokens}-token limit before completing JSON"
        )
    try:
        plan = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Ollama returned invalid JSON ({len(content)} characters): {error}"
        ) from error
    return plan, metrics


def _request_openai(
    *,
    endpoint: str,
    model: str,
    prompt: str,
    context_tokens: int,
    output_tokens: int,
    timeout: int,
    seed: int,
    schema: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    del context_tokens
    schema = schema or response_schema()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "top_p": 1.0,
        "repeat_penalty": 1.05,
        "seed": seed,
        "max_tokens": output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "iupac_semantic_plan",
                "strict": True,
                "schema": schema,
            },
        },
    }
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise RuntimeError(f"OpenAI-compatible request failed: {error}") from error
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("OpenAI-compatible response has no choices")
    choice = choices[0]
    content = choice.get("message", {}).get("content")
    if not isinstance(content, str):
        raise ValueError("OpenAI-compatible response has no message content")
    finish_reason = choice.get("finish_reason")
    usage = raw.get("usage", {})
    timings = raw.get("timings", {})
    metrics = {
        "backend": "openai",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "prompt_tokens": usage.get("prompt_tokens", timings.get("prompt_n")),
        "output_tokens": usage.get("completion_tokens", timings.get("predicted_n")),
        "finish_reason": finish_reason,
        "prompt_tokens_per_second": timings.get("prompt_per_second"),
        "output_tokens_per_second": timings.get("predicted_per_second"),
        "prompt_duration_ms": timings.get("prompt_ms"),
        "output_duration_ms": timings.get("predicted_ms"),
    }
    if finish_reason == "length":
        raise ValueError(
            f"OpenAI-compatible output reached the {output_tokens}-token limit before completing JSON"
        )
    try:
        plan = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"OpenAI-compatible backend returned invalid JSON ({len(content)} characters): {error}"
        ) from error
    return plan, metrics


def _request_model(*, backend: str, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if backend == "ollama":
        return _request_ollama(**kwargs)
    if backend == "openai":
        return _request_openai(**kwargs)
    raise ValueError(f"Unsupported backend: {backend}")


def normalize_plan(
    plan: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = dict(plan)
    dependencies = []
    for rule in candidate["rules"]:
        for reference in rule.get("references", []):
            dependencies.append(
                {
                    "source_rule_id": rule["rule_id"],
                    "target_rule_id": reference["target"],
                    "relation": "cites",
                    "clauses": [reference["i"]],
                    "occurrence_ids": [reference["occurrence_id"]],
                }
            )
    removed_count = len(plan.get("dependencies", [])) if isinstance(
        plan.get("dependencies"), list
    ) else 0
    normalized["dependencies"] = dependencies
    return normalized, {
        "mechanically_rebuilt_dependency_count": len(dependencies),
        "discarded_generated_dependency_count": removed_count,
    }


def validate_plan(plan: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if plan.get("task_id") != candidate["task_id"]:
        errors.append("task_id does not match")
    source_rules = {rule["rule_id"]: rule for rule in candidate["rules"]}
    source_indexes = {
        clause["i"] for rule in candidate["rules"] for clause in rule["clauses"]
    }
    rule_indexes = {
        rule_id: {clause["i"] for clause in rule["clauses"]}
        for rule_id, rule in source_rules.items()
    }
    expected_occurrences: dict[str, tuple[str, Mapping[str, Any]]] = {}
    grounded_rule_ids = set(source_rules)
    for rule_id, rule in source_rules.items():
        for reference in rule.get("references", []):
            occurrence_id = reference["occurrence_id"]
            if occurrence_id in expected_occurrences:
                errors.append(f"source occurrence id is duplicated: {occurrence_id}")
            expected_occurrences[occurrence_id] = (rule_id, reference)
            grounded_rule_ids.add(reference["target"])

    groups = plan.get("groups")
    exceptions = plan.get("exceptions")
    examples = plan.get("examples")
    dependencies = plan.get("dependencies")
    for name, value in (
        ("groups", groups),
        ("exceptions", exceptions),
        ("examples", examples),
        ("dependencies", dependencies),
    ):
        if not isinstance(value, list):
            errors.append(f"{name} must be an array")
    if errors:
        return {"passed": False, "task_id": candidate["task_id"], "errors": errors}

    group_ids: set[str] = set()
    exception_ids: set[str] = set()
    covered: set[int] = set()
    ownership_counts: dict[int, int] = {}

    def cover(owner_id: object, indexes: object) -> None:
        if not isinstance(indexes, list):
            errors.append(f"{owner_id}: clauses must be an array")
            return
        for index in indexes:
            if not isinstance(index, int) or index not in source_indexes:
                errors.append(f"{owner_id}: clause {index} is not source-grounded")
                continue
            ownership_counts[index] = ownership_counts.get(index, 0) + 1
            covered.add(index)

    for group in groups:
        if not isinstance(group, Mapping):
            errors.append("group is not an object")
            continue
        group_id = group.get("id")
        if not isinstance(group_id, str) or not group_id:
            errors.append("invalid group id")
        elif group_id in group_ids:
            errors.append(f"duplicate group id: {group_id}")
        else:
            group_ids.add(group_id)
        if group.get("kind") not in PLAN_KINDS:
            errors.append(f"{group_id}: invalid kind")
        if group.get("force") not in PLAN_FORCES:
            errors.append(f"{group_id}: invalid force")
        semantics = group.get("semantics")
        if not isinstance(semantics, Mapping) or not semantics:
            errors.append(f"{group_id}: semantics must be a nonempty object")
        cover(group_id, group.get("clauses"))

    for exception in exceptions:
        if not isinstance(exception, Mapping):
            errors.append("exception is not an object")
            continue
        exception_id = exception.get("id")
        if not isinstance(exception_id, str) or not exception_id:
            errors.append("invalid exception id")
        elif exception_id in exception_ids:
            errors.append(f"duplicate exception id: {exception_id}")
        else:
            exception_ids.add(exception_id)
        target = exception.get("target")
        if not isinstance(target, Mapping):
            errors.append(f"{exception_id}: target must be an object")
        elif target.get("kind") == "group" and target.get("id") not in group_ids:
            errors.append(f"{exception_id}: target group does not resolve")
        elif target.get("kind") == "rule" and target.get("id") not in grounded_rule_ids:
            errors.append(f"{exception_id}: target rule is not source-grounded")
        elif target.get("kind") not in {"group", "rule"}:
            errors.append(f"{exception_id}: invalid target kind")
        if not isinstance(exception.get("condition"), Mapping):
            errors.append(f"{exception_id}: condition must be an object")
        if not isinstance(exception.get("effect"), Mapping):
            errors.append(f"{exception_id}: effect must be an object")
        if not isinstance(exception.get("precedence"), int):
            errors.append(f"{exception_id}: precedence must be an integer")
        cover(exception_id, exception.get("clauses"))

    cover("examples", examples)

    seen_occurrences: set[str] = set()
    for dependency in dependencies:
        if not isinstance(dependency, Mapping):
            errors.append("dependency is not an object")
            continue
        source_rule_id = dependency.get("source_rule_id")
        target_rule_id = dependency.get("target_rule_id")
        if source_rule_id not in source_rules:
            errors.append(f"dependency source {source_rule_id} is not source-grounded")
            allowed_indexes: set[int] = set()
        else:
            allowed_indexes = rule_indexes[source_rule_id]
        if target_rule_id not in grounded_rule_ids:
            errors.append(f"dependency target {target_rule_id} is not source-grounded")
        dependency_indexes = dependency.get("clauses")
        if not isinstance(dependency_indexes, list):
            errors.append(f"{source_rule_id}: dependency clauses must be an array")
        else:
            for index in dependency_indexes:
                if not isinstance(index, int) or index not in allowed_indexes:
                    errors.append(
                        f"{source_rule_id}: dependency clause {index} is outside its source rule"
                    )
        occurrence_ids = dependency.get("occurrence_ids")
        if not isinstance(occurrence_ids, list) or not occurrence_ids:
            errors.append(f"{source_rule_id}: dependency occurrence_ids must be nonempty")
            continue
        for occurrence_id in occurrence_ids:
            expected = expected_occurrences.get(occurrence_id)
            if expected is None:
                errors.append(
                    f"{source_rule_id}: dependency occurrence {occurrence_id} is not source-grounded"
                )
                continue
            expected_source, occurrence = expected
            if occurrence_id in seen_occurrences:
                errors.append(f"dependency occurrence {occurrence_id} is duplicated")
            seen_occurrences.add(occurrence_id)
            if source_rule_id != expected_source:
                errors.append(f"dependency occurrence {occurrence_id} has the wrong source")
            if target_rule_id != occurrence["target"]:
                errors.append(f"dependency occurrence {occurrence_id} has the wrong target")

    missing_occurrences = sorted(set(expected_occurrences).difference(seen_occurrences))
    if missing_occurrences:
        errors.append(f"missing dependency occurrences: {missing_occurrences[:20]}")

    missing = sorted(source_indexes.difference(covered))
    if missing:
        errors.append(f"missing clause indexes: {missing[:40]}")
    multiply_grounded = sorted(
        index for index, count in ownership_counts.items() if count > 1
    )
    if multiply_grounded:
        errors.append(
            f"clause indexes assigned more than once: {multiply_grounded[:40]}"
        )
    return {
        "passed": not errors,
        "task_id": candidate["task_id"],
        "source_clause_count": len(source_indexes),
        "covered_clause_count": len(covered),
        "group_count": len(group_ids),
        "multiply_grounded_clause_count": len(multiply_grounded),
        "errors": errors,
    }


def clean_plan_for_patch(
    plan: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[dict[str, Any], list[int]]:
    """Retain valid, singly owned semantics and return clauses still needing work."""
    source_indexes = {
        clause["i"] for rule in candidate["rules"] for clause in rule["clauses"]
    }
    source_rule_ids = {rule["rule_id"] for rule in candidate["rules"]}
    grounded_rule_ids = source_rule_ids | {
        reference["target"]
        for rule in candidate["rules"]
        for reference in rule.get("references", [])
    }
    claimed: set[int] = set()
    groups: list[dict[str, Any]] = []
    group_ids: set[str] = set()

    for value in plan.get("groups", []):
        if not isinstance(value, Mapping):
            continue
        group_id = value.get("id")
        clauses = value.get("clauses")
        semantics = value.get("semantics")
        if (
            not isinstance(group_id, str)
            or not group_id
            or group_id in group_ids
            or value.get("kind") not in PLAN_KINDS
            or value.get("force") not in PLAN_FORCES
            or not isinstance(semantics, Mapping)
            or not semantics
            or not isinstance(clauses, list)
        ):
            continue
        retained = [
            index
            for index in clauses
            if isinstance(index, int)
            and index in source_indexes
            and index not in claimed
        ]
        if not retained:
            continue
        group = dict(value)
        group["clauses"] = retained
        groups.append(group)
        group_ids.add(group_id)
        claimed.update(retained)

    exceptions: list[dict[str, Any]] = []
    exception_ids: set[str] = set()
    for value in plan.get("exceptions", []):
        if not isinstance(value, Mapping):
            continue
        exception_id = value.get("id")
        target = value.get("target")
        clauses = value.get("clauses")
        target_resolves = isinstance(target, Mapping) and (
            (target.get("kind") == "group" and target.get("id") in group_ids)
            or (
                target.get("kind") == "rule"
                and target.get("id") in grounded_rule_ids
            )
        )
        if (
            not isinstance(exception_id, str)
            or not exception_id
            or exception_id in exception_ids
            or not target_resolves
            or not isinstance(value.get("condition"), Mapping)
            or not isinstance(value.get("effect"), Mapping)
            or not isinstance(value.get("precedence"), int)
            or not isinstance(clauses, list)
        ):
            continue
        retained = [
            index
            for index in clauses
            if isinstance(index, int)
            and index in source_indexes
            and index not in claimed
        ]
        if not retained:
            continue
        exception = dict(value)
        exception["clauses"] = retained
        exceptions.append(exception)
        exception_ids.add(exception_id)
        claimed.update(retained)

    examples = []
    for index in plan.get("examples", []):
        if (
            isinstance(index, int)
            and index in source_indexes
            and index not in claimed
        ):
            examples.append(index)
            claimed.add(index)

    cleaned, _ = normalize_plan(
        {
            "task_id": candidate["task_id"],
            "groups": groups,
            "exceptions": exceptions,
            "examples": examples,
        },
        candidate,
    )
    return cleaned, sorted(source_indexes.difference(claimed))


def build_patch_prompt(
    candidate: Mapping[str, Any],
    base_plan: Mapping[str, Any],
    target_indexes: Sequence[int],
    errors: Sequence[str],
) -> str:
    targets = set(target_indexes)
    focused_rules = []
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
        focused_rules.append(
            {
                "rule_id": rule["rule_id"],
                "parent": rule.get("parent"),
                "clauses": [
                    {**clause, "repair_target": clause["i"] in targets}
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
    payload = {
        "task_id": candidate["task_id"],
        "target_clause_indexes": list(target_indexes),
        "source_context": focused_rules,
        "retained_group_ids": [group["id"] for group in base_plan["groups"]],
        "retained_exception_ids": [
            exception["id"] for exception in base_plan["exceptions"]
        ],
        "validation_errors": list(errors),
    }
    return (
        SYSTEM_PROMPT
        + "\nPATCH MODE: Return semantic owners only for target_clause_indexes. "
        "Every target index must occur exactly once across groups, exceptions, and "
        "examples. Do not return neighboring context indexes. IDs must not duplicate "
        "retained IDs. The patch is merged mechanically with the retained valid plan.\n"
        + "RESPONSE SCHEMA:\n"
        + json.dumps(response_schema(), separators=(",", ":"), ensure_ascii=False)
        + "\nFOCUSED REPAIR INPUT:\n"
        + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    )


def merge_plan_patch(
    base_plan: Mapping[str, Any],
    patch: Mapping[str, Any],
    candidate: Mapping[str, Any],
    target_indexes: Sequence[int],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    target_set = set(target_indexes)
    patch_indexes: list[Any] = []
    for owner in patch.get("groups", []):
        if isinstance(owner, Mapping) and isinstance(owner.get("clauses"), list):
            patch_indexes.extend(owner["clauses"])
    for owner in patch.get("exceptions", []):
        if isinstance(owner, Mapping) and isinstance(owner.get("clauses"), list):
            patch_indexes.extend(owner["clauses"])
    if isinstance(patch.get("examples"), list):
        patch_indexes.extend(patch["examples"])
    scope_errors = []
    outside = sorted(
        {index for index in patch_indexes if isinstance(index, int)} - target_set
    )
    if outside:
        scope_errors.append(f"patch contains non-target clause indexes: {outside[:40]}")
    if patch.get("task_id") != candidate["task_id"]:
        scope_errors.append("patch task_id does not match")
    merged = {
        "task_id": candidate["task_id"],
        "groups": list(base_plan["groups"]) + list(patch.get("groups", [])),
        "exceptions": list(base_plan["exceptions"])
        + list(patch.get("exceptions", [])),
        "examples": list(base_plan["examples"]) + list(patch.get("examples", [])),
    }
    normalized, normalization = normalize_plan(merged, candidate)
    normalization["patch_target_clause_count"] = len(target_set)
    normalization["patch_returned_clause_count"] = len(patch_indexes)
    return normalized, normalization, scope_errors


def audit_plan_against_authoring(
    plan: Mapping[str, Any], reference: Mapping[str, Any]
) -> dict[str, Any]:
    def pair_scores(
        expected: Mapping[int, set[str]], predicted: Mapping[int, set[str]]
    ) -> dict[str, Any]:
        expected_pairs = {
            (index, value) for index, values in expected.items() for value in values
        }
        predicted_pairs = {
            (index, value)
            for index, values in predicted.items()
            if index in expected
            for value in values
        }
        true_pairs = expected_pairs.intersection(predicted_pairs)
        precision = len(true_pairs) / max(len(predicted_pairs), 1)
        recall = len(true_pairs) / max(len(expected_pairs), 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-12)
        exact_clause_count = sum(
            predicted.get(index, set()) == values for index, values in expected.items()
        )
        return {
            "expected_pair_count": len(expected_pairs),
            "predicted_pair_count": len(predicted_pairs),
            "true_positive_pair_count": len(true_pairs),
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "exact_clause_set_count": exact_clause_count,
            "exact_clause_set_accuracy": round(
                exact_clause_count / max(len(expected), 1), 6
            ),
        }

    expected_kinds: dict[int, set[str]] = {}
    expected_forces: dict[int, set[str]] = {}
    for unit in reference.get("units", []):
        kind = unit.get("k")
        force = unit.get("f")
        for index in unit.get("c", []):
            if not isinstance(index, int):
                continue
            if isinstance(kind, str):
                expected_kinds.setdefault(index, set()).add(kind)
            if isinstance(force, str):
                expected_forces.setdefault(index, set()).add(force)

    predicted_kinds: dict[int, set[str]] = {}
    predicted_forces: dict[int, set[str]] = {}
    for group in plan.get("groups", []):
        for index in group.get("clauses", []):
            if isinstance(index, int):
                predicted_kinds.setdefault(index, set()).add(str(group.get("kind")))
                predicted_forces.setdefault(index, set()).add(str(group.get("force")))

    expected_kind_families = {
        index: {"procedure" if kind == "decision" else kind for kind in kinds}
        for index, kinds in expected_kinds.items()
    }
    predicted_kind_families = {
        index: {"procedure" if kind == "decision" else kind for kind in kinds}
        for index, kinds in predicted_kinds.items()
    }

    evaluated = exact_kind = family_kind = force_exact = 0
    for index, kinds in expected_kinds.items():
        if index not in predicted_kinds:
            continue
        evaluated += 1
        kinds_predicted = predicted_kinds[index]
        forces_predicted = predicted_forces.get(index, set())
        if kinds_predicted.intersection(kinds):
            exact_kind += 1
        normalized_kinds = {
            "procedure" if kind == "decision" else kind for kind in kinds
        }
        normalized_predictions = {
            "procedure" if kind == "decision" else kind for kind in kinds_predicted
        }
        if normalized_predictions.intersection(normalized_kinds):
            family_kind += 1
        if forces_predicted.intersection(expected_forces.get(index, set())):
            force_exact += 1
    return {
        "evaluated_clause_count": evaluated,
        "kind_exact_count": exact_kind,
        "kind_exact_accuracy": round(exact_kind / max(evaluated, 1), 6),
        "kind_family_accuracy": round(family_kind / max(evaluated, 1), 6),
        "force_accuracy": round(force_exact / max(evaluated, 1), 6),
        "kind_pair_scores": pair_scores(expected_kinds, predicted_kinds),
        "kind_family_pair_scores": pair_scores(
            expected_kind_families, predicted_kind_families
        ),
        "force_pair_scores": pair_scores(expected_forces, predicted_forces),
        "expected_exception_count": len(reference.get("exceptions", [])),
        "predicted_exception_count": len(plan.get("exceptions", [])),
    }


def _paths_for_task(output_dir: Path, task_id: str) -> tuple[Path, Path, Path]:
    return (
        output_dir / "candidates" / f"{task_id}.json",
        output_dir / "plans" / f"{task_id}.json",
        output_dir / "reports" / f"{task_id}.json",
    )


def process_task(
    task_path: Path,
    *,
    authoring_dir: Path,
    reference_dir: Path | None,
    output_dir: Path,
    model: str,
    backend: str,
    endpoint: str,
    context_tokens: int,
    output_tokens: int,
    timeout: int,
    repair_attempts: int,
    seed: int,
    dry_run: bool,
    force: bool,
) -> dict[str, Any]:
    task = load_json(task_path)
    task_id = str(task["task_id"])
    authoring = load_json(authoring_dir / f"{task_id}.json")
    candidate = build_candidate_view(task, authoring)
    candidate_path, plan_path, report_path = _paths_for_task(output_dir, task_id)
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_bytes(canonical_json_bytes(candidate))
    prompt = build_prompt(candidate)
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper()

    cached_report: dict[str, Any] | None = None
    cached_plan: dict[str, Any] | None = None
    if not force and plan_path.exists() and report_path.exists():
        cached_report = load_json(report_path)
        cached_plan = load_json(plan_path)
        cached_validation = validate_plan(cached_plan, candidate)
        if (
            cached_report.get("prompt_sha256") == prompt_sha256
            and cached_report.get("model") == model
            and cached_report.get("backend") == backend
            and cached_report.get("endpoint") == endpoint
            and cached_report.get("seed") == seed
            and cached_report.get("validator_version") == VALIDATOR_VERSION
            and cached_validation.get("passed") is True
        ):
            return {
                **cached_report,
                "validation": cached_validation,
                "cached": True,
            }

    base_report: dict[str, Any] = {
        "format": "iupac-bluebook-local-semantic-compaction-report",
        "format_version": "1.0.0",
        "task_id": task_id,
        "task_sha256": task["task_sha256"],
        "model": model,
        "backend": backend,
        "endpoint": endpoint,
        "seed": seed,
        "validator_version": VALIDATOR_VERSION,
        "response_schema_sha256": _sha256(response_schema()),
        "prompt_sha256": prompt_sha256,
        "prompt_bytes": len(prompt.encode("utf-8")),
        "candidate_sha256": _sha256(candidate),
    }
    if dry_run:
        return {**base_report, "dry_run": True}

    attempts = []
    plan: dict[str, Any] = {}
    generated_plan: dict[str, Any] = {}
    best_plan: dict[str, Any] = {}
    best_validation: dict[str, Any] = {"passed": False, "errors": ["not generated"]}
    best_score = (-1, -1, -1)
    validation: dict[str, Any] = {"passed": False, "errors": ["not generated"]}
    effective_context = context_tokens
    estimated_prompt_tokens = 0
    request_prompt = prompt
    request_output_tokens = output_tokens
    repair_base: dict[str, Any] | None = None
    repair_targets: list[int] = []

    if (
        cached_plan is not None
        and cached_report is not None
        and cached_report.get("candidate_sha256") == _sha256(candidate)
    ):
        cleaned_plan, repair_targets = clean_plan_for_patch(cached_plan, candidate)
        cleaned_validation = validate_plan(cleaned_plan, candidate)
        cleanup_attempt = {
            "attempt": 0,
            "mode": "deterministic_cache_cleanup",
            "source_plan_sha256": _sha256(cached_plan),
            "validation": cleaned_validation,
        }
        attempts.append(cleanup_attempt)
        if cleaned_validation["passed"]:
            plan_path.write_bytes(canonical_json_bytes(cleaned_plan))
            report = {
                **base_report,
                "plan_sha256": _sha256(cleaned_plan),
                "attempts": attempts,
                "estimated_prompt_tokens": 0,
                "context_tokens": context_tokens,
                "validation": cleaned_validation,
            }
            if reference_dir is not None:
                reference_path = reference_dir / f"{task_id}.json"
                if reference_path.exists():
                    report["benchmark"] = audit_plan_against_authoring(
                        cleaned_plan, load_json(reference_path)
                    )
            report_path.write_bytes(canonical_json_bytes(report))
            return report
        if repair_targets:
            repair_base = cleaned_plan
            request_prompt = build_patch_prompt(
                candidate,
                repair_base,
                repair_targets,
                cleaned_validation["errors"],
            )
            request_output_tokens = min(
                output_tokens, max(2048, 768 + 192 * len(repair_targets))
            )
            best_plan = cleaned_plan
            best_validation = cleaned_validation
            best_score = (
                int(cleaned_validation.get("covered_clause_count", 0)),
                0,
                -len(cleaned_validation.get("errors", [])),
            )
    for attempt in range(repair_attempts + 1):
        estimated_prompt_tokens = math.ceil(
            len(request_prompt.encode("utf-8")) / 3
        )
        required_context = estimated_prompt_tokens + request_output_tokens
        effective_context = min(
            context_tokens,
            max(32768, math.ceil(required_context / 16384) * 16384),
        )
        if effective_context < required_context:
            validation = {
                "passed": False,
                "errors": [
                    f"Prompt and output budget require about {required_context} tokens, "
                    f"above configured context {context_tokens}"
                ],
            }
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                    "context_tokens": effective_context,
                    "request_error": validation["errors"][0],
                    "validation": validation,
                }
            )
            break
        try:
            generated_plan, metrics = _request_model(
                backend=backend,
                endpoint=endpoint,
                model=model,
                prompt=request_prompt,
                context_tokens=effective_context,
                output_tokens=request_output_tokens,
                timeout=timeout,
                seed=seed,
            )
        except (OSError, TypeError, ValueError, RuntimeError) as error:
            validation = {"passed": False, "errors": [str(error)]}
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                    "context_tokens": effective_context,
                    "request_error": str(error),
                    "validation": validation,
                }
            )
            if attempt == repair_attempts:
                break
            if repair_base is None:
                request_prompt = (
                    prompt
                    + "\nTHE PREVIOUS ATTEMPT FAILED BEFORE VALIDATION:\n"
                    + str(error)
                    + "\nReturn a complete but substantially more compact plan as JSON only."
                )
            else:
                request_prompt += (
                    "\nTHE PREVIOUS PATCH FAILED BEFORE VALIDATION:\n"
                    + str(error)
                    + "\nReturn the same scoped patch as JSON only."
                )
            continue
        if repair_base is None:
            plan, normalization = normalize_plan(generated_plan, candidate)
            scope_errors: list[str] = []
        else:
            plan, normalization, scope_errors = merge_plan_patch(
                repair_base, generated_plan, candidate, repair_targets
            )
        validation = validate_plan(plan, candidate)
        if scope_errors:
            validation["passed"] = False
            validation["errors"] = scope_errors + validation["errors"]
        covered = int(validation.get("covered_clause_count", 0))
        score = (
            covered,
            -int(validation.get("multiply_grounded_clause_count", 0)),
            -len(validation.get("errors", [])),
        )
        if validation.get("passed") is True or score > best_score:
            best_plan = plan
            best_validation = validation
            best_score = score
        attempts.append(
            {
                "attempt": attempt + 1,
                "estimated_prompt_tokens": estimated_prompt_tokens,
                "context_tokens": effective_context,
                "output_token_budget": request_output_tokens,
                "mode": "patch" if repair_base is not None else "full",
                "metrics": metrics,
                "normalization": normalization,
                "validation": validation,
            }
        )
        if validation["passed"] or attempt == repair_attempts:
            break
        repair_source = best_plan if best_plan else plan
        repair_base, repair_targets = clean_plan_for_patch(repair_source, candidate)
        cleanup_validation = validate_plan(repair_base, candidate)
        attempts.append(
            {
                "attempt": attempt + 1,
                "mode": "deterministic_post_patch_cleanup",
                "source_plan_sha256": _sha256(repair_source),
                "validation": cleanup_validation,
            }
        )
        if cleanup_validation["passed"]:
            plan = repair_base
            validation = cleanup_validation
            best_plan = repair_base
            best_validation = cleanup_validation
            break
        request_prompt = build_patch_prompt(
            candidate, repair_base, repair_targets, validation["errors"]
        )
        request_output_tokens = min(
            output_tokens, max(2048, 768 + 192 * len(repair_targets))
        )
    if best_plan:
        plan = best_plan
        validation = best_validation
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_bytes(canonical_json_bytes(plan))
    report = {
        **base_report,
        "plan_sha256": _sha256(plan),
        "attempts": attempts,
        "estimated_prompt_tokens": estimated_prompt_tokens,
        "context_tokens": effective_context,
        "validation": validation,
    }
    if reference_dir is not None:
        reference_path = reference_dir / f"{task_id}.json"
        if reference_path.exists():
            report["benchmark"] = audit_plan_against_authoring(
                plan, load_json(reference_path)
            )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_bytes(canonical_json_bytes(report))
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build resumable local-model semantic compaction plans"
    )
    parser.add_argument("tasks", nargs="+", type=Path)
    parser.add_argument("--authoring-dir", type=Path, default=DEFAULT_AUTHORING_DIR)
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--backend", choices=("ollama", "openai"), default="ollama")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--context-tokens", type=int, default=131072)
    parser.add_argument("--output-tokens", type=int, default=32768)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--repair-attempts", type=int, default=2)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--dry-run", action="store_true")
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
                    authoring_dir=args.authoring_dir,
                    reference_dir=args.reference_dir,
                    output_dir=args.output_dir,
                    model=args.model,
                    backend=args.backend,
                    endpoint=args.endpoint,
                    context_tokens=args.context_tokens,
                    output_tokens=args.output_tokens,
                    timeout=args.timeout,
                    repair_attempts=args.repair_attempts,
                    seed=args.seed,
                    dry_run=args.dry_run,
                    force=args.force,
                )
            )
    except (KeyError, OSError, TypeError, ValueError, RuntimeError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1
    output = {
        "format": "iupac-bluebook-local-semantic-compaction-run",
        "format_version": "1.0.0",
        "model": args.model,
        "backend": args.backend,
        "task_count": len(reports),
        "passed": all(
            report.get("dry_run") is True
            or report.get("validation", {}).get("passed") is True
            for report in reports
        ),
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
