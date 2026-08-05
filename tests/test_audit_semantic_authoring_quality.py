from __future__ import annotations

from pathlib import Path

from scripts.audit_semantic_authoring_quality import quality_findings
from scripts.build_compact_semantic_tasks import load_json
from scripts.scaffold_semantic_authoring import scaffold_authoring


ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "work" / "compact_semantic_tasks"


def test_normative_language_misclassified_as_note_is_flagged() -> None:
    task = load_json(TASK_DIR / "P-53-part-001.json")
    authoring = scaffold_authoring(task)
    for index, decision in enumerate(authoring["clauses"]):
        if decision == []:
            authoring["clauses"][index] = [
                "note",
                "informative",
                "skip",
                "explanatory_note",
            ]
    authoring["clauses"][2] = [
        "history",
        "informative",
        "skip",
        "historical_context",
    ]
    authoring["clauses"][10] = [
        "cross_reference",
        "informative",
        "skip",
        "citation_only",
    ]

    findings = quality_findings(authoring, task)
    clause_ids = {item["clause_id"] for item in findings}

    assert "P-53:clause:0002" in clause_ids
    assert "P-53:clause:0005" in clause_ids
    assert "P-53:clause:0010" in clause_ids
    assert "P-53:clause:0003" not in clause_ids
    assert "P-53:clause:0011" not in clause_ids


def test_compiled_clauses_are_not_quality_findings() -> None:
    task = load_json(TASK_DIR / "P-53-part-001.json")
    authoring = scaffold_authoring(task)
    for index, decision in enumerate(authoring["clauses"]):
        if decision == []:
            authoring["clauses"][index] = ["rule", "normative", "compile"]

    assert quality_findings(authoring, task) == []
