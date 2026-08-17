from __future__ import annotations

from jsonschema import Draft202012Validator

from scripts.build_compact_semantic_tasks import load_json
from scripts.local_semantic_authoring import DEFAULT_BOOTSTRAP_DIR, DEFAULT_EXAMPLE
from scripts.local_semantic_authoring_chunked import (
    assemble_patches,
    deduplicate_patch_ids,
    focused_candidate,
    localize_compile_errors,
    normalize_mechanical_decisions,
    normalize_mechanical_ownership,
    normalize_bootstrap_ownership,
    normalize_clause_metadata,
    normalize_partition_ownership,
    normalize_record_ownership,
    normalize_example_references,
    normalize_example_names,
    normalize_compact_identifiers,
    normalize_reason_codes,
    normalize_table_references,
    partition_indexes,
    response_schema,
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
    assert len(validation["errors"]) >= 2


def test_patch_validation_rejects_short_clause_decisions() -> None:
    _task, authoring, _candidate = _fixture()
    patch = {
        "task_id": authoring["task_id"],
        "clauses": [{"i": 1, "decision": ["normative", "compile"]}],
        "symbols": [],
        "units": [],
        "exceptions": [],
        "examples": [],
    }

    validation = validate_patch(patch, authoring["task_id"], [1])

    assert validation["passed"] is False
    assert "3-5 item array" in validation["errors"][0]


def test_duplicate_ids_are_namespaced_with_local_references() -> None:
    base = {
        "task_id": "P-1-part-001",
        "clauses": [],
        "symbols": [],
        "units": [{"id": "same", "c": [1]}],
        "exceptions": [],
        "examples": [],
    }
    later = {
        "task_id": "P-1-part-001",
        "clauses": [],
        "symbols": [],
        "units": [{"id": "same", "c": [2]}],
        "exceptions": [
            {
                "id": "exception",
                "c": [2],
                "target": ["semantic_unit", "same"],
            }
        ],
        "examples": [],
    }

    normalized = deduplicate_patch_ids([base, later])

    assert normalized[0]["units"][0]["id"] == "same"
    assert normalized[1]["units"][0]["id"] == "same_partition_002"
    assert normalized[1]["exceptions"][0]["target"] == [
        "semantic_unit",
        "same_partition_002",
    ]


def test_duplicate_mapping_unit_does_not_rename_table_reference() -> None:
    base = {
        "symbols": [],
        "units": [{"id": "shared", "k": "mapping", "table": "shared"}],
        "exceptions": [],
        "examples": [],
    }
    later = {
        "symbols": [],
        "units": [{"id": "shared", "k": "mapping", "table": "shared"}],
        "exceptions": [],
        "examples": [],
    }

    normalized = deduplicate_patch_ids([base, later])

    assert normalized[1]["units"][0]["id"] == "shared_partition_002"
    assert normalized[1]["units"][0]["table"] == "shared"


def test_patch_validation_rejects_units_missing_kind_fields() -> None:
    _task, authoring, _candidate = _fixture()
    patch = {
        "task_id": authoring["task_id"],
        "clauses": [{"i": 1, "decision": ["procedure_step", "normative", "compile"]}],
        "symbols": [],
        "units": [{"id": "incomplete", "k": "procedure", "c": [1]}],
        "exceptions": [],
        "examples": [],
    }

    validation = validate_patch(patch, authoring["task_id"], [1])

    assert validation["passed"] is False
    assert any(
        "missing required fields ['steps']" in error
        for error in validation["errors"]
    )


def test_response_schema_accepts_complete_gold_units_and_rejects_kind_only() -> None:
    _task, authoring, _candidate = _fixture()
    schema = response_schema(1)
    validator = Draft202012Validator(schema)
    complete = {
        "task_id": authoring["task_id"],
        "clauses": [{"i": 1, "decision": authoring["clauses"][0]}],
        "symbols": [],
        "units": [authoring["units"][0]],
        "exceptions": [],
        "examples": [],
    }
    incomplete = {
        **complete,
        "units": [{"k": "procedure"}],
    }

    assert list(validator.iter_errors(complete)) == []
    assert list(validator.iter_errors(incomplete))


def test_strict_patch_validation_rejects_unknown_statement_opcode() -> None:
    task, authoring, _candidate = _fixture()
    patch = {
        "task_id": authoring["task_id"],
        "clauses": [
            {"i": 2, "decision": ["procedure_step", "normative", "compile"]}
        ],
        "symbols": [],
        "units": [
            {
                "id": "bad_apply",
                "k": "procedure",
                "c": [2],
                "steps": [["apply", "something"]],
            }
        ],
        "exceptions": [],
        "examples": [],
    }

    validation = validate_patch(
        patch,
        authoring["task_id"],
        [2],
        task=task,
        bootstrap=authoring,
    )

    assert validation["passed"] is False
    assert any("response schema" in error for error in validation["errors"])


def test_response_schema_rejects_malformed_nested_compact_operations() -> None:
    _task, authoring, _candidate = _fixture()
    schema = response_schema(1)
    validator = Draft202012Validator(schema)
    patch = {
        "task_id": authoring["task_id"],
        "clauses": [{"i": 1, "decision": authoring["clauses"][0]}],
        "symbols": [],
        "units": [
            {
                "id": "bad_literal",
                "k": "rule",
                "c": [1],
                "if": ["lit", True, False],
                "then": [["emit", ["lit", "value"]]],
            }
        ],
        "exceptions": [],
        "examples": [],
    }

    assert list(validator.iter_errors(patch))

    patch["units"][0]["if"] = ["lit", True]
    patch["units"][0]["then"] = [["apply", "something"]]
    assert list(validator.iter_errors(patch))


def test_response_schema_rejects_malformed_exception_reference() -> None:
    _task, authoring, _candidate = _fixture()
    schema = response_schema(1)
    validator = Draft202012Validator(schema)
    patch = {
        "task_id": authoring["task_id"],
        "clauses": [{"i": 1, "decision": authoring["clauses"][0]}],
        "symbols": [],
        "units": [],
        "exceptions": [
            {
                "id": "bad_target",
                "c": [1],
                "if": ["lit", True],
                "target": [1],
                "mode": "suppress",
                "order": 1,
            }
        ],
        "examples": [],
    }

    assert list(validator.iter_errors(patch))
    patch["exceptions"][0]["target"] = ["clause", 1]
    assert list(validator.iter_errors(patch)) == []


def test_patch_validation_enforces_mechanical_clause_null() -> None:
    task, authoring, _candidate = _fixture()
    assert authoring["clauses"][0] is None
    patch = {
        "task_id": authoring["task_id"],
        "clauses": [{"i": 1, "decision": ["figure_asset", "illustrative", "compile"]}],
        "symbols": [],
        "units": [],
        "exceptions": [],
        "examples": [],
    }

    validation = validate_patch(
        patch,
        authoring["task_id"],
        [1],
        task=task,
        bootstrap=authoring,
    )

    assert validation["passed"] is False
    assert "mechanically proven clause 1 decision must be null" in validation["errors"]

    normalized, changed = normalize_mechanical_decisions(patch, authoring)
    assert normalized["clauses"][0]["decision"] is None
    assert changed == [1]
    assert patch["clauses"][0]["decision"] is not None


def test_mechanical_ownership_is_removed_and_empty_objects_are_dropped() -> None:
    _task, authoring, _candidate = _fixture()
    patch = {
        "units": [
            {"id": "mixed", "c": [1, 2]},
            {"id": "mechanical_only", "c": [1]},
        ],
        "exceptions": [],
        "examples": [],
    }

    normalized, changes = normalize_mechanical_ownership(patch, authoring)

    assert normalized["units"] == [{"id": "mixed", "c": [2]}]
    assert [change["id"] for change in changes] == ["mixed", "mechanical_only"]
    assert changes[1]["dropped"] is True


def test_patch_validation_rejects_compiled_clause_without_owner() -> None:
    task, authoring, _candidate = _fixture()
    patch = {
        "task_id": authoring["task_id"],
        "clauses": [
            {"i": 2, "decision": ["definition", "informative", "compile"]}
        ],
        "symbols": [],
        "units": [],
        "exceptions": [],
        "examples": [],
    }

    validation = validate_patch(
        patch,
        authoring["task_id"],
        [2],
        task=task,
        bootstrap=authoring,
    )

    assert validation["passed"] is False
    assert "compiled clause 2 has no semantic owner" in validation["errors"]


def test_bootstrap_ownership_restores_source_example() -> None:
    authoring = load_json(DEFAULT_BOOTSTRAP_DIR / "P-101-part-002.json")
    example_index = 217
    patch = {
        "clauses": [
            {"i": example_index, "decision": ["example", "illustrative", "compile"]}
        ],
        "units": [],
        "exceptions": [],
        "examples": [],
    }

    normalized, changes = normalize_bootstrap_ownership(patch, authoring)

    assert normalized["examples"][0]["c"] == [example_index]
    assert normalized["examples"][0]["shows"] == []
    assert changes[0]["action"] == "restore_source_example"


def test_invalid_clause_metadata_is_restored_from_bootstrap() -> None:
    _task, authoring, _candidate = _fixture()
    patch = {
        "clauses": [{"i": 2, "decision": ["compile", "clause-id", "compile"]}]
    }

    normalized, changes = normalize_clause_metadata(patch, authoring)

    assert normalized["clauses"][0]["decision"][:2] == authoring["clauses"][1][:2]
    assert changes[0]["clause_index"] == 2


def test_invalid_example_reference_is_removed() -> None:
    patch = {
        "examples": [
            {
                "id": "example",
                "shows": [
                    ["render", "not-an-object"],
                    ["semantic_unit", "valid-unit"],
                ],
            }
        ]
    }

    normalized, changes = normalize_example_references(patch)

    assert normalized["examples"][0]["shows"] == [
        ["semantic_unit", "valid-unit"]
    ]
    assert changes == [{"example_id": "example", "removed_count": 1}]


def test_example_reference_ids_are_coerced_to_compiler_types() -> None:
    patch = {
        "examples": [
            {
                "id": "example",
                "shows": [["record", 194], ["clause", "42"]],
            }
        ]
    }

    normalized, changes = normalize_example_references(patch)

    assert normalized["examples"][0]["shows"] == [
        ["record", "194"],
        ["clause", 42],
    ]
    assert changes[0]["normalized_count"] == 2


def test_unknown_record_reference_is_removed_with_task_context() -> None:
    patch = {
        "examples": [
            {
                "id": "example",
                "shows": [["record", "invented"], ["record", "P-1.1"]],
            }
        ]
    }
    task = {"rules": [{"rule_id": "P-1.1"}]}

    normalized, changes = normalize_example_references(patch, task=task)

    assert normalized["examples"][0]["shows"] == [["record", "P-1.1"]]
    assert changes[0]["removed_count"] == 1


def test_placeholder_statement_reference_is_removed() -> None:
    patch = {
        "examples": [
            {
                "id": "example",
                "shows": [
                    ["statement", "render"],
                    ["statement", "stmt.p1.rule.then.1"],
                ],
            }
        ]
    }

    normalized, changes = normalize_example_references(patch)

    assert normalized["examples"][0]["shows"] == [
        ["statement", "stmt.p1.rule.then.1"]
    ]
    assert changes[0]["removed_count"] == 1


def test_source_table_reference_is_rebound_by_rule_label() -> None:
    authoring = load_json(DEFAULT_BOOTSTRAP_DIR / "P-101-part-002.json")
    table = authoring["tables"][0]
    patch = {
        "examples": [
            {
                "id": "example",
                "shows": [["table", f"{table['label']}:table:0001"]],
            }
        ]
    }

    normalized, changes = normalize_example_references(patch, authoring)

    assert normalized["examples"][0]["shows"] == [["table", table["id"]]]
    assert changes[0]["normalized_count"] == 1


def test_literal_wrapped_example_name_is_unwrapped() -> None:
    patch = {
        "examples": [
            {"id": "example", "ok": [["lit", "ethane"]], "bad": []}
        ]
    }

    normalized, changes = normalize_example_names(patch)

    assert normalized["examples"][0]["ok"] == ["ethane"]
    assert changes[0]["unwrapped_count"] == 1


def test_statement_reason_code_is_normalized_recursively() -> None:
    patch = {
        "clauses": [],
        "units": [
            {
                "id": "rule",
                "then": [
                    ["if", ["lit", True], [["reject", "name", "Bad name"]], []]
                ],
            }
        ],
    }

    normalized, changes = normalize_reason_codes(patch)

    assert normalized["units"][0]["then"][0][2][0][2] == "bad_name"
    assert changes[0]["owner_id"] == "rule"


def test_invalid_skip_reason_restores_bootstrap_disposition() -> None:
    patch = {
        "clauses": [
            {"i": 1, "decision": ["heading", "informative", "skip", "P-103.2"]}
        ],
        "units": [],
    }
    bootstrap = {"clauses": [["heading", "informative", "compile"]]}

    normalized, changes = normalize_reason_codes(patch, bootstrap)

    assert normalized["clauses"][0]["decision"] == bootstrap["clauses"][0]
    assert changes[0]["action"] == "restore_bootstrap_disposition"


def test_compact_get_path_and_scope_are_normalized() -> None:
    patch = {
        "units": [
            {
                "id": "rule",
                "scope": {"r": ["P-101"]},
                "if": ["get", ["var", "candidate"], "CIP priority"],
            }
        ]
    }

    normalized, changes = normalize_compact_identifiers(patch)

    assert normalized["units"][0]["scope"]["r"] == ["class_specific"]
    assert normalized["units"][0]["if"][2] == "cip_priority"
    assert len(changes) == 2


def test_compact_literal_list_comparison_becomes_membership() -> None:
    patch = {
        "units": [
            {
                "id": "rule",
                "if": [
                    "cmp",
                    "eq",
                    ["var", "suffix"],
                    [["lit", "ane"], ["lit", "ene"]],
                ],
            }
        ]
    }

    normalized, changes = normalize_compact_identifiers(patch)

    assert normalized["units"][0]["if"] == [
        "cmp",
        "member_of",
        ["var", "suffix"],
        ["lit", ["ane", "ene"]],
    ]
    assert changes[-1]["field"] == "compare_literal_list"


def test_compact_literal_expression_list_is_collapsed() -> None:
    patch = {
        "units": [
            {
                "id": "procedure",
                "steps": [
                    ["set", "values", [["lit", {"name": "a"}], ["lit", {"name": "b"}]]]
                ],
            }
        ]
    }

    normalized, changes = normalize_compact_identifiers(patch)

    assert normalized["units"][0]["steps"][0][2] == [
        "lit",
        [{"name": "a"}, {"name": "b"}],
    ]
    assert changes[-1]["field"] == "literal_list"


def test_empty_conditional_statement_is_removed() -> None:
    patch = {
        "units": [
            {
                "id": "procedure",
                "steps": [
                    ["set", "value", ["lit", 1]],
                    ["if", ["lit", True], [], []],
                ],
            }
        ]
    }

    normalized, changes = normalize_compact_identifiers(patch)

    assert normalized["units"][0]["steps"] == [["set", "value", ["lit", 1]]]
    assert changes[-1]["action"] == "remove_noop_if"


def test_nested_symbol_grounding_is_reduced_to_primitive_id() -> None:
    patch = {
        "symbols": [
            {
                "id": "lookup_name",
                "g": ["primitive", [], {"id": "lookup_name", "k": "function"}],
            }
        ],
        "units": [],
    }

    normalized, changes = normalize_compact_identifiers(patch)

    assert normalized["symbols"][0]["g"] == ["primitive", [], "lookup_name"]
    assert changes[-1]["field"] == "grounding.primitive"


def test_partition_ownership_removes_neighbor_clause_indexes() -> None:
    patch = {
        "units": [{"id": "keep", "c": [42, 49]}],
        "exceptions": [{"id": "drop", "c": [49]}],
        "examples": [],
    }

    normalized, changes = normalize_partition_ownership(patch, range(25, 49))

    assert normalized["units"][0]["c"] == [42]
    assert normalized["exceptions"] == []
    assert len(changes) == 2


def test_empty_supersession_restores_bootstrap_disposition() -> None:
    patch = {
        "clauses": [
            {
                "i": 1,
                "decision": ["correction_event", "correction", "supersede", [], [2]],
            }
        ]
    }
    bootstrap = {"clauses": [["correction_event", "correction", "compile"]]}

    normalized, changes = normalize_mechanical_decisions(patch, bootstrap)

    assert normalized["clauses"][0]["decision"] == bootstrap["clauses"][0]
    assert changes == [1]


def test_record_ownership_splits_cross_record_objects() -> None:
    task = {
        "rules": [
            {"rule_id": "P-1.1", "source_units": [{}, {}]},
            {"rule_id": "P-1.2", "source_units": [{}, {}]},
        ]
    }
    patch = {
        "units": [{"id": "shared", "c": [2, 3, 4]}],
        "exceptions": [],
        "examples": [],
    }

    normalized, changes = normalize_record_ownership(patch, task)

    assert [item["c"] for item in normalized["units"]] == [[2], [3, 4]]
    assert normalized["units"][1]["id"] == "shared_record_P-1.2"
    assert changes[0]["record_ids"] == ["P-1.1", "P-1.2"]


def test_clause_disposition_error_is_localized_by_source_index() -> None:
    report = {
        "errors": [
            {
                "code": "disposition.normative_nonoperative",
                "path": "/clause_dispositions/24/disposition",
            }
        ]
    }

    localized = localize_compile_errors(
        {"units": [], "exceptions": [], "examples": []},
        report,
        [list(range(1, 25)), list(range(25, 49))],
    )

    assert localized == {2: report["errors"]}


def test_mapping_table_alias_is_rebound_by_clause_overlap() -> None:
    authoring = load_json(DEFAULT_BOOTSTRAP_DIR / "P-101-part-002.json")
    patch = {
        "units": [
            {"id": "mapping", "k": "mapping", "c": [32], "table": "invented"}
        ]
    }

    normalized, changes = normalize_table_references(patch, authoring)

    assert normalized["units"][0]["table"] == authoring["tables"][0]["id"]
    assert changes[0]["field"] == "table"


def test_unknown_source_table_column_is_rebound_to_text() -> None:
    authoring = load_json(DEFAULT_BOOTSTRAP_DIR / "P-101-part-002.json")
    table = authoring["tables"][0]
    patch = {
        "symbols": [],
        "units": [
            {
                "id": "lookup",
                "k": "procedure",
                "c": [table["c"][0]],
                "steps": [
                    ["set", "value", ["lookup", table["id"], ["lit", 1], "D"]]
                ],
            }
        ],
    }

    normalized, changes = normalize_table_references(patch, authoring)

    assert normalized["units"][0]["steps"][0][2][3] == "text"
    assert changes[0]["field"] == "lookup_column"


def test_noncustom_decision_comparator_drops_spurious_references() -> None:
    patch = {
        "symbols": [],
        "units": [
            {
                "id": "preference",
                "k": "decision",
                "c": [1],
                "stages": [
                    {
                        "key": ["var", "candidate"],
                        "cmp": ["numeric", "maximum", "source_order", "invented"],
                    }
                ],
            }
        ],
    }
    authoring = {"tables": []}

    normalized, changes = normalize_table_references(patch, authoring)

    assert normalized["units"][0]["stages"][0]["cmp"] == [
        "numeric",
        "maximum",
        None,
        None,
    ]
    assert changes[0]["field"] == "decision_comparator"


def test_unretained_lookup_is_lowered_to_grounded_function() -> None:
    authoring = load_json(DEFAULT_BOOTSTRAP_DIR / "P-101-part-002.json")
    patch = {
        "symbols": [],
        "units": [
            {
                "id": "rule",
                "k": "rule",
                "c": [2],
                "if": ["lookup", "missing", ["var", "key"], "name"],
            }
        ],
    }

    normalized, changes = normalize_table_references(patch, authoring)

    assert normalized["units"][0]["if"][0] == "call"
    assert normalized["symbols"][0]["id"] == "lookup_missing"
    assert changes[0]["action"] == "lower_to_grounded_function"
