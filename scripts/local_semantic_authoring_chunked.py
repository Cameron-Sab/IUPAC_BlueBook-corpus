from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

if __package__:
    from scripts.build_compact_semantic_tasks import canonical_json_bytes, load_json
    from scripts.compile_semantic_authoring import AuthoringError, Expander
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
    from compile_semantic_authoring import (  # type: ignore[no-redef]
        AuthoringError,
        Expander,
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


PARTITION_VALIDATOR_VERSION = "2.2.0"
CLAUSE_ROLES = [
    "heading",
    "scope",
    "definition",
    "condition",
    "effect",
    "constraint",
    "permission",
    "prohibition",
    "preference_criterion",
    "tie_continuation",
    "procedure_step",
    "mapping_entry",
    "exception",
    "cross_reference",
    "table_data",
    "table_layout",
    "figure_asset",
    "example",
    "note",
    "rationale",
    "history",
    "correction_event",
    "source_metadata",
]
CLAUSE_FORCES = [
    "normative",
    "informative",
    "illustrative",
    "source_metadata",
    "correction",
]
OBJECT_REF_KINDS = [
    "record",
    "clause",
    "semantic_unit",
    "expression",
    "statement",
    "decision_stage",
    "exception",
    "table",
    "table_column",
    "table_row",
    "table_cell",
    "table_footnote",
    "figure",
    "example",
    "correction_application",
    "reference",
    "symbol",
    "chapter",
    "rule",
    "historical_rule",
    "external",
]
REASON_CODE_SCHEMA = {
    "type": "string",
    "pattern": "^[a-z][a-z0-9_.]*$",
}
IDENTIFIER_SCHEMA = {
    "type": "string",
    "pattern": "^[A-Za-z][A-Za-z0-9_.:-]*$",
}
SYMBOL_ID_SCHEMA = {
    "type": "string",
    "pattern": "^[a-z][a-z0-9_.]*$",
}
RULE_ID_SCHEMA = {
    "type": "string",
    "pattern": "^P-[0-9]+(?:\\.[0-9]+)*(?:\\([a-z0-9]+\\))?$",
}
COMPARE_RELATIONS = [
    "eq", "ne", "lt", "le", "gt", "ge", "contains", "member_of", "same_set"
]
SCOPE_REGIMES = [
    "preferred_iupac_name",
    "preselected_name",
    "general_nomenclature",
    "retained_name",
    "class_specific",
    "all",
]
UNIT_FORCES = ["required", "permitted", "prohibited", "preference", "definition"]
NONOPERATIVE_REASON_CODES = [
    "heading_or_title",
    "example_label",
    "illustrative_example",
    "explanatory_note",
    "historical_context",
    "rationale",
    "source_navigation",
    "citation_only",
    "structural_layout",
    "empty_layout_cell",
    "figure_only",
    "correction_marker",
    "bibliographic_material",
    "duplicate_rendering",
    "source_artifact",
]


def response_schema(
    maximum_clauses: int,
    target_indexes: Sequence[int] | None = None,
    mechanical_indexes: set[int] | None = None,
) -> dict[str, Any]:
    expression = {"$ref": "#/$defs/expression"}
    statement = {"$ref": "#/$defs/statement"}
    statement_block = {"$ref": "#/$defs/statement_block"}
    object_ref = {"$ref": "#/$defs/object_ref"}
    bindings = {"$ref": "#/$defs/bindings"}

    def operation(
        name: str,
        operands: list[dict[str, Any]],
        *,
        repeat: dict[str, Any] | None = None,
        min_repeats: int = 0,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "array",
            "minItems": 1 + len(operands) + min_repeats,
            "prefixItems": [{"type": "string", "const": name}, *operands],
        }
        if repeat is None:
            result["maxItems"] = 1 + len(operands)
        else:
            result["items"] = repeat
        return result

    expression_variants = [
        operation("lit", [{}]),
        operation("var", [IDENTIFIER_SCHEMA]),
        operation("get", [expression, SYMBOL_ID_SCHEMA]),
        operation("pred", [SYMBOL_ID_SCHEMA], repeat=expression),
        operation("call", [SYMBOL_ID_SCHEMA], repeat=expression),
        operation("all", [], repeat=expression, min_repeats=1),
        operation("any", [], repeat=expression, min_repeats=1),
        operation("not", [expression]),
        operation("exists", [IDENTIFIER_SCHEMA, expression, expression]),
        operation("forall", [IDENTIFIER_SCHEMA, expression, expression]),
        operation(
            "cmp",
            [{"type": "string", "enum": COMPARE_RELATIONS}, expression, expression],
        ),
        operation("lookup", [IDENTIFIER_SCHEMA, expression, IDENTIFIER_SCHEMA]),
        operation("outcome", [RULE_ID_SCHEMA, IDENTIFIER_SCHEMA]),
    ]
    statement_variants = [
        operation("seq", [], repeat=statement),
        operation("if", [expression, statement_block, statement_block]),
        operation("set", [IDENTIFIER_SCHEMA, expression]),
        operation(
            "xform",
            [IDENTIFIER_SCHEMA, SYMBOL_ID_SCHEMA],
            repeat=expression,
        ),
        operation(
            "render",
            [IDENTIFIER_SCHEMA, IDENTIFIER_SCHEMA, expression],
        ),
        operation("reject", [IDENTIFIER_SCHEMA, REASON_CODE_SCHEMA]),
        operation(
            "invoke",
            [
                RULE_ID_SCHEMA,
                {"type": "object", "additionalProperties": expression},
            ],
        ),
        operation(
            "each",
            [IDENTIFIER_SCHEMA, expression, statement_block, expression],
        ),
        operation("emit", [expression]),
        operation("assert", [expression, REASON_CODE_SCHEMA]),
    ]
    scope = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "r": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "enum": SCOPE_REGIMES},
            },
            "if": expression,
        },
    }
    common_unit_properties: dict[str, Any] = {
        "id": {"type": "string", "minLength": 1},
        "k": {
            "type": "string",
            "enum": [
                "rule",
                "definition",
                "procedure",
                "constraint",
                "mapping",
                "decision",
            ],
        },
        "f": {
            "type": "string",
            "enum": UNIT_FORCES,
        },
        "c": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "integer"},
        },
        "scope": scope,
        "in": bindings,
        "out": bindings,
    }
    stage = {
        "type": "object",
        "additionalProperties": False,
        "required": ["key", "cmp"],
        "properties": {
            "id": {"type": "string"},
            "c": {"type": "array", "items": {"type": "integer"}},
            "if": expression,
            "key": expression,
            "cmp": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
                "prefixItems": [
                    {
                        "type": "string",
                        "enum": ["numeric", "lexicographic", "ordered_table", "set_order", "custom"],
                    },
                    {
                        "type": "string",
                        "enum": ["minimum", "maximum", "source_order", "symbol_defined"],
                    },
                    {"oneOf": [SYMBOL_ID_SCHEMA, {"type": "null"}]},
                    {"oneOf": [IDENTIFIER_SCHEMA, {"type": "null"}]},
                ],
            },
        },
    }
    kind_properties = {
        "rule": {
            "if": expression,
            "then": statement_block,
            "else": statement_block,
        },
        "definition": {
            "term": {"type": "string"},
            "entity": {"type": "string"},
            "value": expression,
        },
        "procedure": {"steps": statement_block},
        "constraint": {
            "assert": expression,
            "violation": statement_block,
        },
        "mapping": {"table": {"type": "string"}},
        "decision": {
            "candidates": expression,
            "stages": {"type": "array", "minItems": 1, "items": stage},
            "tie": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "prefixItems": [
                    {
                        "type": "string",
                        "enum": ["retain_coequal", "apply_fallback", "reject_ambiguous"],
                    },
                    {"oneOf": [object_ref, {"type": "null"}]},
                ],
            },
        },
    }
    unit_variants = [
        {
            "type": "object",
            "additionalProperties": False,
            "required": required,
            "properties": {
                **common_unit_properties,
                **kind_properties[kind],
                "k": {"type": "string", "const": kind},
            },
        }
        for kind, required in (
            ("rule", ["id", "k", "c", "if"]),
            ("definition", ["id", "k", "c", "term", "entity", "value"]),
            ("procedure", ["id", "k", "c", "steps"]),
            ("constraint", ["id", "k", "c", "assert", "violation"]),
            ("mapping", ["id", "k", "c", "table"]),
            ("decision", ["id", "k", "c", "candidates", "stages", "tie"]),
        )
    ]
    decision_variants = [
        {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "prefixItems": [
                {"type": "string", "enum": CLAUSE_ROLES},
                {"type": "string", "enum": CLAUSE_FORCES},
                {"type": "string", "const": "compile"},
            ],
        },
        {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "prefixItems": [
                {"type": "string", "enum": CLAUSE_ROLES},
                {"type": "string", "enum": CLAUSE_FORCES},
                {"type": "string", "const": "skip"},
                {"type": "string", "enum": NONOPERATIVE_REASON_CODES},
            ],
        },
        {
            "type": "array",
            "minItems": 5,
            "maxItems": 5,
            "prefixItems": [
                {"type": "string", "enum": CLAUSE_ROLES},
                {"type": "string", "enum": CLAUSE_FORCES},
                {"type": "string", "const": "supersede"},
                {"type": "array", "items": {"type": "string"}},
                {"type": "array", "items": {"type": "integer"}},
            ],
        },
    ]

    def clause_item(index: int | None = None) -> dict[str, Any]:
        mechanical = index is not None and index in (mechanical_indexes or set())
        decisions = (
            [{"type": "null"}]
            if mechanical
            else decision_variants
            if index is not None
            else [{"type": "null"}, *decision_variants]
        )
        index_schema: dict[str, Any] = {"type": "integer"}
        if index is not None:
            index_schema["const"] = index
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["i", "decision"],
            "properties": {
                "i": index_schema,
                "decision": {"oneOf": decisions},
            },
        }

    clause_collection: dict[str, Any] = {
        "type": "array",
        "minItems": maximum_clauses,
        "maxItems": maximum_clauses,
    }
    if target_indexes is None:
        clause_collection["items"] = clause_item()
    else:
        clause_collection["prefixItems"] = [
            clause_item(index) for index in target_indexes
        ]
    return {
        "$defs": {
            "expression": {"oneOf": expression_variants},
            "statement": {"oneOf": statement_variants},
            "statement_block": {"type": "array", "items": statement},
            "bindings": {
                "type": "array",
                "items": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "prefixItems": [
                        IDENTIFIER_SCHEMA,
                        {"type": "string"},
                    ],
                },
            },
            "object_ref": {
                "oneOf": [
                    {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 2,
                        "prefixItems": [
                            {"type": "string", "const": "clause"},
                            {"type": "integer"},
                        ],
                    },
                    {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 2,
                        "prefixItems": [
                            {
                                "type": "string",
                                "enum": [
                                    kind for kind in OBJECT_REF_KINDS if kind != "clause"
                                ],
                            },
                            {"type": "string"},
                        ],
                    },
                ]
            },
        },
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
            "clauses": clause_collection,
            "symbols": {
                "type": "array",
                "maxItems": maximum_clauses,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "k", "d", "ret"],
                    "properties": {
                        "id": SYMBOL_ID_SCHEMA,
                        "k": {
                            "type": "string",
                            "enum": ["entity_type", "predicate", "function", "transformation", "comparator", "reason_code"],
                        },
                        "d": {"type": "string", "minLength": 1},
                        "a": bindings,
                        "ret": {"type": "string", "minLength": 1},
                        "g": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 3,
                            "prefixItems": [
                                {"type": "string"},
                                {"type": "array", "items": object_ref},
                                {"oneOf": [{"type": "string"}, {"type": "null"}]},
                            ],
                        },
                    },
                },
            },
            "units": {
                "type": "array",
                "maxItems": maximum_clauses,
                "items": {"oneOf": unit_variants},
            },
            "exceptions": {
                "type": "array",
                "maxItems": maximum_clauses,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "c", "if", "target", "mode", "order"],
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "c": {"type": "array", "items": {"type": "integer"}},
                        "if": expression,
                        "target": object_ref,
                        "mode": {
                            "type": "string",
                            "enum": ["suppress", "replace", "add_guard", "redirect", "change_precedence"],
                        },
                        "order": {"type": "integer", "minimum": 1},
                        "replacement": {
                            "oneOf": [object_ref, {"type": "null"}]
                        },
                        "guard": expression,
                        "redirect": object_ref,
                        "specificity": {"type": "integer", "minimum": 0},
                    },
                },
            },
            "examples": {
                "type": "array",
                "maxItems": maximum_clauses,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "c"],
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "c": {"type": "array", "items": {"type": "integer"}},
                        "input": {},
                        "ok": {"type": "array", "items": {"type": "string"}},
                        "bad": {"type": "array", "items": {"type": "string"}},
                        "shows": {"type": "array", "items": object_ref},
                        "why": {"type": "string"},
                    },
                },
            },
        },
    }


