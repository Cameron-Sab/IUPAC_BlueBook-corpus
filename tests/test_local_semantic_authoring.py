from __future__ import annotations

from scripts.build_compact_semantic_tasks import load_json
from scripts.local_semantic_authoring import (
    DEFAULT_BOOTSTRAP_DIR,
    DEFAULT_EXAMPLE,
    assemble_authoring,
    build_prompt,
    validate_authoring,
)
from scripts.local_semantic_compaction import build_candidate_view


def test_gold_plan_assembles_and_passes_strict_compiler() -> None:
    example = load_json(DEFAULT_EXAMPLE)
    task_id = example["task_id"]
    task = load_json(
        DEFAULT_BOOTSTRAP_DIR.parents[0] / "compact_semantic_tasks" / f"{task_id}.json"
    )
    bootstrap = example
    plan = {
        key: example[key]
        for key in ("task_id", "clauses", "symbols", "units", "exceptions", "examples")
    }

    source = assemble_authoring(plan, bootstrap)
    validation, chunk, _report = validate_authoring(source, task)

    assert validation["passed"] is True
    assert chunk is not None
    assert source["tables"] == bootstrap["tables"]
    assert source["refs"] == bootstrap["refs"]


def test_prompt_marks_mechanical_assets_and_uses_compact_example() -> None:
    example = load_json(DEFAULT_EXAMPLE)
    task_id = example["task_id"]
    task = load_json(
        DEFAULT_BOOTSTRAP_DIR.parents[0] / "compact_semantic_tasks" / f"{task_id}.json"
    )
    bootstrap = example

    prompt = build_prompt(build_candidate_view(task, bootstrap), bootstrap, example)

    assert "COMPACT AUTHORING EXAMPLE" in prompt
    assert "MECHANICALLY RETAINED OBJECTS" in prompt
    assert '"task_id":"P-100-part-001"' in prompt
