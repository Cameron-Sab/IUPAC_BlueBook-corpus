from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.compile_semantic_authoring import (
    AuthoringError,
    compile_authoring,
    expand_authoring,
)
from scripts.build_compact_semantic_tasks import canonical_json_bytes, load_json


ROOT = Path(__file__).resolve().parents[1]
TASK = load_json(ROOT / "work" / "compact_semantic_tasks" / "P-40-part-001.json")


def authoring_source() -> dict:
    units = []
    for index in range(3, 8):
        units.append(
            {
                "id": f"clause_{index}_meaning",
                "k": "definition",
                "c": [index],
                "term": f"synthetic test meaning {index}",
                "entity": "SyntheticTestMeaning",
                "value": ["lit", f"meaning-{index}"],
                "out": [[f"meaning_{index}", "SyntheticTestMeaning"]],
            }
        )
    return {
        "format": "iupac-bluebook-semantic-authoring",
        "format_version": "1.0.0",
        "task_id": "P-40-part-001",
        "clauses": [
            None,
            ["source_metadata", "source_metadata", "skip", "source_navigation"],
            ["permission", "normative", "compile"],
            ["constraint", "normative", "compile"],
            ["scope", "informative", "compile"],
            ["permission", "normative", "compile"],
            ["scope", "informative", "compile"],
        ],
        "symbols": [],
        "units": units,
        "exceptions": [],
        "tables": [],
        "figures": [],
        "examples": [],
        "corrections": [],
        "refs": [],
        "additional_refs": [],
    }


def test_compact_authoring_expands_deterministically_and_passes_strict_gate() -> None:
    source = authoring_source()

    first_delta, first_chunk, first_report = compile_authoring(source, TASK)
    second_delta, second_chunk, second_report = compile_authoring(
        deepcopy(source), TASK
    )

    assert first_report["passed"] is True
    assert second_report["passed"] is True
    assert first_delta == second_delta
    assert first_chunk == second_chunk
    assert first_delta["task_sha256"] == TASK["task_sha256"]
    assert first_delta["semantic_units"][0]["scope"]["applies_to"] == {
        "expression_id": "expr.p40.part001.clause_3_meaning.scope",
        "clause_ids": ["P-40:clause:0003"],
        "op": "literal",
        "value": True,
    }
    compiled = first_delta["clause_dispositions"][2]["disposition"]
    assert compiled["kind"] == "compiled"
    assert compiled["targets"][0] == {
        "kind": "semantic_unit",
        "id": "unit.p40.part001.clause_3_meaning",
    }


def test_authoring_is_materially_smaller_than_expanded_delta() -> None:
    source = authoring_source()
    delta = expand_authoring(source, TASK)

    authored_bytes = canonical_json_bytes(source)
    delta_bytes = canonical_json_bytes(delta)

    assert len(authored_bytes) < len(delta_bytes) * 0.45


def test_prefix_expressions_and_statements_expand_without_authored_ids() -> None:
    source = authoring_source()
    source["symbols"] = [
        {
            "id": "test.is_allowed",
            "k": "predicate",
            "d": "Whether a synthetic candidate is allowed.",
            "a": [["candidate", "SyntheticCandidate"]],
            "ret": "boolean",
        }
    ]
    source["units"][0] = {
        "id": "conditional_test",
        "k": "rule",
        "f": "required",
        "c": [3],
        "in": [["candidate", "SyntheticCandidate"]],
        "out": [["status", "string"]],
        "if": ["pred", "test.is_allowed", ["var", "candidate"]],
        "then": [["set", "status", ["lit", "accepted"]]],
        "else": [["reject", "candidate", "reason.test_not_allowed"]],
    }

    delta, _chunk, report = compile_authoring(source, TASK)

    assert report["passed"] is True
    unit = delta["semantic_units"][0]
    assert unit["when"]["op"] == "predicate"
    assert unit["then"][0]["op"] == "assign"
    assert unit["else"][0]["op"] == "reject"
    assert unit["then"][0]["statement_id"].startswith(
        "stmt.p40.part001.conditional_test."
    )


def test_missing_clause_decision_or_semantic_target_fails_closed() -> None:
    missing = authoring_source()
    missing["clauses"].pop()
    with pytest.raises(AuthoringError, match="exactly 7"):
        expand_authoring(missing, TASK)

    no_target = authoring_source()
    no_target["units"] = no_target["units"][1:]
    with pytest.raises(AuthoringError, match="no semantic target"):
        expand_authoring(no_target, TASK)


def test_tables_examples_and_exceptions_use_compact_source_shapes() -> None:
    source = authoring_source()
    source["tables"] = [
        {
            "id": "status_codes",
            "c": [3],
            "label": "T",
            "title": "Synthetic statuses",
            "cols": [
                ["code", "Code", "string"],
                ["meaning", "Meaning", "string"],
            ],
            "rows": [
                {"id": "accepted", "v": ["A", "accepted"]},
                {"id": "rejected", "v": ["R", "rejected"]},
            ],
            "contract": {
                "key": ["code"],
                "result": ["meaning"],
                "cardinality": "one_to_one",
                "ordering": "source_order",
            },
        }
    ]
    source["examples"] = [
        {
            "id": "accepted_status",
            "c": [3],
            "input": {"code": "A"},
            "ok": ["accepted"],
            "shows": [["semantic_unit", "clause_3_meaning"]],
        }
    ]
    source["exceptions"] = [
        {
            "id": "synthetic_suppression",
            "c": [3],
            "if": ["lit", False],
            "target": ["semantic_unit", "clause_3_meaning"],
            "mode": "suppress",
            "order": 1,
        }
    ]

    delta, _chunk, report = compile_authoring(source, TASK)

    assert report["passed"] is True
    table = delta["tables"][0]
    assert table["table_id"] == "table.p40.part001.status_codes"
    assert table["columns"][0]["column_id"] == (
        "column.p40.part001.status_codes.code"
    )
    assert table["rows"][1]["cells"][1]["value"] == "rejected"
    assert delta["examples"][0]["demonstrates"][0]["id"] == (
        "unit.p40.part001.clause_3_meaning"
    )
    assert delta["exceptions"][0]["target"]["id"] == (
        "unit.p40.part001.clause_3_meaning"
    )


def test_direct_cli_help_works() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts" / "compile_semantic_authoring.py"),
            "--help",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "compact semantic authoring" in result.stdout
