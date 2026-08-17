from __future__ import annotations

from scripts.build_compact_semantic_tasks import load_json
from scripts.local_semantic_authoring import DEFAULT_BOOTSTRAP_DIR, DEFAULT_EXAMPLE
from scripts.local_semantic_authoring_chunked import (
    assemble_patches,
    focused_candidate,
    partition_indexes,
    validate_patch,
)
from scripts.local_semantic_compaction import build_candidate_view


def _fixture() -> tuple[dict, dict, dict]:
    authoring = load_json(DEFAULT_EXAMPLE)
    task = load_json(
        DEFAULT_BOOTSTRAP_DIR.parent
        / "compact_semantic_tasks"
        / f"{authoring['task_id']}.json"
    )
    candidate = build_candidate_view(task, authoring)
    return task, authoring, candidate


def test_partitions_cover_source_indexes_once_and_keep_neighbor_context() -> None:
    _task, _authoring, candidate = _fixture()

    partitions = partition_indexes(candidate, 5)
    focused = focused_candidate(candidate, partitions[1])

    assert [index for part in partitions for index in part] == list(range(1, 20))
    assert len(partitions) == 4
    assert focused["target_clause_indexes"] == [6, 7, 8, 9, 10]
    shown = [clause for rule in focused["rules"] for clause in rule["clauses"]]
    assert {clause["i"] for clause in shown}.issuperset({6, 7, 8, 9, 10})
    assert {clause["i"] for clause in shown}.difference({6, 7, 8, 9, 10})
    assert all(
        set(group["clauses"]).issubset({6, 7, 8, 9, 10})
        for group in focused["rough_groups"]
    )


def test_gold_authoring_round_trips_through_partition_merge() -> None:
    task, authoring, candidate = _fixture()
    indexes = partition_indexes(candidate, 50)
    patches = []
    for part in indexes:
        target = set(part)
        patch = {
            "task_id": authoring["task_id"],
            "clauses": [
                {"i": index, "decision": authoring["clauses"][index - 1]}
                for index in part
            ],
            "symbols": authoring["symbols"] if part == indexes[0] else [],
            "units": [
                unit
                for unit in authoring["units"]
                if set(unit["c"]).issubset(target)
            ],
            "exceptions": [
                item
                for item in authoring["exceptions"]
                if set(item["c"]).issubset(target)
            ],
            "examples": [
                item
                for item in authoring["examples"]
                if set(item["c"]).issubset(target)
            ],
        }
        assert validate_patch(patch, authoring["task_id"], part)["passed"]
        patches.append(patch)

    assembled = assemble_patches(patches, authoring)

    assert assembled["clauses"] == authoring["clauses"]
    assert assembled["units"] == authoring["units"]
    assert assembled["tables"] == authoring["tables"]


def test_patch_validation_rejects_wrong_order_and_cross_partition_units() -> None:
    _task, authoring, _candidate = _fixture()
    patch = {
        "task_id": authoring["task_id"],
        "clauses": [
            {"i": 2, "decision": authoring["clauses"][1]},
            {"i": 1, "decision": authoring["clauses"][0]},
        ],
        "symbols": [],
        "units": [{"id": "bad", "c": [1, 3]}],
        "exceptions": [],
        "examples": [],
    }

    validation = validate_patch(patch, authoring["task_id"], [1, 2])

    assert validation["passed"] is False
    assert len(validation["errors"]) == 2
