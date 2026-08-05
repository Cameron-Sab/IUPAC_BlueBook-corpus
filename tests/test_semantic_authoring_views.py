from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.build_compact_semantic_tasks import load_json, model_view_bytes
from scripts.render_semantic_authoring_task import (
    authoring_view_bytes,
    authoring_view_rows,
)
from scripts.scaffold_semantic_authoring import (
    authoring_metrics,
    scaffold_authoring,
)
from scripts.scaffold_semantic_delta import scaffold_delta


ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "work" / "compact_semantic_tasks"


def task(task_id: str) -> dict:
    return load_json(TASK_DIR / f"{task_id}.json")


def test_sparse_skeleton_has_one_locked_or_unresolved_slot_per_clause() -> None:
    source_task = task("P-65-part-001")
    authoring = scaffold_authoring(source_task)
    metrics = authoring_metrics(source_task, authoring)

    assert metrics["clause_count"] == len(authoring["clauses"])
    assert metrics["prefilled_clause_count"] + metrics["unresolved_clause_count"] == (
        metrics["clause_count"]
    )
    assert metrics["prefilled_citation_count"] + metrics["unresolved_citation_count"] == (
        metrics["citation_count"]
    )
    assert all(item is None or item == [] for item in authoring["clauses"])


def test_authoring_view_contains_exactly_unresolved_clause_and_reference_evidence() -> None:
    source_task = task("P-43-part-001")
    scaffold = scaffold_delta(source_task)
    rows = authoring_view_rows(source_task)
    unresolved_clause_ids = {row[2] for row in rows if row[0] == "U"}
    unresolved_occurrence_ids = {row[1] for row in rows if row[0] == "X"}
    all_clause_ids = {
        unit["clause_id"]
        for rule in source_task["rules"]
        for unit in rule["source_units"]
    }
    all_occurrence_ids = {
        reference["occurrence_id"]
        for rule in source_task["rules"]
        for reference in rule["references"]
    }
    mechanical_clause_ids = {
        item["clause_id"] for item in scaffold["clause_dispositions"]
    }
    mechanical_occurrence_ids = {
        occurrence_id
        for item in scaffold["citation_bindings"]
        for occurrence_id in item["occurrence_ids"]
    }

    assert unresolved_clause_ids == all_clause_ids - mechanical_clause_ids
    assert unresolved_occurrence_ids == all_occurrence_ids - mechanical_occurrence_ids
    assert not unresolved_clause_ids.intersection(mechanical_clause_ids)
    assert not unresolved_occurrence_ids.intersection(mechanical_occurrence_ids)


def test_sparse_views_reduce_total_fleet_input_without_losing_task_hashes() -> None:
    manifest = load_json(TASK_DIR / "manifest.json")
    tasks = [task(entry["task_id"]) for entry in manifest["tasks"]]

    sparse = sum(len(authoring_view_bytes(item)) for item in tasks)
    legacy_compact = sum(len(model_view_bytes(item)) for item in tasks)

    assert sparse < legacy_compact
    for item in tasks[:5]:
        header = json.loads(authoring_view_bytes(item).splitlines()[0])
        assert header["task_id"] == item["task_id"]
        assert header["task_sha256"] == item["task_sha256"]


def test_direct_clis_work(tmp_path: Path) -> None:
    task_path = TASK_DIR / "P-40-part-001.json"
    skeleton = tmp_path / "P-40.authoring.json"
    view = tmp_path / "P-40.view.jsonl"
    scaffold_result = subprocess.run(
        [
            sys.executable,
            "scripts/scaffold_semantic_authoring.py",
            str(task_path),
            "--output",
            str(skeleton),
            "--metrics",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    view_result = subprocess.run(
        [
            sys.executable,
            "scripts/render_semantic_authoring_task.py",
            str(task_path),
            "--output",
            str(view),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert scaffold_result.returncode == 0, scaffold_result.stderr
    assert view_result.returncode == 0, view_result.stderr
    assert skeleton.exists()
    assert view.exists()