def count_prompt_tokens(
    endpoint: str, prompt: str, *, timeout: int = 30
) -> tuple[int, str]:
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/tokenize",
        data=json.dumps({"content": prompt}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        tokens = payload.get("tokens")
        if isinstance(tokens, list):
            return len(tokens), "server_tokenizer"
    except (OSError, ValueError, urllib.error.URLError):
        pass
    return math.ceil(len(prompt.encode("utf-8")) / 3), "utf8_fallback"


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
    mechanical_indexes = set(retained["mechanical_clause_indexes"])
    return (
        SYSTEM_PROMPT
        + "\nPARTITION MODE: Author only target_clause_indexes. The clauses array "
        "must contain one {i,decision} object per target in the exact given order. "
        "For every mechanical_clause_index, decision MUST be null. For every other "
        "target, decision MUST be compile, skip, or supersede. "
        "Never include a mechanical_clause_index in any c ownership array. "
        "All c arrays in units, exceptions, and examples must be subsets of the "
        "targets. Use IDs specific to the shown source rule so independently authored "
        "partitions merge without collisions. Neighbor clauses are context only.\n"
        + "RESPONSE SCHEMA:\n"
        + json.dumps(
            response_schema(
                len(target_indexes), target_indexes, mechanical_indexes
            ),
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\nCOMPACT AUTHORING EXAMPLE:\n"
        + json.dumps(example, separators=(",", ":"), ensure_ascii=False)
        + "\nMECHANICALLY RETAINED OBJECTS:\n"
        + json.dumps(retained, separators=(",", ":"), ensure_ascii=False)
        + "\nFOCUSED SOURCE PARTITION:\n"
        + json.dumps(focused, separators=(",", ":"), ensure_ascii=False)
    )


def validate_patch(
    patch: Mapping[str, Any],
    task_id: str,
    target_indexes: Sequence[int],
    *,
    task: Mapping[str, Any] | None = None,
    bootstrap: Mapping[str, Any] | None = None,
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
            elif isinstance(decision, list) and (
                decision[0] not in CLAUSE_ROLES
                or decision[1] not in CLAUSE_FORCES
            ):
                errors.append(
                    f"clause {item['i']} has invalid role or force"
                )
            if bootstrap is not None and 1 <= item["i"] <= len(bootstrap["clauses"]):
                is_mechanical = bootstrap["clauses"][item["i"] - 1] is None
                if is_mechanical and decision is not None:
                    errors.append(
                        f"mechanically proven clause {item['i']} decision must be null"
                    )
                elif not is_mechanical and decision is None:
                    errors.append(
                        f"nonmechanical clause {item['i']} needs a decision"
                    )
        if observed != expected:
            errors.append(f"clause indexes must exactly equal {expected}")
    target_set = set(expected)
    mechanical_set = (
        {
            index
            for index, decision in enumerate(bootstrap["clauses"], 1)
            if decision is None
        }
        if bootstrap is not None
        else set()
    )
    schema_errors = sorted(
        Draft202012Validator(
            response_schema(len(expected), expected, mechanical_set)
            if bootstrap is not None
            else response_schema(len(expected))
        ).iter_errors(patch),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    errors.extend(
        "response schema at /"
        + "/".join(map(str, error.absolute_path))
        + f": {error.message}"
        for error in schema_errors[:10]
    )
    object_ids: set[str] = set()
    required_by_kind = {
        "rule": {"id", "k", "c", "if"},
        "definition": {"id", "k", "c", "term", "entity", "value"},
        "procedure": {"id", "k", "c", "steps"},
        "constraint": {"id", "k", "c", "assert", "violation"},
        "mapping": {"id", "k", "c", "table"},
        "decision": {"id", "k", "c", "candidates", "stages", "tie"},
    }
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
        mechanical_owned = sorted(
            {
                index
                for item in values
                if isinstance(item, Mapping) and isinstance(item.get("c"), list)
                for index in item["c"]
                if index in mechanical_set
            }
        )
        if mechanical_owned:
            errors.append(
                f"{collection} owns mechanical clauses: {mechanical_owned}"
            )
        for item in values:
            if not isinstance(item, Mapping):
                errors.append(f"{collection} item must be an object")
                continue
            if not isinstance(item.get("id"), str) or not item["id"]:
                errors.append(f"{collection} item needs a nonempty string id")
                continue
            if item["id"] in object_ids:
                errors.append(f"duplicate authored id in partition: {item['id']}")
            object_ids.add(item["id"])
            if collection == "units":
                required = required_by_kind.get(item.get("k"))
                if required is None:
                    errors.append(f"{item['id']}: unknown unit kind {item.get('k')}")
                else:
                    missing = sorted(required.difference(item))
                    if missing:
                        errors.append(f"{item['id']}: missing required fields {missing}")
            elif collection == "exceptions":
                missing = sorted(
                    {"id", "c", "if", "target", "mode", "order"}.difference(item)
                )
                if missing:
                    errors.append(f"{item['id']}: missing required fields {missing}")
    if not isinstance(patch.get("symbols"), list):
        errors.append("symbols must be an array")
    else:
        for item in patch["symbols"]:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            if item["id"] in object_ids:
                errors.append(f"duplicate authored id in partition: {item['id']}")
            object_ids.add(item["id"])
    owned_clause_indexes = {
        index
        for collection in ("units", "exceptions", "examples")
        for item in patch.get(collection, [])
        if isinstance(item, Mapping) and isinstance(item.get("c"), list)
        for index in item["c"]
        if isinstance(index, int)
    }
    if bootstrap is not None:
        owned_clause_indexes.update(
            index
            for collection in ("tables", "figures")
            for item in bootstrap.get(collection, [])
            if isinstance(item, Mapping) and isinstance(item.get("c"), list)
            for index in item["c"]
            if isinstance(index, int)
        )
    if isinstance(clauses, list):
        for item in clauses:
            if not isinstance(item, Mapping):
                continue
            decision = item.get("decision")
            if (
                isinstance(decision, list)
                and len(decision) >= 3
                and decision[2] == "compile"
                and item.get("i") not in owned_clause_indexes
            ):
                errors.append(
                    f"compiled clause {item.get('i')} has no semantic owner"
                )
    if task is not None and bootstrap is not None and not errors:
        try:
            expander = Expander(task)
            expander.register_ids(
                {"units": patch["units"], "tables": bootstrap["tables"]}
            )
            for unit in patch["units"]:
                expander.unit(unit)
            expander.symbols(patch["symbols"])
            for exception in patch["exceptions"]:
                expander.exception(exception)
            for example in patch["examples"]:
                expander.example(example)
        except (AuthoringError, KeyError, TypeError, ValueError) as error:
            errors.append(f"strict compact authoring failed: {error}")
    return {"passed": not errors, "errors": errors}


def normalize_mechanical_decisions(
    patch: Mapping[str, Any], bootstrap: Mapping[str, Any]
) -> tuple[dict[str, Any], list[int]]:
    normalized = dict(patch)
    normalized_clauses = []
    changed = []
    for raw_item in patch.get("clauses", []):
        if not isinstance(raw_item, Mapping):
            normalized_clauses.append(raw_item)
            continue
        item = dict(raw_item)
        index = item.get("i")
        if (
            isinstance(index, int)
            and 1 <= index <= len(bootstrap["clauses"])
            and bootstrap["clauses"][index - 1] is None
            and item.get("decision") is not None
        ):
            item["decision"] = None
            changed.append(index)
        elif (
            isinstance(index, int)
            and 1 <= index <= len(bootstrap["clauses"])
            and isinstance(item.get("decision"), list)
            and len(item["decision"]) == 5
            and item["decision"][2] == "supersede"
            and not item["decision"][3]
        ):
            item["decision"] = bootstrap["clauses"][index - 1]
            changed.append(index)
        normalized_clauses.append(item)
    normalized["clauses"] = normalized_clauses
    return normalized, changed


def normalize_clause_metadata(
    patch: Mapping[str, Any], bootstrap: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = dict(patch)
    normalized_clauses = []
    changes = []
    for raw_item in patch.get("clauses", []):
        if not isinstance(raw_item, Mapping):
            normalized_clauses.append(raw_item)
            continue
        item = dict(raw_item)
        index = item.get("i")
        decision = item.get("decision")
        if (
            isinstance(index, int)
            and 1 <= index <= len(bootstrap["clauses"])
            and isinstance(decision, list)
            and len(decision) >= 3
            and (
                decision[0] not in CLAUSE_ROLES
                or decision[1] not in CLAUSE_FORCES
            )
        ):
            source = bootstrap["clauses"][index - 1]
            if isinstance(source, list) and len(source) >= 2:
                before = decision[:2]
                decision = [source[0], source[1], *decision[2:]]
                item["decision"] = decision
                changes.append(
                    {
                        "clause_index": index,
                        "from": before,
                        "to": decision[:2],
                    }
                )
        normalized_clauses.append(item)
    normalized["clauses"] = normalized_clauses
    return normalized, changes


def _reason_code(value: str) -> str:
    result = re.sub(r"[^a-z0-9_.]+", "_", value.lower()).strip("_.")
    if not result or not result[0].isalpha():
        result = "reason_" + result
    return result


def normalize_reason_codes(
    patch: Mapping[str, Any], bootstrap: Mapping[str, Any] | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = dict(patch)
    changes = []

    def statement(value: Any, owner_id: Any) -> Any:
        if not isinstance(value, list) or not value:
            return value
        result = list(value)
        op = result[0]
        reason_index = 2 if op in {"reject", "assert"} else None
        if (
            reason_index is not None
            and len(result) > reason_index
            and isinstance(result[reason_index], str)
            and re.fullmatch(REASON_CODE_SCHEMA["pattern"], result[reason_index]) is None
        ):
            before = result[reason_index]
            result[reason_index] = _reason_code(before)
            changes.append(
                {
                    "owner_id": owner_id,
                    "from": before,
                    "to": result[reason_index],
                }
            )
        if op == "seq":
            result[1:] = [statement(item, owner_id) for item in result[1:]]
        elif op == "if" and len(result) == 4:
            result[2] = [statement(item, owner_id) for item in result[2]]
            result[3] = [statement(item, owner_id) for item in result[3]]
        elif op == "each" and len(result) == 5:
            result[3] = [statement(item, owner_id) for item in result[3]]
        return result

    normalized_clauses = []
    for raw_item in patch.get("clauses", []):
        if not isinstance(raw_item, Mapping):
            normalized_clauses.append(raw_item)
            continue
        item = dict(raw_item)
        decision = item.get("decision")
        if (
            isinstance(decision, list)
            and len(decision) == 4
            and decision[2] == "skip"
            and isinstance(decision[3], str)
            and decision[3] not in NONOPERATIVE_REASON_CODES
        ):
            index = item.get("i")
            source = (
                bootstrap["clauses"][index - 1]
                if bootstrap is not None
                and isinstance(index, int)
                and 1 <= index <= len(bootstrap["clauses"])
                else None
            )
            if isinstance(source, list) and len(source) >= 3:
                before = list(decision)
                item["decision"] = list(source)
                changes.append(
                    {
                        "clause_index": index,
                        "from": before,
                        "to": item["decision"],
                        "action": "restore_bootstrap_disposition",
                    }
                )
        normalized_clauses.append(item)
    normalized["clauses"] = normalized_clauses

    normalized_units = []
    for raw_item in patch.get("units", []):
        if not isinstance(raw_item, Mapping):
            normalized_units.append(raw_item)
            continue
        item = dict(raw_item)
        for field in ("then", "else", "steps", "violation"):
            if isinstance(item.get(field), list):
                item[field] = [
                    statement(value, item.get("id")) for value in item[field]
                ]
        normalized_units.append(item)
    normalized["units"] = normalized_units
    return normalized, changes


def _identifier(value: str, *, symbol: bool = False) -> str:
    if symbol:
        result = re.sub(r"[^a-z0-9_.]+", "_", value.lower()).strip("_.")
    else:
        result = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value).strip("_.:-")
    if not result or not result[0].isalpha():
        result = "x_" + result
    return result


def normalize_compact_identifiers(
    patch: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = dict(patch)
    changes = []

    def replace(value: Any, *, owner: Any, field: str, symbol: bool = False) -> Any:
        if not isinstance(value, str):
            return value
        result = _identifier(value, symbol=symbol)
        if result != value:
            changes.append({"owner_id": owner, "field": field, "from": value, "to": result})
        return result

    def expression(value: Any, owner: Any) -> Any:
        if not isinstance(value, list):
            return value
        if not value or not isinstance(value[0], str):
            if value and all(
                isinstance(item, list) and len(item) == 2 and item[0] == "lit"
                for item in value
            ):
                result = ["lit", [item[1] for item in value]]
                changes.append(
                    {
                        "owner_id": owner,
                        "field": "literal_list",
                        "from": value,
                        "to": result,
                    }
                )
                return result
            return value
        result = list(value)
        op = result[0]
        if op == "var" and len(result) == 2:
            result[1] = replace(result[1], owner=owner, field="var")
        elif op == "get" and len(result) == 3:
            result[1] = expression(result[1], owner)
            result[2] = replace(result[2], owner=owner, field="path", symbol=True)
        elif op in {"pred", "call"} and len(result) >= 2:
            result[1] = replace(result[1], owner=owner, field="symbol", symbol=True)
            result[2:] = [expression(item, owner) for item in result[2:]]
        elif op in {"all", "any"}:
            result[1:] = [expression(item, owner) for item in result[1:]]
        elif op == "not" and len(result) == 2:
            result[1] = expression(result[1], owner)
        elif op in {"exists", "forall"} and len(result) == 4:
            result[1] = replace(result[1], owner=owner, field="bind")
            result[2] = expression(result[2], owner)
            result[3] = expression(result[3], owner)
        elif op == "cmp" and len(result) == 4:
            aliases = {"=": "eq", "==": "eq", "equals": "eq", "!=": "ne"}
            result[1] = aliases.get(result[1], result[1])
            result[2] = expression(result[2], owner)
            raw_right = result[3]
            if (
                isinstance(raw_right, list)
                and raw_right
                and all(
                    isinstance(item, list)
                    and len(item) == 2
                    and item[0] == "lit"
                    for item in raw_right
                )
            ):
                before = [result[1], raw_right]
                if result[1] == "eq":
                    result[1] = "member_of"
                result[3] = ["lit", [item[1] for item in raw_right]]
                changes.append(
                    {
                        "owner_id": owner,
                        "field": "compare_literal_list",
                        "from": before,
                        "to": [result[1], result[3]],
                    }
                )
            else:
                result[3] = expression(raw_right, owner)
        elif op == "lookup" and len(result) == 4:
            result[1] = replace(result[1], owner=owner, field="table")
            result[2] = expression(result[2], owner)
            result[3] = replace(result[3], owner=owner, field="column")
        elif op == "outcome" and len(result) == 3:
            result[2] = replace(result[2], owner=owner, field="outcome")
        return result

    def statements(values: Sequence[Any], owner: Any) -> list[Any]:
        return [
            normalized_statement
            for value in values
            if (normalized_statement := statement(value, owner)) is not None
        ]

    def statement(value: Any, owner: Any) -> Any:
        if not isinstance(value, list) or not value or not isinstance(value[0], str):
            return value
        result = list(value)
        op = result[0]
        if op == "seq":
            result[1:] = statements(result[1:], owner)
        elif op == "if" and len(result) == 4:
            result[1] = expression(result[1], owner)
            result[2] = statements(result[2], owner)
            result[3] = statements(result[3], owner)
            if not result[2] and not result[3]:
                changes.append(
                    {
                        "owner_id": owner,
                        "field": "statement",
                        "action": "remove_noop_if",
                    }
                )
                return None
        elif op == "set" and len(result) == 3:
            result[1] = replace(result[1], owner=owner, field="target")
            result[2] = expression(result[2], owner)
        elif op == "xform" and len(result) >= 3:
            result[1] = replace(result[1], owner=owner, field="target")
            result[2] = replace(result[2], owner=owner, field="transformation", symbol=True)
            result[3:] = [expression(item, owner) for item in result[3:]]
        elif op == "render" and len(result) == 4:
            result[1] = replace(result[1], owner=owner, field="component")
            result[2] = replace(result[2], owner=owner, field="position")
            result[3] = expression(result[3], owner)
        elif op == "reject" and len(result) == 3:
            result[1] = replace(result[1], owner=owner, field="target")
        elif op == "invoke" and len(result) == 3 and isinstance(result[2], Mapping):
            result[2] = {name: expression(item, owner) for name, item in result[2].items()}
        elif op == "each" and len(result) == 5:
            result[1] = replace(result[1], owner=owner, field="bind")
            result[2] = expression(result[2], owner)
            result[3] = statements(result[3], owner)
            result[4] = expression(result[4], owner)
        elif op == "emit" and len(result) == 2:
            result[1] = expression(result[1], owner)
        elif op == "assert" and len(result) == 3:
            result[1] = expression(result[1], owner)
        return result

    normalized_symbols = []
    for raw_item in patch.get("symbols", []):
        if not isinstance(raw_item, Mapping):
            normalized_symbols.append(raw_item)
            continue
        item = dict(raw_item)
        grounding = item.get("g")
        if (
            isinstance(grounding, list)
            and len(grounding) == 3
            and isinstance(grounding[2], Mapping)
        ):
            before = grounding[2]
            primitive = before.get("id")
            if not isinstance(primitive, str):
                primitive = item.get("id")
            grounding = list(grounding)
            grounding[2] = primitive
            item["g"] = grounding
            changes.append(
                {
                    "owner_id": item.get("id"),
                    "field": "grounding.primitive",
                    "from": before,
                    "to": primitive,
                }
            )
        normalized_symbols.append(item)
    normalized["symbols"] = normalized_symbols

    normalized_units = []
    for raw_item in patch.get("units", []):
        if not isinstance(raw_item, Mapping):
            normalized_units.append(raw_item)
            continue
        item = dict(raw_item)
        owner = item.get("id")
        if "f" in item and item["f"] not in UNIT_FORCES:
            before = item["f"]
            item["f"] = "definition" if item.get("k") == "definition" else "required"
            changes.append(
                {"owner_id": owner, "field": "f", "from": before, "to": item["f"]}
            )
        if isinstance(item.get("scope"), Mapping):
            scope = dict(item["scope"])
            regimes = scope.get("r")
            if isinstance(regimes, list) and any(value not in SCOPE_REGIMES for value in regimes):
                changes.append({"owner_id": owner, "field": "scope.r", "from": regimes, "to": ["class_specific"]})
                scope["r"] = ["class_specific"]
            if "if" in scope:
                scope["if"] = expression(scope["if"], owner)
            item["scope"] = scope
        for field in ("if", "value", "assert", "candidates"):
            if field in item:
                item[field] = expression(item[field], owner)
        for field in ("then", "else", "steps", "violation"):
            if isinstance(item.get(field), list):
                item[field] = statements(item[field], owner)
        normalized_units.append(item)
    normalized["units"] = normalized_units
    return normalized, changes


def normalize_mechanical_ownership(
    patch: Mapping[str, Any], bootstrap: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    mechanical = {
        index
        for index, decision in enumerate(bootstrap["clauses"], 1)
        if decision is None
    }
    normalized = dict(patch)
    changes = []
    for collection in ("units", "exceptions", "examples"):
        normalized_items = []
        for raw_item in patch.get(collection, []):
            if not isinstance(raw_item, Mapping) or not isinstance(
                raw_item.get("c"), list
            ):
                normalized_items.append(raw_item)
                continue
            item = dict(raw_item)
            removed = [index for index in item["c"] if index in mechanical]
            if not removed:
                normalized_items.append(item)
                continue
            item["c"] = [index for index in item["c"] if index not in mechanical]
            changes.append(
                {
                    "collection": collection,
                    "id": item.get("id"),
                    "removed_clause_indexes": removed,
                    "dropped": not item["c"],
                }
            )
            if item["c"]:
                normalized_items.append(item)
        normalized[collection] = normalized_items
    return normalized, changes


def normalize_partition_ownership(
    patch: Mapping[str, Any], target_indexes: Sequence[int]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    targets = set(target_indexes)
    normalized = dict(patch)
    changes = []
    for collection in ("units", "exceptions", "examples"):
        normalized_items = []
        for raw_item in patch.get(collection, []):
            if not isinstance(raw_item, Mapping) or not isinstance(
                raw_item.get("c"), list
            ):
                normalized_items.append(raw_item)
                continue
            item = dict(raw_item)
            removed = [index for index in item["c"] if index not in targets]
            item["c"] = [index for index in item["c"] if index in targets]
            if removed:
                changes.append(
                    {
                        "collection": collection,
                        "id": item.get("id"),
                        "removed_clause_indexes": removed,
                        "dropped": not item["c"],
                    }
                )
            if item["c"]:
                normalized_items.append(item)
        normalized[collection] = normalized_items
    return normalized, changes


def normalize_record_ownership(
    patch: Mapping[str, Any], task: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    owner_by_index = {}
    index = 0
    for rule in task["rules"]:
        for _unit in rule["source_units"]:
            index += 1
            owner_by_index[index] = rule["rule_id"]
    normalized = dict(patch)
    changes = []
    for collection in ("units", "exceptions", "examples"):
        normalized_items = []
        for raw_item in patch.get(collection, []):
            if not isinstance(raw_item, Mapping) or not isinstance(
                raw_item.get("c"), list
            ):
                normalized_items.append(raw_item)
                continue
            groups: dict[str, list[int]] = {}
            for clause_index in raw_item["c"]:
                owner = owner_by_index.get(clause_index)
                if owner is not None:
                    groups.setdefault(owner, []).append(clause_index)
            if len(groups) <= 1:
                normalized_items.append(dict(raw_item))
                continue
            split_ids = []
            for group_number, (owner, clause_indexes) in enumerate(groups.items(), 1):
                item = dict(raw_item)
                item["c"] = clause_indexes
                if group_number > 1:
                    item["id"] = (
                        f"{raw_item.get('id', collection)}_record_{_identifier(owner)}"
                    )
                split_ids.append(item.get("id"))
                normalized_items.append(item)
            changes.append(
                {
                    "collection": collection,
                    "id": raw_item.get("id"),
                    "record_ids": list(groups),
                    "split_ids": split_ids,
                }
            )
        normalized[collection] = normalized_items
    return normalized, changes


def normalize_bootstrap_ownership(
    patch: Mapping[str, Any], bootstrap: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = dict(patch)
    normalized["units"] = [
        dict(item) if isinstance(item, Mapping) else item
        for item in patch.get("units", [])
    ]
    normalized["examples"] = [
        dict(item) if isinstance(item, Mapping) else item
        for item in patch.get("examples", [])
    ]
    owned = {
        index
        for collection in ("units", "exceptions", "examples")
        for item in normalized.get(collection, [])
        if isinstance(item, Mapping) and isinstance(item.get("c"), list)
        for index in item["c"]
        if isinstance(index, int)
    }
    compiled = [
        item["i"]
        for item in normalized.get("clauses", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("decision"), list)
        and len(item["decision"]) >= 3
        and item["decision"][2] == "compile"
        and item["i"] not in owned
    ]
    changes = []
    for index in compiled:
        source_example = next(
            (
                item
                for item in bootstrap.get("examples", [])
                if index in item.get("c", [])
            ),
            None,
        )
        if source_example is not None:
            example = {
                "id": source_example["id"],
                "c": [index],
                "input": {"source_clause_index": index},
                "ok": source_example.get("ok", []),
                "bad": source_example.get("bad", []),
                "shows": [],
                "why": source_example.get(
                    "why", "Source-bound example retained from deterministic extraction."
                ),
            }
            normalized["examples"].append(example)
            owned.add(index)
            changes.append(
                {
                    "clause_index": index,
                    "action": "restore_source_example",
                    "owner_id": example["id"],
                }
            )
            continue
        bootstrap_kind = next(
            (
                item.get("k")
                for item in bootstrap.get("units", [])
                if index in item.get("c", [])
            ),
            None,
        )
        candidates = [
            item
            for item in normalized["units"]
            if isinstance(item, Mapping)
            and item.get("k") == bootstrap_kind
            and isinstance(item.get("c"), list)
            and item["c"]
        ]
        if not candidates:
            continue
        owner = min(
            candidates,
            key=lambda item: min(abs(index - member) for member in item["c"]),
        )
        owner["c"] = sorted({*owner["c"], index})
        owned.add(index)
        changes.append(
            {
                "clause_index": index,
                "action": "attach_to_same_kind_unit",
                "owner_id": owner["id"],
                "bootstrap_kind": bootstrap_kind,
            }
        )
    return normalized, changes


def normalize_example_references(
    patch: Mapping[str, Any],
    bootstrap: Mapping[str, Any] | None = None,
    task: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = dict(patch)
    normalized_examples = []
    changes = []
    table_ids = {
        item["id"]
        for item in (bootstrap or {}).get("tables", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    table_by_label = {
        item["label"]: item["id"]
        for item in (bootstrap or {}).get("tables", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("label"), str)
    }
    for raw_item in patch.get("examples", []):
        if not isinstance(raw_item, Mapping):
            normalized_examples.append(raw_item)
            continue
        item = dict(raw_item)
        shows = item.get("shows")
        if not isinstance(shows, list):
            normalized_examples.append(item)
            continue
        figure_ids = {
            item["id"]
            for item in (bootstrap or {}).get("figures", [])
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        record_ids = {
            rule["rule_id"]
            for rule in (task or {}).get("rules", [])
            if isinstance(rule, Mapping) and isinstance(rule.get("rule_id"), str)
        }
        valid = []
        normalized_count = 0
        for ref in shows:
            if (
                not isinstance(ref, list)
                or len(ref) != 2
                or ref[0] not in OBJECT_REF_KINDS
            ):
                continue
            kind, object_id = ref
            if kind == "clause":
                if isinstance(object_id, str) and object_id.isdigit():
                    object_id = int(object_id)
                    normalized_count += 1
                if not isinstance(object_id, int):
                    continue
            else:
                if isinstance(object_id, int):
                    object_id = str(object_id)
                    normalized_count += 1
                if not isinstance(object_id, str):
                    continue
            if kind == "table" and object_id not in table_ids:
                label = object_id.split(":", 1)[0]
                replacement = table_by_label.get(label)
                if replacement is None:
                    continue
                object_id = replacement
                normalized_count += 1
            if (
                bootstrap is not None
                and kind == "figure"
                and object_id not in figure_ids
            ):
                continue
            if task is not None and kind == "record" and object_id not in record_ids:
                continue
            if kind == "statement" and not object_id.startswith("stmt."):
                continue
            valid.append([kind, object_id])
        if valid != shows:
            item["shows"] = valid
            change = {
                "example_id": item.get("id"),
                "removed_count": len(shows) - len(valid),
            }
            if normalized_count:
                change["normalized_count"] = normalized_count
            changes.append(change)
        normalized_examples.append(item)
    normalized["examples"] = normalized_examples
    return normalized, changes


def normalize_table_references(
    patch: Mapping[str, Any], bootstrap: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = dict(patch)
    tables = [item for item in bootstrap.get("tables", []) if item.get("c")]
    table_ids = {item["id"] for item in tables}
    table_columns = {
        item["id"]: {
            column[0]
            for column in item.get("cols", [])
            if isinstance(column, list) and column and isinstance(column[0], str)
        }
        for item in tables
    }
    changes = []
    normalized_symbols = [
        dict(item) if isinstance(item, Mapping) else item
        for item in patch.get("symbols", [])
    ]
    symbol_ids = {
        item.get("id") for item in normalized_symbols if isinstance(item, Mapping)
    }

    def lookup_symbol(table_id: Any) -> str:
        symbol_id = _identifier(f"lookup_{table_id}", symbol=True)
        if symbol_id not in symbol_ids:
            normalized_symbols.append(
                {
                    "id": symbol_id,
                    "k": "function",
                    "d": f"Resolve {table_id} through a source-defined lookup primitive.",
                    "a": [["key", "Any"], ["column", "String"]],
                    "ret": "Any",
                    "g": ["primitive", [], symbol_id],
                }
            )
            symbol_ids.add(symbol_id)
        return symbol_id

    def replacement(owner: Mapping[str, Any]) -> str | None:
        owner_clauses = set(owner.get("c", []))
        ranked = sorted(
            (
                (len(owner_clauses.intersection(table["c"])), table["id"])
                for table in tables
            ),
            reverse=True,
        )
        return ranked[0][1] if ranked and ranked[0][0] > 0 else None

    def expression(value: Any, owner: Mapping[str, Any]) -> Any:
        if not isinstance(value, list) or not value or not isinstance(value[0], str):
            return value
        result = list(value)
        op = result[0]
        if op == "lookup" and len(result) == 4:
            result[2] = expression(result[2], owner)
            if result[1] not in table_ids:
                table_id = replacement(owner)
                if table_id is not None:
                    changes.append(
                        {
                            "owner_id": owner.get("id"),
                            "field": "lookup",
                            "from": result[1],
                            "to": table_id,
                        }
                    )
                    result[1] = table_id
                else:
                    before = result[1]
                    symbol_id = lookup_symbol(before)
                    changes.append(
                        {
                            "owner_id": owner.get("id"),
                            "field": "lookup",
                            "from": before,
                            "to": symbol_id,
                            "action": "lower_to_grounded_function",
                        }
                    )
                    return ["call", symbol_id, result[2], ["lit", result[3]]]
            columns = table_columns.get(result[1], set())
            if result[3] not in columns and "text" in columns:
                before = result[3]
                result[3] = "text"
                changes.append(
                    {
                        "owner_id": owner.get("id"),
                        "field": "lookup_column",
                        "from": before,
                        "to": result[3],
                    }
                )
        elif op == "get" and len(result) == 3:
            result[1] = expression(result[1], owner)
        elif op in {"pred", "call", "all", "any"}:
            start = 2 if op in {"pred", "call"} else 1
            result[start:] = [expression(item, owner) for item in result[start:]]
        elif op == "not" and len(result) == 2:
            result[1] = expression(result[1], owner)
        elif op in {"exists", "forall"} and len(result) == 4:
            result[2] = expression(result[2], owner)
            result[3] = expression(result[3], owner)
        elif op == "cmp" and len(result) == 4:
            result[2] = expression(result[2], owner)
            result[3] = expression(result[3], owner)
        return result

    def normalize_comparators(item: dict[str, Any]) -> None:
        if item.get("k") != "decision" or not isinstance(item.get("stages"), list):
            return
        for stage in item["stages"]:
            if not isinstance(stage, Mapping) or not isinstance(stage.get("cmp"), list):
                continue
            comparator = list(stage["cmp"])
            if len(comparator) != 4:
                continue
            kind = comparator[0]
            before = list(comparator)
            if kind != "custom":
                comparator[2] = None
            if kind != "ordered_table":
                comparator[3] = None
            elif comparator[3] not in table_ids:
                table_id = replacement(item)
                if table_id is not None:
                    comparator[3] = table_id
                else:
                    comparator[0] = "lexicographic"
                    comparator[3] = None
            if comparator != before:
                stage["cmp"] = comparator
                changes.append(
                    {
                        "owner_id": item.get("id"),
                        "field": "decision_comparator",
                        "from": before,
                        "to": comparator,
                    }
                )

    def statement(value: Any, owner: Mapping[str, Any]) -> Any:
        if not isinstance(value, list) or not value:
            return value
        result = list(value)
        op = result[0]
        if op == "seq":
            result[1:] = [statement(item, owner) for item in result[1:]]
        elif op == "if" and len(result) == 4:
            result[1] = expression(result[1], owner)
            result[2] = [statement(item, owner) for item in result[2]]
            result[3] = [statement(item, owner) for item in result[3]]
        elif op in {"set", "emit", "assert"}:
            expression_index = 2 if op in {"set", "assert"} else 1
            result[expression_index] = expression(result[expression_index], owner)
        elif op in {"xform", "render"}:
            start = 3
            result[start:] = [expression(item, owner) for item in result[start:]]
        elif op == "invoke" and len(result) == 3 and isinstance(result[2], Mapping):
            result[2] = {key: expression(item, owner) for key, item in result[2].items()}
        elif op == "each" and len(result) == 5:
            result[2] = expression(result[2], owner)
            result[3] = [statement(item, owner) for item in result[3]]
            result[4] = expression(result[4], owner)
        return result

    normalized_units = []
    for raw_item in patch.get("units", []):
        if not isinstance(raw_item, Mapping):
            normalized_units.append(raw_item)
            continue
        item = dict(raw_item)
        if isinstance(item.get("stages"), list):
            item["stages"] = [
                dict(stage) if isinstance(stage, Mapping) else stage
                for stage in item["stages"]
            ]
        normalize_comparators(item)
        if item.get("k") == "mapping" and item.get("table") not in table_ids:
            table_id = replacement(item)
            if table_id is not None:
                changes.append(
                    {
                        "owner_id": item.get("id"),
                        "field": "table",
                        "from": item.get("table"),
                        "to": table_id,
                    }
                )
                item["table"] = table_id
        for field in ("if", "value", "assert", "candidates"):
            if field in item:
                item[field] = expression(item[field], item)
        for field in ("then", "else", "steps", "violation"):
            if isinstance(item.get(field), list):
                item[field] = [statement(value, item) for value in item[field]]
        normalized_units.append(item)
    normalized["units"] = normalized_units
    normalized["symbols"] = normalized_symbols
    return normalized, changes


def normalize_example_names(
    patch: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = dict(patch)
    normalized_examples = []
    changes = []
    for raw_item in patch.get("examples", []):
        if not isinstance(raw_item, Mapping):
            normalized_examples.append(raw_item)
            continue
        item = dict(raw_item)
        for field in ("ok", "bad"):
            values = item.get(field)
            if not isinstance(values, list):
                continue
            unwrapped = [
                value[1]
                if isinstance(value, list)
                and len(value) == 2
                and value[0] == "lit"
                and isinstance(value[1], str)
                else value
                for value in values
            ]
            if unwrapped != values:
                item[field] = unwrapped
                changes.append(
                    {
                        "example_id": item.get("id"),
                        "field": field,
                        "unwrapped_count": sum(
                            before != after
                            for before, after in zip(values, unwrapped)
                        ),
                    }
                )
        normalized_examples.append(item)
    normalized["examples"] = normalized_examples
    return normalized, changes


def deduplicate_patch_ids(
    patches: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Namespace only colliding authored IDs and update references in that patch."""
    seen: set[str] = set()
    result = []

    def replace(
        value: Any, replacements: Mapping[str, str], *, field: str | None = None
    ) -> Any:
        if isinstance(value, str):
            if field == "table":
                return value
            return replacements.get(value, value)
        if isinstance(value, list):
            if value and value[0] == "lookup":
                return [
                    value[0],
                    value[1],
                    *[replace(item, replacements) for item in value[2:]],
                ]
            if len(value) == 2 and value[0] == "table":
                return list(value)
            return [replace(item, replacements) for item in value]
        if isinstance(value, Mapping):
            return {
                key: replace(item, replacements, field=str(key))
                for key, item in value.items()
            }
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


def localize_compile_errors(
    source: Mapping[str, Any],
    compile_report: Mapping[str, Any] | None,
    partitions: Sequence[Sequence[int]],
) -> dict[int, list[dict[str, Any]]]:
    localized: dict[int, list[dict[str, Any]]] = {}
    collection_map = {
        "semantic_units": "units",
        "exceptions": "exceptions",
        "examples": "examples",
    }
    for error in (compile_report or {}).get("errors", []):
        parts = str(error.get("path", "")).strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "clause_dispositions":
            try:
                clause_index = int(parts[1]) + 1
            except ValueError:
                continue
            for partition_number, indexes in enumerate(partitions, 1):
                if clause_index in indexes:
                    localized.setdefault(partition_number, []).append(dict(error))
                    break
            continue
        if len(parts) < 2 or parts[0] not in collection_map:
            continue
        try:
            object_index = int(parts[1])
            item = source[collection_map[parts[0]]][object_index]
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        clause_indexes = item.get("c", []) if isinstance(item, Mapping) else []
        for partition_number, indexes in enumerate(partitions, 1):
            if set(clause_indexes).intersection(indexes):
                localized.setdefault(partition_number, []).append(dict(error))
    return localized


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
        schema = response_schema(
            len(indexes),
            indexes,
            {
                index
                for index, decision in enumerate(bootstrap["clauses"], 1)
                if decision is None
            },
        )
        patch_path = patch_dir / f"part-{number:03}.json"
        part_report_path = report_dir / f"part-{number:03}.json"
        prior_patch: dict[str, Any] | None = None
        prior_global_errors: list[dict[str, Any]] = []
        prior_global_repair_round = 0
        if not force and patch_path.exists() and part_report_path.exists():
            patch = load_json(patch_path)
            patch, normalized_metadata = normalize_clause_metadata(patch, bootstrap)
            patch, normalized_reasons = normalize_reason_codes(patch, bootstrap)
            patch, normalized_identifiers = normalize_compact_identifiers(patch)
            patch, normalized_tables = normalize_table_references(patch, bootstrap)
            patch, normalized_indexes = normalize_mechanical_decisions(
                patch, bootstrap
            )
            patch, normalized_ownership = normalize_mechanical_ownership(
                patch, bootstrap
            )
            patch, partition_ownership = normalize_partition_ownership(
                patch, indexes
            )
            patch, bootstrap_ownership = normalize_bootstrap_ownership(
                patch, bootstrap
            )
            patch, record_ownership = normalize_record_ownership(patch, task)
            patch, example_references = normalize_example_references(
                patch, bootstrap, task
            )
            patch, example_names = normalize_example_names(patch)
            part_report = load_json(part_report_path)
            prior_patch = patch
            prior_global_errors = list(
                part_report.get("global_validation_errors", [])
            )
            prior_global_repair_round = int(
                part_report.get("global_repair_round", 0)
            )
            validation = validate_patch(
                patch, task_id, indexes, task=task, bootstrap=bootstrap
            )
            cache_normalized = bool(
                normalized_metadata
                or normalized_reasons
                or normalized_identifiers
                or normalized_tables
                or normalized_indexes
                or normalized_ownership
                or partition_ownership
                or record_ownership
                or bootstrap_ownership
                or example_references
                or example_names
            )
            if validation["passed"] and prior_global_errors and cache_normalized:
                part_report["superseded_global_validation_errors"] = prior_global_errors
                part_report.pop("global_validation_errors", None)
                prior_global_errors = []
            if (
                part_report.get("model") == model
                and part_report.get("backend") == backend
                and part_report.get("endpoint") == endpoint
                and part_report.get("seed") == seed + number - 1
                and validation["passed"]
                and not prior_global_errors
            ):
                previous_prompt_sha256 = part_report.get("prompt_sha256")
                part_report = {
                    **part_report,
                    "validator_version": PARTITION_VALIDATOR_VERSION,
                    "task_sha256": task["task_sha256"],
                    "prompt_sha256": prompt_sha256,
                    "prompt_bytes": len(prompt.encode("utf-8")),
                    "schema_sha256": _sha256(schema),
                    "validation": validation,
                }
                if normalized_indexes:
                    part_report["mechanical_decisions_normalized"] = normalized_indexes
                if normalized_metadata:
                    part_report["clause_metadata_normalized"] = normalized_metadata
                if normalized_reasons:
                    part_report["reason_codes_normalized"] = normalized_reasons
                if normalized_identifiers:
                    part_report["compact_identifiers_normalized"] = normalized_identifiers
                if normalized_tables:
                    part_report["table_references_normalized"] = normalized_tables
                if normalized_ownership:
                    part_report["mechanical_ownership_normalized"] = normalized_ownership
                if partition_ownership:
                    part_report["partition_ownership_normalized"] = partition_ownership
                if record_ownership:
                    part_report["record_ownership_normalized"] = record_ownership
                if bootstrap_ownership:
                    part_report["bootstrap_ownership_normalized"] = bootstrap_ownership
                if example_references:
                    part_report["example_references_normalized"] = example_references
                if example_names:
                    part_report["example_names_normalized"] = example_names
                if cache_normalized:
                    patch_path.write_bytes(canonical_json_bytes(patch))
                if previous_prompt_sha256 != prompt_sha256:
                    part_report["cache_migration"] = {
                        "from_prompt_sha256": previous_prompt_sha256,
                        "reason": "passed current strict compact-authoring preflight",
                    }
                part_report_path.write_bytes(canonical_json_bytes(part_report))
                patches.append(patch)
                partition_reports.append({**part_report, "cached": True})
                continue

        output_tokens = min(
            maximum_output_tokens, max(4096, 768 + 384 * len(indexes))
        )
        request_prompt = prompt
        if prior_global_errors and prior_patch is not None:
            request_prompt = (
                prompt
                + "\nPREVIOUS PARTITION FAILED GLOBAL COMPILATION:\n"
                + json.dumps(
                    prior_global_errors, separators=(",", ":"), ensure_ascii=False
                )
                + "\nPREVIOUS PARTITION JSON:\n"
                + json.dumps(
                    prior_patch, separators=(",", ":"), ensure_ascii=False
                )
                + "\nRepair every reported dependency or schema problem while "
                "preserving valid content. References and table IDs must resolve to "
                "objects listed in the retained objects or this partition. This "
                "partition cannot create tables: replace any unresolved lookup with "
                "executable conditionals, literals, or local variable access. Return "
                "the complete corrected partition as JSON only."
            )
        attempts = []
        best_patch: dict[str, Any] = {}
        best_validation = {"passed": False, "errors": ["not generated"]}
        for attempt in range(repair_attempts + 1):
            prompt_tokens, token_count_method = count_prompt_tokens(
                endpoint, request_prompt, timeout=min(timeout, 30)
            )
            context_overhead = 256
            available_output_tokens = (
                context_tokens - prompt_tokens - context_overhead
            )
            request_output_tokens = min(output_tokens, available_output_tokens)
            context_preflight = {
                "prompt_tokens": prompt_tokens,
                "count_method": token_count_method,
                "context_overhead_tokens": context_overhead,
                "available_output_tokens": available_output_tokens,
                "requested_output_tokens": request_output_tokens,
            }
            if request_output_tokens < 2048:
                validation = {
                    "passed": False,
                    "errors": [
                        f"tokenized prompt leaves only {available_output_tokens} output "
                        f"tokens inside context {context_tokens}"
                    ],
                }
                attempts.append(
                    {
                        "attempt": attempt + 1,
                        "context_preflight": context_preflight,
                        "validation": validation,
                    }
                )
                break
            request_seed = (
                seed + number - 1 + (1009 * prior_global_repair_round)
            )
            patch, metrics = _request_model(
                backend=backend,
                endpoint=endpoint,
                model=model,
                prompt=request_prompt,
                context_tokens=context_tokens,
                output_tokens=request_output_tokens,
                timeout=timeout,
                seed=request_seed,
                schema=schema,
            )
            patch, normalized_metadata = normalize_clause_metadata(patch, bootstrap)
            patch, normalized_reasons = normalize_reason_codes(patch, bootstrap)
            patch, normalized_identifiers = normalize_compact_identifiers(patch)
            patch, normalized_tables = normalize_table_references(patch, bootstrap)
            patch, normalized_indexes = normalize_mechanical_decisions(
                patch, bootstrap
            )
            patch, normalized_ownership = normalize_mechanical_ownership(
                patch, bootstrap
            )
            patch, partition_ownership = normalize_partition_ownership(
                patch, indexes
            )
            patch, bootstrap_ownership = normalize_bootstrap_ownership(
                patch, bootstrap
            )
            patch, record_ownership = normalize_record_ownership(patch, task)
            patch, example_references = normalize_example_references(
                patch, bootstrap, task
            )
            patch, example_names = normalize_example_names(patch)
            validation = validate_patch(
                patch, task_id, indexes, task=task, bootstrap=bootstrap
            )
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "request_seed": request_seed,
                    "context_preflight": context_preflight,
                    "metrics": metrics,
                    "clause_metadata_normalized": normalized_metadata,
                    "reason_codes_normalized": normalized_reasons,
                    "compact_identifiers_normalized": normalized_identifiers,
                    "table_references_normalized": normalized_tables,
                    "mechanical_decisions_normalized": normalized_indexes,
                    "mechanical_ownership_normalized": normalized_ownership,
                    "partition_ownership_normalized": partition_ownership,
                    "record_ownership_normalized": record_ownership,
                    "bootstrap_ownership_normalized": bootstrap_ownership,
                    "example_references_normalized": example_references,
                    "example_names_normalized": example_names,
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
                    + "\nPREVIOUS PARTITION JSON:\n"
                    + json.dumps(
                        patch, separators=(",", ":"), ensure_ascii=False
                    )
                    + "\nRepair this JSON while preserving valid content. For every "
                    "compiled clause without an owner, add its index to the c array of "
                    "the semantic unit or example that implements it, or change its "
                    "decision to skip only if the source is genuinely nonoperative. "
                    "Return the complete corrected partition as JSON only."
                )
        if best_patch:
            patch_path.write_bytes(canonical_json_bytes(best_patch))
        part_report = {
            "format": "iupac-bluebook-local-authoring-partition-report",
            "format_version": "1.0.0",
            "validator_version": PARTITION_VALIDATOR_VERSION,
            "task_id": task_id,
            "task_sha256": task["task_sha256"],
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
        if prior_global_repair_round:
            part_report["global_repair_round"] = prior_global_repair_round
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
    localized_errors = localize_compile_errors(source, compile_report, partitions)
    if localized_errors:
        for partition_number, global_errors in localized_errors.items():
            stored_report = dict(partition_reports[partition_number - 1])
            stored_report.pop("cached", None)
            stored_report["global_validation_errors"] = global_errors
            stored_report["global_repair_round"] = (
                int(stored_report.get("global_repair_round", 0)) + 1
            )
            partition_reports[partition_number - 1] = stored_report
            (report_dir / f"part-{partition_number:03}.json").write_bytes(
                canonical_json_bytes(stored_report)
            )
        report["partition_reports"] = partition_reports
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
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
