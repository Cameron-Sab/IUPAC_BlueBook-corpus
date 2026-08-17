from __future__ import annotations

from pathlib import Path

from scripts.bootstrap_semantic_authoring import (
    DEFAULT_TASK_DIR,
    bootstrap_authoring,
    classify_clause,
    parse_logic,
    train_classifier,
)
from scripts.build_compact_semantic_tasks import load_json
from scripts.compile_semantic_authoring import compile_authoring


def test_parse_logic_extracts_if_then_exception_and_dependency() -> None:
    parsed = parse_logic(
        "If two parents remain, the parent with the lower locant is selected, except for P-44.2.",
        "preference_criterion",
    )

    assert parsed["operator"] == "prefer"
    assert parsed["if"] == "two parents remain"
    assert parsed["then"].startswith("the parent with the lower locant")
    assert parsed["except"] == "P-44.2"
    assert parsed["rule_dependencies"] == ["P-44.2"]


def test_heading_classifier_fails_closed_on_operative_heading() -> None:
    classifier = train_classifier()
    title, _ = classify_clause(
        {
            "unit_kind": "heading_text",
            "node_kind": "heading",
            "semantic_cue": None,
            "text": "P-25.3 ORDER OF CITATION",
        },
        classifier,
    )
    operative, _ = classify_clause(
        {
            "unit_kind": "heading_text",
            "node_kind": "heading",
            "semantic_cue": None,
            "text": "P-25.3.1 The component with the greater number of rings is selected.",
        },
        classifier,
    )

    assert title == ["heading", "informative", "compile"]
    assert operative[2] == "compile"


def test_empty_example_table_cell_is_compiled_as_typed_layout() -> None:
    decision, confidence = classify_clause(
        {
            "unit_kind": "empty_table_cell",
            "node_kind": "table",
            "ancestor_node_kinds": ["example_block"],
            "semantic_cue": None,
            "text": None,
        },
        train_classifier(),
    )

    assert decision == ["table_layout", "illustrative", "compile"]
    assert confidence == 1.0


def test_bootstrap_authoring_strictly_compiles_real_missing_task() -> None:
    task_path = DEFAULT_TASK_DIR / "P-73-part-002.json"
    if not task_path.exists():
        return
    task = load_json(task_path)
    authoring, report = bootstrap_authoring(task, train_classifier())

    assert not any(slot == [] for slot in authoring["clauses"])
    assert report["semantic_unit_count"] > 0
    delta, chunk, validation = compile_authoring(authoring, task)
    assert validation["passed"] is True
    assert delta["delta_sha256"]
    assert chunk["chunk_sha256"]
