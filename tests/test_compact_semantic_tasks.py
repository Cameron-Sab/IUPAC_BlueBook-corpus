from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest

from scripts.build_compact_semantic_tasks import (
    BASE,
    build_tasks,
    canonical_json_bytes,
    digest_without_field,
    file_hash,
    load_json,
    model_view_bytes,
    sha256_bytes,
    validate_tasks,
    write_or_check,
)
from scripts.compile_semantic_delta import compile_delta, finalize_delta
from scripts.migrate_normalized_chunk_to_delta import migrate_chunk
from scripts.audit_semantic_delta_progress import audit_progress
from scripts.render_compact_semantic_task import select_rules, validate_task
from scripts.scaffold_semantic_authoring import scaffold_authoring


ROOT = Path(__file__).resolve().parents[1]
LEGACY_PACKET_BYTES = 335_255_522


@lru_cache(maxsize=1)
def tasks_and_manifest() -> tuple[list[dict], dict]:
    source = load_json(BASE / "bluebook_v3_source_corpus.json")
    corrections = load_json(BASE / "bluebook_v3_correction_overlays.json")
    clauses = load_json(BASE / "bluebook_v3_clause_inventory.json")
    occurrences = load_json(BASE / "bluebook_v3_reference_occurrences.json")
    resolutions = load_json(BASE / "bluebook_v3_reference_resolutions.json")
    hashes = {
        "source_corpus_sha256": file_hash(BASE / "bluebook_v3_source_corpus.json"),
        "document_nodes_sha256": clauses["document_nodes_sha256"],
        "correction_overlays_sha256": file_hash(
            BASE / "bluebook_v3_correction_overlays.json"
        ),
        "clause_inventory_sha256": file_hash(
            BASE / "bluebook_v3_clause_inventory.json"
        ),
        "reference_occurrences_sha256": file_hash(
            BASE / "bluebook_v3_reference_occurrences.json"
        ),
        "reference_resolutions_sha256": file_hash(
            BASE / "bluebook_v3_reference_resolutions.json"
        ),
    }
    return build_tasks(
        source, corrections, clauses, occurrences, resolutions, hashes
    )


def task_by_id(task_id: str) -> dict:
    return next(task for task in tasks_and_manifest()[0] if task["task_id"] == task_id)


def base_delta() -> dict:
    return load_json(ROOT / "tests" / "fixtures" / "P-40-part-001.delta.json")


def add_task_reference(task: dict, *, ambiguous: bool) -> None:
    context = "See P-2."
    task["rules"][0]["references"].append(
        {
            "occurrence_id": "P-40:xref:0001",
            "reference_kind": "text",
            "reference_text": "P-2",
            "cited_rule_id": "P-2",
            "raw_target_rule_id": "P-2",
            "effective_target_rule_id": "P-2",
            "effective_target_kind": (
                "external_or_historical" if ambiguous else "rule"
            ),
            "resolution_id": None,
            "resolution_kind": None,
            "correction_overlay_id": None,
            "context_text": context,
            "context_sha256": sha256_bytes(context.encode("utf-8")),
        }
    )
    task["metrics"]["reference_count"] += 1
    task["task_sha256"] = digest_without_field(task, "task_sha256")


def test_compact_tasks_are_complete_schema_valid_and_self_hashed() -> None:
    tasks, manifest = tasks_and_manifest()
    validate_tasks(tasks, manifest)
    assert len(tasks) == manifest["task_count"] == 151
    assert manifest["assigned_rule_count"] == 2_554
    assert manifest["assigned_clause_count"] == 32_408
    assert manifest["assigned_reference_count"] == 4_023
    assert manifest["assigned_reference_resolution_count"] == 3


def test_compact_model_view_removes_over_97_percent_of_legacy_packet_bytes() -> None:
    tasks, manifest = tasks_and_manifest()
    assert manifest["task_file_bytes"] < 17_000_000
    assert manifest["model_view_bytes"] < LEGACY_PACKET_BYTES * 0.03
    assert manifest["model_view_bytes"] == sum(
        len(model_view_bytes(task)) for task in tasks
    )


