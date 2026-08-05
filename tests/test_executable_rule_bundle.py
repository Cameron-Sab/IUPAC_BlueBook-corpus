from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from iupac_rule_runtime import (
    CapabilityError,
    CapabilityRegistry,
    ExecutionError,
    RuleRuntime,
)
from iupac_rule_runtime.runtime import digest_without_field
from scripts.compile_executable_rule_bundle import (
    BundleCompileError,
    compile_bundle,
    normalized_source_digest,
    validate_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
DELTA_FIXTURE = ROOT / "tests" / "fixtures" / "P-40-part-001.delta.json"


def expression(expression_id: str, op: str, **values: Any) -> dict[str, Any]:
    return {
        "expression_id": expression_id,
        "clause_ids": ["P-1:clause:0001"],
        "op": op,
        **values,
    }


def statement(statement_id: str, op: str, **values: Any) -> dict[str, Any]:
    return {
        "statement_id": statement_id,
        "clause_ids": ["P-1:clause:0001"],
        "op": op,
        **values,
    }


def scope(prefix: str) -> dict[str, Any]:
    return {
        "regimes": ["all"],
        "applies_to": expression(f"expr.{prefix}.scope", "literal", value=True),
    }


def symbol(symbol_id: str, kind: str, arguments: list[dict[str, str]], returns: str) -> dict[str, Any]:
    return {
        "symbol_id": symbol_id,
        "kind": kind,
        "description": f"Test operation {symbol_id}.",
        "arguments": arguments,
        "returns": returns,
        "grounding": {"kind": "primitive", "refs": [], "primitive": "test-host"},
    }


def build_test_chunk() -> dict[str, Any]:
    size_rule = {
        "unit_id": "unit.test.size",
        "kind": "rule",
        "force": "required",
        "clause_ids": ["P-1:clause:0001"],
        "scope": scope("size"),
        "inputs": [{"name": "count", "type": "integer"}],
        "outputs": [{"name": "category", "type": "string"}],
        "when": expression(
            "expr.size.when",
            "predicate",
            symbol="test.is_large",
            args=[expression("expr.size.count", "var", name="count")],
        ),
        "then": [
            statement(
                "stmt.size.large",
                "assign",
                target="category",
                value=expression("expr.size.large", "literal", value="large"),
            )
        ],
        "else": [
            statement(
                "stmt.size.small",
                "assign",
                target="category",
                value=expression("expr.size.small", "literal", value="small"),
            )
        ],
    }
    decision = {
        "unit_id": "unit.test.choose",
        "kind": "decision",
        "force": "preference",
        "clause_ids": ["P-1:clause:0001"],
        "scope": scope("choose"),
        "inputs": [{"name": "candidates", "type": "CandidateList"}],
        "outputs": [{"name": "winner", "type": "Candidate"}],
        "candidates": expression("expr.choose.candidates", "var", name="candidates"),
        "stages": [
            {
                "stage_id": "stage.choose.minimum",
                "ordinal": 1,
                "clause_ids": ["P-1:clause:0001"],
                "guard": expression("expr.choose.guard", "literal", value=True),
                "key": expression(
                    "expr.choose.score",
                    "get",
                    **{
                        "from": expression("expr.choose.candidate", "var", name="candidate"),
                        "path": "score",
                    },
                ),
                "comparator": {
                    "kind": "numeric",
                    "direction": "minimum",
                    "symbol": None,
                    "table_id": None,
                },
                "on_tie": {"mode": "continue", "next_stage_id": None},
            }
        ],
        "terminal_tie": {"mode": "retain_coequal", "fallback_ref": None},
    }
    chunk = {
        "format": "iupac-bluebook-normalized-rule-chunk",
        "format_version": "1.0.0",
        "packet_id": "P-1-part-001",
        "assigned_rule_ids": ["P-1"],
        "symbol_declarations": [
            symbol(
                "test.is_large",
                "predicate",
                [{"name": "count", "type": "integer"}],
                "boolean",
            )
        ],
        "records": [
            {
                "record_id": "bluebook-v3:P-1",
                "source_rule_id": "P-1",
                "semantic_unit_ids": ["unit.test.size", "unit.test.choose"],
            }
        ],
        "semantic_units": [size_rule, decision],
        "exceptions": [],
        "tables": [],
        "dependency_edges": [],
    }
    chunk["chunk_sha256"] = normalized_source_digest(chunk, "chunk_sha256")
    return chunk


def compile_test_bundle() -> dict[str, Any]:
    return compile_bundle([build_test_chunk()], allow_partial=True)


def build_opcode_chunk() -> dict[str, Any]:
    child = {
        "unit_id": "unit.test.child",
        "kind": "rule",
        "force": "required",
        "clause_ids": ["P-1.2:clause:0001"],
        "scope": scope("child"),
        "inputs": [{"name": "x", "type": "integer"}],
        "outputs": [{"name": "invoked", "type": "integer"}],
        "when": expression("expr.child.when", "literal", value=True),
        "then": [
            statement(
                "stmt.child.assign",
                "assign",
                target="invoked",
                value=expression("expr.child.x", "var", name="x"),
            )
        ],
        "else": [],
    }
    item_var = lambda suffix: expression(f"expr.ops.item.{suffix}", "var", name="item")
    parent = {
        "unit_id": "unit.test.ops",
        "kind": "procedure",
        "force": "required",
        "clause_ids": ["P-1:clause:0001"],
        "scope": scope("ops"),
        "inputs": [
            {"name": "items", "type": "IntegerList"},
            {"name": "expected", "type": "integer"},
        ],
        "outputs": [],
        "steps": [
            statement(
                "stmt.ops.sequence",
                "sequence",
                steps=[
                    statement(
                        "stmt.ops.initialize",
                        "assign",
                        target="total",
                        value=expression("expr.ops.zero", "literal", value=0),
                    ),
                    statement(
                        "stmt.ops.iterate",
                        "iterate",
                        bind="item",
                        **{
                            "in": expression("expr.ops.items", "var", name="items"),
                            "body": [
                                statement(
                                    "stmt.ops.add",
                                    "transform",
                                    target="total",
                                    transformation="test.add",
                                    args=[item_var("add")],
                                )
                            ],
                            "stop_when": expression(
                                "expr.ops.stop", "literal", value=False
                            ),
                        },
                    ),
                    statement(
                        "stmt.ops.table",
                        "assign",
                        target="element_name",
                        value=expression(
                            "expr.ops.table_lookup",
                            "table_lookup",
                            table_id="table.test.elements",
                            key=expression("expr.ops.table_key", "literal", value="C"),
                            column_id="column.element_name",
                        ),
                    ),
                    statement(
                        "stmt.ops.branch",
                        "branch",
                        when=expression(
                            "expr.ops.conditions",
                            "all",
                            args=[
                                expression(
                                    "expr.ops.not_false",
                                    "not",
                                    arg=expression(
                                        "expr.ops.any_false",
                                        "any",
                                        args=[
                                            expression(
                                                "expr.ops.false", "literal", value=False
                                            )
                                        ],
                                    ),
                                ),
                                expression(
                                    "expr.ops.exists",
                                    "exists",
                                    bind="member",
                                    **{
                                        "in": expression(
                                            "expr.ops.exists.items", "var", name="items"
                                        ),
                                        "where": expression(
                                            "expr.ops.exists.compare",
                                            "compare",
                                            relation="gt",
                                            left=expression(
                                                "expr.ops.exists.member",
                                                "var",
                                                name="member",
                                            ),
                                            right=expression(
                                                "expr.ops.exists.one", "literal", value=1
                                            ),
                                        ),
                                    },
                                ),
                                expression(
                                    "expr.ops.forall",
                                    "forall",
                                    bind="member",
                                    **{
                                        "in": expression(
                                            "expr.ops.forall.items", "var", name="items"
                                        ),
                                        "where": expression(
                                            "expr.ops.forall.compare",
                                            "compare",
                                            relation="ge",
                                            left=expression(
                                                "expr.ops.forall.member",
                                                "var",
                                                name="member",
                                            ),
                                            right=expression(
                                                "expr.ops.forall.one", "literal", value=1
                                            ),
                                        ),
                                    },
                                ),
                                expression(
                                    "expr.ops.identity",
                                    "function",
                                    symbol="test.identity",
                                    args=[
                                        expression(
                                            "expr.ops.identity.true", "literal", value=True
                                        )
                                    ],
                                ),
                            ],
                        ),
                        then=[
                            statement(
                                "stmt.ops.emit_total",
                                "emit",
                                value=expression("expr.ops.total.emit", "var", name="total"),
                            )
                        ],
                        **{
                            "else": [
                                statement(
                                    "stmt.ops.emit_zero",
                                    "emit",
                                    value=expression(
                                        "expr.ops.zero.emit", "literal", value=0
                                    ),
                                )
                            ]
                        },
                    ),
                    statement(
                        "stmt.ops.render",
                        "render",
                        component="parent",
                        position="suffix",
                        value=expression(
                            "expr.ops.element_name", "var", name="element_name"
                        ),
                    ),
                    statement(
                        "stmt.ops.invoke",
                        "invoke",
                        rule_id="P-1.2",
                        bindings={
                            "x": expression("expr.ops.total.invoke", "var", name="total")
                        },
                    ),
                    statement(
                        "stmt.ops.emit_outcome",
                        "emit",
                        value=expression(
                            "expr.ops.outcome",
                            "rule_outcome",
                            rule_id="P-1.2",
                            outcome="applied",
                        ),
                    ),
                    statement(
                        "stmt.ops.assert",
                        "assert",
                        assertion=expression(
                            "expr.ops.assertion",
                            "compare",
                            relation="eq",
                            left=expression("expr.ops.total.assert", "var", name="total"),
                            right=expression("expr.ops.expected", "var", name="expected"),
                        ),
                        reason_code="reason.test.bad_total",
                    ),
                    statement(
                        "stmt.ops.reject",
                        "reject",
                        target="discarded_candidate",
                        reason_code="reason.test.demonstration",
                    ),
                ],
            )
        ],
    }
    table = {
        "table_id": "table.test.elements",
        "label": "Test",
        "title": "Element names",
        "clause_ids": ["P-1:clause:0001"],
        "columns": [
            {
                "column_id": "column.symbol",
                "ordinal": 1,
                "label": "Symbol",
                "value_type": "string",
                "clause_ids": ["P-1:clause:0001"],
            },
            {
                "column_id": "column.element_name",
                "ordinal": 2,
                "label": "Name",
                "value_type": "string",
                "clause_ids": ["P-1:clause:0001"],
            },
        ],
        "rows": [
            {
                "row_id": "row.carbon",
                "ordinal": 1,
                "rank_group": None,
                "clause_ids": ["P-1:clause:0001"],
                "cells": [
                    {
                        "cell_id": "cell.carbon.symbol",
                        "column_id": "column.symbol",
                        "value": "C",
                        "clause_ids": ["P-1:clause:0001"],
                    },
                    {
                        "cell_id": "cell.carbon.name",
                        "column_id": "column.element_name",
                        "value": "carbon",
                        "clause_ids": ["P-1:clause:0001"],
                    },
                ],
            }
        ],
        "footnotes": [],
        "contract": {
            "key_column_ids": ["column.symbol"],
            "result_column_ids": ["column.element_name"],
            "cardinality": "one_to_one",
            "ordering": "none",
        },
    }
    chunk = {
        "format": "iupac-bluebook-normalized-rule-chunk",
        "format_version": "1.0.0",
        "packet_id": "P-1-part-ops",
        "assigned_rule_ids": ["P-1", "P-1.2"],
        "symbol_declarations": [
            symbol(
                "test.add",
                "transformation",
                [
                    {"name": "current", "type": "integer"},
                    {"name": "value", "type": "integer"},
                ],
                "integer",
            ),
            symbol(
                "test.identity",
                "function",
                [{"name": "value", "type": "boolean"}],
                "boolean",
            ),
        ],
        "records": [
            {
                "record_id": "bluebook-v3:P-1",
                "source_rule_id": "P-1",
                "semantic_unit_ids": ["unit.test.ops"],
            },
            {
                "record_id": "bluebook-v3:P-1.2",
                "source_rule_id": "P-1.2",
                "semantic_unit_ids": ["unit.test.child"],
            },
        ],
        "semantic_units": [parent, child],
        "exceptions": [],
        "tables": [table],
        "dependency_edges": [],
    }
    chunk["chunk_sha256"] = normalized_source_digest(chunk, "chunk_sha256")
    return chunk


def test_compiler_emits_schema_valid_deterministic_if_then_bundle() -> None:
    first = compile_test_bundle()
    second = compile_test_bundle()

    validate_bundle(first)
    assert first == second
    assert first["execution_model"] == "ordered-if-then-v1"
    assert first["complete"] is False
    assert first["programs"][0]["when"]["op"] == "predicate"
    assert first["programs"][0]["then"][0]["op"] == "assign"
    assert first["programs"][0]["else"][0]["op"] == "assign"
    assert first["bundle_sha256"] == digest_without_field(first, "bundle_sha256")


def test_partial_compilation_must_be_explicit() -> None:
    with pytest.raises(BundleCompileError, match="--allow-partial"):
        compile_bundle([build_test_chunk()])


def test_incomplete_object_cannot_masquerade_as_a_complete_corpus() -> None:
    fake = build_test_chunk()
    fake["format"] = "iupac-bluebook-normalized-rule-language"
    fake["format_version"] = "3.0.0"
    fake.pop("chunk_sha256")
    fake["corpus_sha256"] = normalized_source_digest(fake, "corpus_sha256")

    with pytest.raises(BundleCompileError, match="Complete normalized corpus validation failed"):
        compile_bundle([fake])


def test_runtime_audits_all_capabilities_before_execution() -> None:
    bundle = compile_test_bundle()

    with pytest.raises(CapabilityError) as error:
        RuleRuntime(bundle)

    assert error.value.missing == ("test.is_large",)


def test_runtime_executes_then_else_and_decision_stages() -> None:
    capabilities = CapabilityRegistry().register(
        "test.is_large", "predicate", lambda count: count >= 3
    )
    runtime = RuleRuntime(compile_test_bundle(), capabilities)

    assert runtime.execute("unit.test.size", {"count": 4}).values["category"] == "large"
    assert runtime.execute("unit.test.size", {"count": 2}).values["category"] == "small"
    candidates = [{"name": "b", "score": 4}, {"name": "a", "score": 1}]
    result = runtime.execute("unit.test.choose", {"candidates": candidates})
    assert result.values["winner"] == {"name": "a", "score": 1}
    assert result.trace[-2]["event"] == "decision_stage"


def test_runtime_executes_all_statement_families_and_remaining_expressions() -> None:
    bundle = compile_bundle([build_opcode_chunk()], allow_partial=True)
    validate_bundle(bundle)
    capabilities = (
        CapabilityRegistry()
        .register("test.add", "transformation", lambda current, value: current + value)
        .register("test.identity", "function", lambda value: value)
    )

    result = RuleRuntime(bundle, capabilities).execute(
        "unit.test.ops", {"items": [1, 2, 3], "expected": 6}
    )

    assert result.values["total"] == 6
    assert result.values["element_name"] == "carbon"
    assert result.values["invoked"] == 6
    assert result.emitted == [6, True]
    assert result.rendered == [
        {"component": "parent", "position": "suffix", "value": "carbon"}
    ]
    assert result.rejected == [
        {"target": "discarded_candidate", "reason_code": "reason.test.demonstration"}
    ]
    assert result.outcomes["P-1.2"] == "applied"


def test_runtime_rejects_mutated_bundle_before_execution() -> None:
    bundle = compile_test_bundle()
    bundle["programs"][0]["force"] = "permitted"
    capabilities = CapabilityRegistry().register(
        "test.is_large", "predicate", lambda count: count >= 3
    )

    with pytest.raises(ExecutionError, match="SHA-256"):
        RuleRuntime(bundle, capabilities)


def test_exception_precedence_is_executable_before_its_target_rule() -> None:
    chunk = build_test_chunk()
    chunk["exceptions"] = [
        {
            "exception_id": "exception.test.suppress_size",
            "clause_ids": ["P-1:clause:0001"],
            "when": expression("expr.exception.suppress", "literal", value=True),
            "target": {"kind": "semantic_unit", "id": "unit.test.size"},
            "effect": {
                "mode": "suppress",
                "replacement": None,
                "guard": None,
                "redirect": None,
            },
            "precedence": {"specificity": 1, "source_order": 1},
        }
    ]
    chunk["chunk_sha256"] = normalized_source_digest(chunk, "chunk_sha256")
    bundle = compile_bundle([chunk], allow_partial=True)
    capabilities = CapabilityRegistry().register(
        "test.is_large", "predicate", lambda count: count >= 3
    )

    result = RuleRuntime(bundle, capabilities).execute("unit.test.size", {"count": 4})

    assert "category" not in result.values
    assert result.outcomes["unit.test.size"] == "suppressed"
    assert result.trace == [
        {
            "event": "exception",
            "exception_id": "exception.test.suppress_size",
            "mode": "suppress",
        }
    ]


def test_reference_runtime_fails_closed_for_nested_exception_targets() -> None:
    chunk = build_test_chunk()
    chunk["exceptions"] = [
        {
            "exception_id": "exception.test.stage",
            "clause_ids": ["P-1:clause:0001"],
            "when": expression("expr.exception.stage", "literal", value=True),
            "target": {"kind": "decision_stage", "id": "stage.choose.minimum"},
            "effect": {
                "mode": "suppress",
                "replacement": None,
                "guard": None,
                "redirect": None,
            },
            "precedence": {"specificity": 1, "source_order": 1},
        }
    ]
    chunk["chunk_sha256"] = normalized_source_digest(chunk, "chunk_sha256")
    bundle = compile_bundle([chunk], allow_partial=True)
    capabilities = CapabilityRegistry().register(
        "test.is_large", "predicate", lambda count: count >= 3
    )

    with pytest.raises(ExecutionError, match="decision_stage"):
        RuleRuntime(bundle, capabilities)


def test_real_p40_fixture_compiles_to_an_engine_capability_contract() -> None:
    delta = json.loads(DELTA_FIXTURE.read_text(encoding="utf-8"))
    program_ids = [program["unit_id"] for program in delta["semantic_units"]]
    chunk = {
        "format": "iupac-bluebook-normalized-rule-chunk",
        "format_version": "1.0.0",
        "packet_id": delta["task_id"],
        "assigned_rule_ids": ["P-40"],
        "symbol_declarations": delta["symbol_declarations"],
        "records": [
            {
                "record_id": "bluebook-v3:P-40",
                "source_rule_id": "P-40",
                "semantic_unit_ids": program_ids,
            }
        ],
        "semantic_units": delta["semantic_units"],
        "exceptions": delta["exceptions"],
        "tables": delta["tables"],
        "dependency_edges": [],
    }
    chunk["chunk_sha256"] = normalized_source_digest(chunk, "chunk_sha256")

    bundle = compile_bundle([chunk], allow_partial=True)
    validate_bundle(bundle)

    required = bundle["capability_contract"]["required"]
    assert bundle["metrics"]["program_count"] == len(program_ids)
    assert {item["kind"] for item in required} == {"predicate"}
    assert {item["symbol_id"] for item in required} == {
        symbol["symbol_id"] for symbol in delta["symbol_declarations"]
    }


def test_compiler_rejects_undeclared_or_wrong_kind_capabilities() -> None:
    chunk = build_test_chunk()
    chunk["symbol_declarations"] = []
    chunk["chunk_sha256"] = normalized_source_digest(chunk, "chunk_sha256")
    with pytest.raises(BundleCompileError, match="undeclared"):
        compile_bundle([chunk], allow_partial=True)

    chunk = build_test_chunk()
    chunk["symbol_declarations"][0]["kind"] = "function"
    chunk["chunk_sha256"] = normalized_source_digest(chunk, "chunk_sha256")
    with pytest.raises(BundleCompileError, match="declared as function"):
        compile_bundle([chunk], allow_partial=True)


def test_compiler_script_has_a_direct_cli() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts" / "compile_executable_rule_bundle.py"),
            "--help",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "executable rule bundle" in result.stdout
