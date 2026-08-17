from __future__ import annotations

from scripts.build_compact_semantic_tasks import load_json
from scripts.local_semantic_compaction import (
    DEFAULT_TASK_DIR,
    build_candidate_view,
    normalize_plan,
    validate_plan,
)


def _fixture_candidate() -> dict:
    task = load_json(DEFAULT_TASK_DIR / "P-100-part-001.json")
    authoring = load_json(
        DEFAULT_TASK_DIR.parents[1]
        / "data"
        / "bluebook_v3"
        / "semantic_authoring"
        / "P-100-part-001.json"
    )
    return build_candidate_view(task, authoring)


def _covering_plan(candidate: dict) -> dict:
    return {
        "task_id": candidate["task_id"],
        "groups": [
            {
                "id": f"{rule['rule_id']}.definition",
                "kind": "definition",
                "force": "definition",
                "clauses": [clause["i"] for clause in rule["clauses"]],
                "semantics": {
                    "term": rule["rule_id"],
                    "entity_type": "NomenclatureRule",
                    "value": {"coverage_fixture": True},
                },
            }
            for rule in candidate["rules"]
        ],
        "exceptions": [],
        "examples": [],
        "dependencies": [
            {
                "source_rule_id": rule["rule_id"],
                "target_rule_id": reference["target"],
                "relation": "cites",
                "clauses": [reference["i"]],
                "occurrence_ids": [reference["occurrence_id"]],
            }
            for rule in candidate["rules"]
            for reference in rule["references"]
        ],
    }


def test_candidate_view_preserves_source_order_and_draft_decisions() -> None:
    candidate = _fixture_candidate()

    assert candidate["task_id"] == "P-100-part-001"
    assert candidate["rules"]
    indexes = [
        clause["i"] for rule in candidate["rules"] for clause in rule["clauses"]
    ]
    assert indexes == list(range(1, len(indexes) + 1))
    assert any("draft" in clause for rule in candidate["rules"] for clause in rule["clauses"])


def test_plan_validation_requires_exact_clause_coverage() -> None:
    candidate = _fixture_candidate()
    plan = _covering_plan(candidate)

    assert validate_plan(plan, candidate)["passed"] is True

    plan["groups"][0]["clauses"].pop()
    report = validate_plan(plan, candidate)
    assert report["passed"] is False
    assert any("missing clause indexes" in error for error in report["errors"])


def test_plan_normalization_rebuilds_exact_source_dependencies() -> None:
    candidate = _fixture_candidate()
    plan = _covering_plan(candidate)
    plan["dependencies"] = [
        {
            "source_rule_id": "invented",
            "target_rule_id": "invented",
            "relation": "invokes",
            "clauses": [1],
            "occurrence_ids": ["invented"],
        }
    ]

    normalized, report = normalize_plan(plan, candidate)

    expected_count = sum(len(rule["references"]) for rule in candidate["rules"])
    assert len(normalized["dependencies"]) == expected_count
    assert report == {
        "mechanically_rebuilt_dependency_count": expected_count,
        "discarded_generated_dependency_count": 1,
    }
    assert validate_plan(normalized, candidate)["passed"] is True
