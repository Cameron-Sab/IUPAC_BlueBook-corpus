from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "data" / "normalized_rule_language.schema.json"

RULE_ID = "P-1.1"
OPERATIVE_CLAUSE = f"{RULE_ID}:clause:0001"
NONOPERATIVE_CLAUSE = f"{RULE_ID}:clause:0002"
TABLE_ID = "table.priority"
DECISION_UNIT_ID = "unit.choose_candidate"
FALLBACK_UNIT_ID = "unit.retain_coequal"


def expression(expression_id: str, op: str, **payload: Any) -> dict[str, Any]:
    return {
        "expression_id": expression_id,
        "clause_ids": [OPERATIVE_CLAUSE],
        "op": op,
        **payload,
    }


def object_ref(kind: str, object_id: str) -> dict[str, str]:
    return {"kind": kind, "id": object_id}


def build_corpus() -> dict[str, Any]:
    decision = {
        "unit_id": DECISION_UNIT_ID,
        "kind": "decision",
        "force": "preference",
        "clause_ids": [OPERATIVE_CLAUSE],
        "scope": {
            "regimes": ["preferred_iupac_name"],
            "applies_to": expression("expr.decision.scope", "literal", value=True),
        },
        "inputs": [{"name": "candidates", "type": "NameCandidate[]"}],
        "outputs": [{"name": "selected", "type": "NameCandidate"}],
        "candidates": expression("expr.decision.candidates", "var", name="candidates"),
        "stages": [
            {
                "stage_id": "stage.score",
                "ordinal": 1,
                "clause_ids": [OPERATIVE_CLAUSE],
                "guard": expression("expr.stage.score.guard", "literal", value=True),
                "key": expression(
                    "expr.stage.score.key",
                    "function",
                    symbol="candidate.score",
                    args=[
                        expression(
                            "expr.stage.score.candidate", "var", name="candidate"
                        )
                    ],
                ),
                "comparator": {
                    "kind": "numeric",
                    "direction": "minimum",
                    "symbol": None,
                    "table_id": None,
                },
                "on_tie": {"mode": "continue", "next_stage_id": "stage.priority"},
            },
            {
                "stage_id": "stage.priority",
                "ordinal": 2,
                "clause_ids": [OPERATIVE_CLAUSE],
                "guard": expression(
                    "expr.stage.priority.guard", "literal", value=True
                ),
                "key": expression(
                    "expr.stage.priority.key",
                    "table_lookup",
                    table_id=TABLE_ID,
                    key=expression(
                        "expr.stage.priority.candidate", "var", name="candidate"
                    ),
                    column_id="col.rank",
                ),
                "comparator": {
                    "kind": "ordered_table",
                    "direction": "minimum",
                    "symbol": "priority.compare",
                    "table_id": TABLE_ID,
                },
                "on_tie": {"mode": "continue", "next_stage_id": None},
            },
        ],
        "terminal_tie": {
            "mode": "apply_fallback",
            "fallback_ref": object_ref("semantic_unit", FALLBACK_UNIT_ID),
        },
    }

    fallback = {
        "unit_id": FALLBACK_UNIT_ID,
        "kind": "rule",
        "force": "permitted",
        "clause_ids": [OPERATIVE_CLAUSE],
        "scope": {
            "regimes": ["preferred_iupac_name"],
            "applies_to": expression("expr.fallback.scope", "literal", value=True),
        },
        "inputs": [{"name": "candidate", "type": "NameCandidate"}],
        "outputs": [{"name": "selected", "type": "NameCandidate"}],
        "when": expression("expr.fallback.when", "literal", value=True),
        "then": [
            {
                "statement_id": "stmt.fallback.emit",
                "clause_ids": [OPERATIVE_CLAUSE],
                "op": "emit",
                "value": expression(
                    "expr.fallback.selected", "var", name="candidate"
                ),
            }
        ],
        "else": [],
    }

    return {
        "format": "iupac-bluebook-normalized-rule-language",
        "format_version": "3.0.0",
        "conversion_stage": "complete_semantic_ir",
        "source_snapshot": {
            "source_corpus_sha256": "A" * 64,
            "source_pages_sha256": "B" * 64,
            "document_nodes_sha256": "C" * 64,
            "correction_overlays_sha256": "D" * 64,
            "clause_inventory_sha256": "E" * 64,
            "reference_occurrences_sha256": "F" * 64,
            "reference_resolutions_sha256": "0" * 64,
            "effective_through": "2026-07-15",
        },
        "symbol_registry": {
            "symbols": [
                {
                    "symbol_id": "candidate.score",
                    "kind": "function",
                    "description": "Return the primary score for a candidate.",
                    "arguments": [{"name": "candidate", "type": "NameCandidate"}],
                    "returns": "integer",
                    "grounding": {
                        "kind": "primitive",
                        "refs": [],
                        "primitive": "candidate primary score",
                    },
                },
                {
                    "symbol_id": "candidate.is_retained",
                    "kind": "predicate",
                    "description": "Whether a candidate is a retained name.",
                    "arguments": [{"name": "candidate", "type": "NameCandidate"}],
                    "returns": "boolean",
                    "grounding": {
                        "kind": "definition",
                        "refs": [object_ref("clause", OPERATIVE_CLAUSE)],
                        "primitive": None,
                    },
                },
                {
                    "symbol_id": "priority.compare",
                    "kind": "comparator",
                    "description": "Compare candidates by the normalized priority table.",
                    "arguments": [
                        {"name": "left", "type": "NameCandidate"},
                        {"name": "right", "type": "NameCandidate"},
                    ],
                    "returns": "ordering",
                    "grounding": {
                        "kind": "table",
                        "refs": [object_ref("table", TABLE_ID)],
                        "primitive": None,
                    },
                },
            ]
        },
        "clause_dispositions": [
            {
                "clause_id": OPERATIVE_CLAUSE,
                "role": "preference_criterion",
                "force": "normative",
                "disposition": {
                    "kind": "compiled",
                    "targets": [
                        object_ref("semantic_unit", DECISION_UNIT_ID),
                        object_ref("expression", "expr.decision.candidates"),
                        object_ref("statement", "stmt.fallback.emit"),
                        object_ref("exception", "exception.retained"),
                        object_ref("table", TABLE_ID),
                    ],
                },
            },
            {
                "clause_id": NONOPERATIVE_CLAUSE,
                "role": "note",
                "force": "informative",
                "disposition": {
                    "kind": "nonoperative",
                    "reason_code": "explanatory_note",
                },
            },
        ],
        "records": [
            {
                "record_id": "bluebook-v3:P-1.1",
                "source_rule_id": RULE_ID,
                "chapter": "P-1",
                "clause_ids": [OPERATIVE_CLAUSE, NONOPERATIVE_CLAUSE],
                "operative": True,
                "semantic_unit_ids": [DECISION_UNIT_ID, FALLBACK_UNIT_ID],
                "exception_ids": ["exception.retained"],
                "table_ids": [TABLE_ID],
                "figure_ids": [],
                "example_ids": [],
                "correction_application_ids": ["correction.priority_row"],
                "reference_ids": ["reference.decision_table"],
            }
        ],
        "semantic_units": [decision, fallback],
        "exceptions": [
            {
                "exception_id": "exception.retained",
                "clause_ids": [OPERATIVE_CLAUSE],
                "when": expression(
                    "expr.exception.retained",
                    "predicate",
                    symbol="candidate.is_retained",
                    args=[
                        expression(
                            "expr.exception.candidate", "var", name="candidate"
                        )
                    ],
                ),
                "target": object_ref("decision_stage", "stage.priority"),
                "effect": {
                    "mode": "redirect",
                    "replacement": None,
                    "guard": None,
                    "redirect": object_ref("semantic_unit", FALLBACK_UNIT_ID),
                },
                "precedence": {"specificity": 1, "source_order": 1},
            }
        ],
        "tables": [
            {
                "table_id": TABLE_ID,
                "label": "1",
                "title": "Candidate priority",
                "clause_ids": [OPERATIVE_CLAUSE],
                "columns": [
                    {
                        "column_id": "col.kind",
                        "ordinal": 1,
                        "label": "Candidate kind",
                        "value_type": "string",
                        "clause_ids": [OPERATIVE_CLAUSE],
                    },
                    {
                        "column_id": "col.rank",
                        "ordinal": 2,
                        "label": "Rank",
                        "value_type": "integer",
                        "clause_ids": [OPERATIVE_CLAUSE],
                    },
                ],
                "rows": [
                    {
                        "row_id": "row.retained",
                        "ordinal": 1,
                        "rank_group": 1,
                        "cells": [
                            {
                                "cell_id": "cell.retained.kind",
                                "column_id": "col.kind",
                                "value": "retained",
                                "clause_ids": [OPERATIVE_CLAUSE],
                            },
                            {
                                "cell_id": "cell.retained.rank",
                                "column_id": "col.rank",
                                "value": 1,
                                "clause_ids": [OPERATIVE_CLAUSE],
                            },
                        ],
                        "clause_ids": [OPERATIVE_CLAUSE],
                    }
                ],
                "footnotes": [],
                "contract": {
                    "key_column_ids": ["col.kind"],
                    "result_column_ids": ["col.rank"],
                    "cardinality": "one_to_one",
                    "ordering": "ascending",
                },
            }
        ],
        "figures": [],
        "examples": [],
        "correction_applications": [
            {
                "application_id": "correction.priority_row",
                "overlay_id": "BBV3-CORR-0123456789ABCDEF",
                "operation_id": "replace-priority-row",
                "status": "applied",
                "before_clause_ids": [OPERATIVE_CLAUSE],
                "after_clause_ids": [OPERATIVE_CLAUSE],
                "target_refs": [object_ref("table_row", "row.retained")],
                "effective_date": "2026-01-22",
                "result_sha256": "1" * 64,
            }
        ],
        "references": [
            {
                "reference_id": "reference.decision_table",
                "clause_ids": [OPERATIVE_CLAUSE],
                "relation": "uses_table",
                "source": object_ref("semantic_unit", DECISION_UNIT_ID),
                "target": object_ref("table", TABLE_ID),
                "resolution": "exact",
                "ordered_member_refs": [object_ref("table_row", "row.retained")],
            }
        ],
        "dependency_edges": [
            {
                "edge_id": "edge.decision_table",
                "from": object_ref("semantic_unit", DECISION_UNIT_ID),
                "relation": "uses_table",
                "to": object_ref("table", TABLE_ID),
                "clause_ids": [OPERATIVE_CLAUSE],
                "derived_from_object_ids": [
                    "reference.decision_table",
                    "stage.priority",
                ],
            }
        ],
        "metrics": {
            "record_count": 1,
            "clause_disposition_count": 2,
            "compiled_clause_count": 1,
            "nonoperative_clause_count": 1,
            "superseded_clause_count": 0,
            "semantic_unit_count": 2,
            "exception_count": 1,
            "table_count": 1,
            "figure_count": 0,
            "example_count": 0,
            "correction_application_count": 1,
            "reference_count": 1,
            "dependency_edge_count": 1,
        },
        "corpus_sha256": "F" * 64,
    }


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def assert_invalid(
    validator: Draft202012Validator, corpus: dict[str, Any]
) -> None:
    assert list(validator.iter_errors(corpus))