def test_model_view_is_source_bound_and_rule_selectable() -> None:
    task = task_by_id("P-20-part-001")
    validate_task(task)
    selected = select_rules(task, ["P-20"])
    lines = model_view_bytes(selected).decode("utf-8").splitlines()
    header = json.loads(lines[0])
    rows = [json.loads(line) for line in lines[1:]]
    assert header["task_sha256"] == task["task_sha256"]
    assert rows[0] == ["R", "P-20", "chapter:P-2"]
    assert [row[1] for row in rows if row[0] == "C"] == [
        unit["clause_id"] for unit in task["rules"][0]["source_units"]
    ]


def test_compact_tasks_preserve_example_ancestor_context() -> None:
    tasks, _manifest = tasks_and_manifest()
    units = [
        unit for task in tasks for rule in task["rules"] for unit in rule["source_units"]
    ]
    labels = [unit for unit in units if unit["node_kind"] == "example_block"]
    descendants = [
        unit for unit in units if "example_block" in unit["ancestor_node_kinds"]
    ]
    assert len(labels) == 1_745
    assert len(descendants) == 14_528
    assert all(unit["unit_kind"] == "example_label" for unit in labels)

    task = task_by_id("P-12-part-001")
    rows = [
        json.loads(line)
        for line in model_view_bytes(task).decode("utf-8").splitlines()[1:]
    ]
    first_example_child = next(row for row in rows if row[1] == "P-12.1:clause:0025")
    assert first_example_child[4] == ["example_block"]


def test_byte_exact_check_rejects_stale_or_extra_tasks(tmp_path: Path) -> None:
    tasks, manifest = tasks_and_manifest()
    write_or_check(tmp_path, tasks, manifest, check=False)
    write_or_check(tmp_path, tasks, manifest, check=True)
    changed = tmp_path / "P-20-part-001.json"
    changed.write_bytes(changed.read_bytes() + b" ")
    with pytest.raises(ValueError, match="stale or altered"):
        write_or_check(tmp_path, tasks, manifest, check=True)


def test_delta_compiler_generates_mechanical_fields_and_passes_strict_gate() -> None:
    task = task_by_id("P-40-part-001")
    delta = base_delta()
    chunk, result = compile_delta(delta, task)
    assert result["passed"] is True
    assert chunk["packet_id"] == task["task_id"]
    assert chunk["assigned_rule_ids"] == task["assigned_rule_ids"]
    assert chunk["records"][0]["reference_ids"] == [
        "reference.p_40.hierarchy_parent"
    ]
    assert chunk["references"][0]["relation"] == "hierarchy_parent"
    assert chunk["references"][0]["target"] == {
        "kind": "chapter",
        "id": "chapter:P-4",
    }


def test_delta_chunk_round_trip_is_deterministic() -> None:
    task = task_by_id("P-40-part-001")
    expected = base_delta()
    chunk, result = compile_delta(deepcopy(expected), task)
    assert result["passed"] is True
    assert migrate_chunk(chunk, task) == expected


def test_delta_compiler_rejects_missing_clause_and_occurrence_coverage() -> None:
    p40_task = task_by_id("P-40-part-001")
    missing_clause = base_delta()
    missing_clause["clause_dispositions"] = missing_clause["clause_dispositions"][:-1]
    finalize_delta(missing_clause, p40_task)
    with pytest.raises(ValueError, match="exactly cover task clauses"):
        compile_delta(missing_clause, p40_task)

    task_with_occurrence = deepcopy(p40_task)
    add_task_reference(task_with_occurrence, ambiguous=False)
    missing_occurrence = base_delta()
    finalize_delta(missing_occurrence, task_with_occurrence)
    with pytest.raises(ValueError, match="every task occurrence exactly once"):
        compile_delta(missing_occurrence, task_with_occurrence)


