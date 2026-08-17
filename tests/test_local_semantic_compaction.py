from __future__ import annotations

import json

import scripts.local_semantic_compaction as compaction
from scripts.build_compact_semantic_tasks import load_json
from scripts.local_semantic_compaction import (
    DEFAULT_TASK_DIR,
    build_candidate_view,
    build_patch_prompt,
    clean_plan_for_patch,
    merge_plan_patch,
    normalize_plan,
    process_task,
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


def test_plan_validation_rejects_duplicate_clause_ownership() -> None:
    candidate = _fixture_candidate()
    plan = _covering_plan(candidate)
    duplicate = plan["groups"][0]["clauses"][0]
    plan["examples"].append(duplicate)

    report = validate_plan(plan, candidate)

    assert report["passed"] is False
    assert report["multiply_grounded_clause_count"] == 1
    assert any("assigned more than once" in error for error in report["errors"])


def test_patch_cleanup_and_merge_preserve_valid_semantics() -> None:
    candidate = _fixture_candidate()
    plan = _covering_plan(candidate)
    target = plan["groups"][0]["clauses"].pop()
    plan["groups"].append(
        {
            "id": "invalid-empty-semantics",
            "kind": "rule",
            "force": "required",
            "clauses": [target],
            "semantics": {},
        }
    )

    base, targets = clean_plan_for_patch(plan, candidate)
    prompt = build_patch_prompt(candidate, base, targets, ["fixture failure"])
    patch = {
        "task_id": candidate["task_id"],
        "groups": [],
        "exceptions": [],
        "examples": [target],
    }
    merged, normalization, scope_errors = merge_plan_patch(
        base, patch, candidate, targets
    )

    assert targets == [target]
    assert '"target_clause_indexes":[' + str(target) + "]" in prompt
    assert scope_errors == []
    assert normalization["patch_target_clause_count"] == 1
    assert validate_plan(merged, candidate)["passed"] is True


def test_patch_merge_rejects_non_target_clause_indexes() -> None:
    candidate = _fixture_candidate()
    plan = _covering_plan(candidate)
    target = plan["groups"][0]["clauses"].pop()
    base, targets = clean_plan_for_patch(plan, candidate)
    patch = {
        "task_id": candidate["task_id"],
        "groups": [],
        "exceptions": [],
        "examples": [target, target + 1],
    }

    _, _, scope_errors = merge_plan_patch(base, patch, candidate, targets)

    assert scope_errors == [f"patch contains non-target clause indexes: [{target + 1}]"]


def test_process_task_migrates_duplicate_cache_without_model_call(
    tmp_path, monkeypatch
) -> None:
    candidate = _fixture_candidate()
    plan = _covering_plan(candidate)
    plan["examples"].append(plan["groups"][0]["clauses"][0])
    (tmp_path / "plans").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "plans" / "P-100-part-001.json").write_text(
        json.dumps(plan), encoding="utf-8"
    )
    (tmp_path / "reports" / "P-100-part-001.json").write_text(
        json.dumps({"candidate_sha256": compaction._sha256(candidate)}),
        encoding="utf-8",
    )

    def fail_request(**_kwargs):
        raise AssertionError("deterministic cleanup should not call the model")

    monkeypatch.setattr(compaction, "_request_model", fail_request)
    report = process_task(
        DEFAULT_TASK_DIR / "P-100-part-001.json",
        authoring_dir=(
            DEFAULT_TASK_DIR.parents[1] / "data" / "bluebook_v3" / "semantic_authoring"
        ),
        reference_dir=None,
        output_dir=tmp_path,
        model="fixture",
        backend="openai",
        endpoint="http://127.0.0.1:1",
        context_tokens=49152,
        output_tokens=8192,
        timeout=1,
        repair_attempts=1,
        seed=1,
        dry_run=False,
        force=False,
    )

    assert report["validation"]["passed"] is True
    assert report["attempts"][0]["mode"] == "deterministic_cache_cleanup"
    migrated = load_json(tmp_path / "plans" / "P-100-part-001.json")
    assert validate_plan(migrated, candidate)["passed"] is True


def test_process_task_accepts_deterministically_cleaned_patch(
    tmp_path, monkeypatch
) -> None:
    candidate = _fixture_candidate()
    plan = _covering_plan(candidate)
    target = plan["groups"][0]["clauses"].pop()
    (tmp_path / "plans").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "plans" / "P-100-part-001.json").write_text(
        json.dumps(plan), encoding="utf-8"
    )
    (tmp_path / "reports" / "P-100-part-001.json").write_text(
        json.dumps({"candidate_sha256": compaction._sha256(candidate)}),
        encoding="utf-8",
    )
    calls = 0

    def redundant_patch(**_kwargs):
        nonlocal calls
        calls += 1
        return (
            {
                "task_id": candidate["task_id"],
                "groups": [],
                "exceptions": [],
                "examples": [target, target + 1],
            },
            {"fixture": True},
        )

    monkeypatch.setattr(compaction, "_request_model", redundant_patch)
    report = process_task(
        DEFAULT_TASK_DIR / "P-100-part-001.json",
        authoring_dir=(
            DEFAULT_TASK_DIR.parents[1] / "data" / "bluebook_v3" / "semantic_authoring"
        ),
        reference_dir=None,
        output_dir=tmp_path,
        model="fixture",
        backend="openai",
        endpoint="http://127.0.0.1:1",
        context_tokens=49152,
        output_tokens=8192,
        timeout=1,
        repair_attempts=2,
        seed=1,
        dry_run=False,
        force=False,
    )

    assert calls == 1
    assert report["validation"]["passed"] is True
    assert report["attempts"][-1]["mode"] == "deterministic_post_patch_cleanup"


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