def test_coherent_v3_corpus_is_schema_valid(
    validator: Draft202012Validator,
) -> None:
    corpus = build_corpus()

    assert list(validator.iter_errors(corpus)) == []
    assert [
        item["disposition"]["kind"] for item in corpus["clause_dispositions"]
    ] == ["compiled", "nonoperative"]
    decision = corpus["semantic_units"][0]
    assert decision["stages"][0]["on_tie"] == {
        "mode": "continue",
        "next_stage_id": "stage.priority",
    }
    assert decision["stages"][1]["on_tie"]["next_stage_id"] is None
    assert decision["terminal_tie"]["fallback_ref"]["id"] == FALLBACK_UNIT_ID
    assert {symbol["grounding"]["kind"] for symbol in corpus["symbol_registry"]["symbols"]} == {
        "primitive",
        "definition",
        "table",
    }


def test_schema_rejects_compiled_clause_without_targets(
    validator: Draft202012Validator,
) -> None:
    corpus = build_corpus()
    del corpus["clause_dispositions"][0]["disposition"]["targets"]

    assert_invalid(validator, corpus)


Mutation = Callable[[dict[str, Any]], None]


def remove_decision_stage_key(corpus: dict[str, Any]) -> None:
    del corpus["semantic_units"][0]["stages"][0]["key"]