def test_delta_compiler_rejects_stale_delta_hash() -> None:
    task = task_by_id("P-40-part-001")
    delta = base_delta()
    delta["clause_dispositions"][0]["role"] = "source_metadata"
    with pytest.raises(ValueError, match="Delta SHA-256"):
        compile_delta(delta, task)


def test_strict_gate_rejects_nonoperative_example_descendant() -> None:
    task = deepcopy(task_by_id("P-40-part-001"))
    task["rules"][0]["source_units"][0]["ancestor_node_kinds"] = ["example_block"]
    task["task_sha256"] = digest_without_field(task, "task_sha256")
    delta = base_delta()
    finalize_delta(delta, task)
    _chunk, result = compile_delta(delta, task)
    assert result["passed"] is False
    assert "disposition.example_context_nonoperative" in {
        error["code"] for error in result["errors"]
    }


def test_delta_compiler_rejects_ambiguous_target_without_semantic_override() -> None:
    task = deepcopy(task_by_id("P-40-part-001"))
    add_task_reference(task, ambiguous=True)
    delta = base_delta()
    delta["citation_bindings"] = [
        {
            "reference_id": "reference.p40.cites_p2",
            "clause_ids": ["P-40:clause:0001"],
            "relation": "cites",
            "occurrence_ids": ["P-40:xref:0001"],
            "resolution": "exact",
            "ordered_member_refs": [],
        }
    ]
    finalize_delta(delta, task)
    with pytest.raises(ValueError, match="needs target_override"):
        compile_delta(delta, task)


def test_progress_auditor_never_counts_missing_deltas_as_complete(
    tmp_path: Path,
) -> None:
    tasks, manifest = tasks_and_manifest()
    task_dir = tmp_path / "tasks"
    delta_dir = tmp_path / "deltas"
    write_or_check(task_dir, tasks, manifest, check=False)
    empty = audit_progress(task_dir, delta_dir)
    assert empty["passed"] is True
    assert empty["complete"] is False
    assert empty["completed_task_count"] == 0
    assert empty["missing_task_count"] == 151

    delta_dir.mkdir()
    fixture = ROOT / "tests" / "fixtures" / "P-40-part-001.delta.json"
    (delta_dir / fixture.name.replace(".delta", "")).write_bytes(
        fixture.read_bytes()
    )
    partial = audit_progress(task_dir, delta_dir)
    assert partial["passed"] is True
    assert partial["complete"] is False
    assert partial["completed_task_ids"] == ["P-40-part-001"]
    assert partial["completed_rule_count"] == 1
    assert partial["completed_clause_count"] == 7


def test_progress_auditor_reopens_stale_authoring(tmp_path: Path) -> None:
    tasks, manifest = tasks_and_manifest()
    task_dir = tmp_path / "tasks"
    delta_dir = tmp_path / "deltas"
    authoring_dir = tmp_path / "semantic_authoring"
    write_or_check(task_dir, tasks, manifest, check=False)
    delta_dir.mkdir()
    authoring_dir.mkdir()
    fixture = ROOT / "tests" / "fixtures" / "P-40-part-001.delta.json"
    (delta_dir / "P-40-part-001.json").write_bytes(fixture.read_bytes())
    task = load_json(task_dir / "P-40-part-001.json")
    authoring = scaffold_authoring(task)
    (authoring_dir / "P-40-part-001.json").write_bytes(
        canonical_json_bytes(authoring)
    )

    report = audit_progress(task_dir, delta_dir)

    assert report["completed_task_count"] == 0
    assert report["stale_task_ids"] == ["P-40-part-001"]
    assert "is unresolved under the current scaffold" in report["stale_tasks"][0]["error"]


def test_direct_cli_help_works() -> None:
    import subprocess
    import sys

    for script in (
        "scripts/build_compact_semantic_tasks.py",
        "scripts/render_compact_semantic_task.py",
        "scripts/compile_semantic_delta.py",
        "scripts/migrate_normalized_chunk_to_delta.py",
        "scripts/audit_semantic_delta_progress.py",
    ):
        result = subprocess.run(
            [sys.executable, script, "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
