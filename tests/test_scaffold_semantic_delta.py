from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.build_compact_semantic_tasks import (
    canonical_json_bytes,
    digest_without_field,
    sha256_bytes,
)
from scripts.compile_semantic_delta import compile_delta
from scripts.render_compact_semantic_task import validate_task
from scripts.scaffold_semantic_delta import (
    FORBIDDEN_OUTPUT_RE,
    scaffold_delta,
    scaffold_metrics,
)
from scripts.validate_normalized_rule_chunks import digest_without_field as delta_digest


ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "work" / "compact_semantic_tasks"


def load_task(task_id: str) -> dict:
    return json.loads((TASK_DIR / f"{task_id}.json").read_bytes())


def test_scaffold_is_schema_valid_source_bound_and_byte_deterministic() -> None:
    task = load_task("P-43-part-001")
    original = deepcopy(task)
    first = scaffold_delta(task)
    second = scaffold_delta(task)

    assert task == original
    assert first["task_id"] == task["task_id"]
    assert first["task_sha256"] == task["task_sha256"]
    assert first["delta_sha256"] == delta_digest(first, "delta_sha256")
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert not FORBIDDEN_OUTPUT_RE.search(canonical_json_bytes(first).decode("utf-8"))


def test_scaffold_only_classifies_proven_structural_nonoperative_clauses() -> None:
    task = load_task("P-65-part-001")
    delta = scaffold_delta(task)
    dispositions = {item["clause_id"]: item for item in delta["clause_dispositions"]}

    assert dispositions["P-65:clause:0001"]["disposition"]["reason_code"] == "heading_or_title"
    assert dispositions["P-65:clause:0002"]["disposition"]["reason_code"] == "source_navigation"
    example_label = next(
        unit
        for rule in task["rules"]
        for unit in rule["source_units"]
        if unit["unit_kind"] == "example_label"
    )
    assert dispositions[example_label["clause_id"]]["disposition"]["reason_code"] == "example_label"
    example_child = next(
        unit
        for rule in task["rules"]
        for unit in rule["source_units"]
        if "example_block" in unit["ancestor_node_kinds"]
    )
    assert example_child["clause_id"] not in dispositions
    assert "P-65.0:clause:0002" not in dispositions


def test_citations_are_bound_only_when_the_source_clause_is_unique() -> None:
    task = load_task("P-43-part-001")
    delta = scaffold_delta(task)
    assert [binding["occurrence_ids"] for binding in delta["citation_bindings"]] == [
        ["P-43:xref:0001"],
        ["P-43:xref:0002"],
    ]
    assert {tuple(binding["clause_ids"]) for binding in delta["citation_bindings"]} == {
        ("P-43:clause:0002",)
    }

    ambiguous = deepcopy(task)
    ambiguous["rules"][0]["source_units"][0]["text"] += " P-43.0"
    ambiguous["rules"][0]["source_units"][0]["text_sha256"] = sha256_bytes(
        ambiguous["rules"][0]["source_units"][0]["text"].encode("utf-8")
    )
    ambiguous["task_sha256"] = digest_without_field(ambiguous, "task_sha256")
    validate_task(ambiguous)
    bindings = scaffold_delta(ambiguous)["citation_bindings"]
    assert [item["occurrence_ids"] for item in bindings] == [["P-43:xref:0002"]]


def test_incomplete_scaffold_cannot_pass_strict_compilation() -> None:
    task = load_task("P-10-part-001")
    delta = scaffold_delta(task)
    assert [item["clause_id"] for item in delta["clause_dispositions"]] == [
        "P-10:clause:0001"
    ]
    with pytest.raises(ValueError, match="exactly cover task clauses"):
        compile_delta(delta, task)


def test_direct_cli_writes_exact_canonical_bytes(tmp_path: Path) -> None:
    task_path = TASK_DIR / "P-10-part-001.json"
    output = tmp_path / "draft.delta.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/scaffold_semantic_delta.py",
            str(task_path),
            "--output",
            str(output),
            "--metrics",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    task = load_task("P-10-part-001")
    expected = scaffold_delta(task)
    assert output.read_bytes() == canonical_json_bytes(expected)
    assert json.loads(result.stdout) == scaffold_metrics(task, expected)


@pytest.mark.parametrize(
    "task_id", ["P-10-part-001", "P-43-part-001", "P-65-part-001"]
)
def test_representative_metrics_are_internally_exact(task_id: str) -> None:
    task = load_task(task_id)
    delta = scaffold_delta(task)
    metrics = scaffold_metrics(task, delta)
    assert metrics["remaining_clause_count"] + metrics["prefilled_clause_count"] == metrics["clause_count"]
    assert metrics["remaining_citation_count"] + metrics["prefilled_citation_count"] == metrics["citation_count"]
    assert metrics["mechanical_output_bytes_avoided"] > 0
    assert metrics["mechanical_output_tokens_approx"] == (
        metrics["mechanical_output_bytes_avoided"] + 3
    ) // 4