def remove_exception_effect_member(corpus: dict[str, Any]) -> None:
    del corpus["exceptions"][0]["effect"]["replacement"]


def empty_table_row_cells(corpus: dict[str, Any]) -> None:
    corpus["tables"][0]["rows"][0]["cells"] = []


def remove_table_cell_column(corpus: dict[str, Any]) -> None:
    del corpus["tables"][0]["rows"][0]["cells"][0]["column_id"]


def empty_correction_targets(corpus: dict[str, Any]) -> None:
    corpus["correction_applications"][0]["target_refs"] = []


@pytest.mark.parametrize(
    "mutate",
    [
        remove_decision_stage_key,
        remove_exception_effect_member,
        empty_table_row_cells,
        remove_table_cell_column,
        empty_correction_targets,
    ],
    ids=[
        "decision-stage",
        "exception-effect",
        "table-row",
        "table-cell",
        "correction-application",
    ],
)
def test_schema_rejects_malformed_nested_shapes(
    validator: Draft202012Validator, mutate: Mutation
) -> None:
    corpus = build_corpus()
    mutate(corpus)

    assert_invalid(validator, corpus)


def test_schema_rejects_forbidden_extra_properties(
    validator: Draft202012Validator,
) -> None:
    corpus = build_corpus()
    corpus["exceptions"][0]["effect"]["explanation"] = "not part of the IR"

    assert_invalid(validator, corpus)


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("corpus_sha256",), "f" * 64),
        (("source_snapshot", "document_nodes_sha256"), "C" * 63),
        (("clause_dispositions", 0, "clause_id"), "P-1.1:clause:1"),
        (("semantic_units", 0, "unit_id"), "1 invalid unit"),
        (("symbol_registry", "symbols", 0, "symbol_id"), "Candidate-Score"),
        (
            ("correction_applications", 0, "overlay_id"),
            "BBV3-CORR-0123456789abcdef",
        ),
    ],
    ids=[
        "lowercase-corpus-hash",
        "short-source-hash",
        "clause-id",
        "identifier",
        "symbol-id",
        "overlay-id",
    ],
)
def test_schema_rejects_invalid_hashes_and_ids(
    validator: Draft202012Validator,
    path: tuple[str | int, ...],
    invalid_value: str,
) -> None:
    corpus = deepcopy(build_corpus())
    target: Any = corpus
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = invalid_value

    assert_invalid(validator, corpus)
