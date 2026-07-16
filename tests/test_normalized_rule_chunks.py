from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest

from scripts.validate_normalized_rule_chunks import (
    Audit,
    CHUNK_SCHEMA_PATH,
    LANGUAGE_SCHEMA_PATH,
    _expected_metrics,
    build_schema_validator,
    canonical_json_bytes,
    digest_without_field,
    language_schema_sha256,
    validate_chunk,
)


RULE_ID = "P-1.1"
PACKET_ID = "P-1-part-001"
OPERATIVE_CLAUSE = f"{RULE_ID}:clause:0001"
NONOPERATIVE_CLAUSE = f"{RULE_ID}:clause:0002"
RECORD_ID = f"bluebook-v3:{RULE_ID}"
UNIT_ID = "unit.synthetic_rule"
STATEMENT_ID = "stmt.emit_name"
REFERENCE_ID = "reference.rule_constraint"
SPECIFIC_EXCEPTION_ID = "exception.specific"
GENERAL_EXCEPTION_ID = "exception.general"


def load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def object_ref(kind: str, object_id: str) -> dict[str, str]:
    return {"kind": kind, "id": object_id}


def expression(expression_id: str, value: Any = True) -> dict[str, Any]:
    return {
        "expression_id": expression_id,
        "clause_ids": [OPERATIVE_CLAUSE],
        "op": "literal",
        "value": value,
    }


def packet_source_unit(unit_id: str, ordinal: int) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "ordinal": ordinal,
        "source_node_id": f"{RULE_ID}:node:0001",
        "node_kind": "paragraph",
        "unit_kind": "prose_text",
        "ownership": "primary",
        "source_occurrence_id": None,
        "component_path": "/nodes/0/text",
        "field_source_path": "/nodes/0/field_sources/text",
        "field_source_ids": ["field:" + "A" * 24],
        "provenance_path": "/nodes/0/source",
        "provenance_manifest_sha256": "A" * 64,
        "semantic_cue": "unspecified",
        "text_start": 0,
        "text_end": 1,
        "text": "x",
        "text_sha256": "A" * 64,
        "component_text_sha256": "A" * 64,
        "payload": None,
        "payload_sha256": None,
    }


def build_packet() -> dict[str, Any]:
    packet: dict[str, Any] = {
        "format": "iupac-bluebook-semantic-work-packet",
        "format_version": "1.0.0",
        "packet_id": PACKET_ID,
        "source_corpus_sha256": "A" * 64,
        "document_nodes_sha256": "B" * 64,
        "correction_overlays_sha256": "C" * 64,
        "clause_inventory_sha256": "D" * 64,
        "reference_occurrences_sha256": "E" * 64,
        "reference_resolutions_sha256": "F" * 64,
        "output_path": f"data/bluebook_v3/semantic_chunks/{PACKET_ID}.json",
        "assigned_rule_ids": [RULE_ID],
        "assigned": [
            {
                "source_rule_id": RULE_ID,
                "source_record": {"record_id": RECORD_ID},
                "document_fragment": {"nodes": []},
                "clause_inventory_record": {
                    "record_id": RECORD_ID,
                    "source_rule_id": RULE_ID,
                    "chapter": "P-1",
                    "document_id": "P-1",
                    "fragment_ordinal": 1,
                    "source_reference_rule_ids": [],
                    "correction_overlay_ids": [],
                    "source_units": [
                        packet_source_unit(OPERATIVE_CLAUSE, 1),
                        packet_source_unit(NONOPERATIVE_CLAUSE, 2),
                    ],
                    "node_coverage": [
                        {
                            "node_id": f"{RULE_ID}:node:0001",
                            "node_kind": "paragraph",
                            "component_path": "/nodes/0",
                            "unit_ids": [OPERATIVE_CLAUSE, NONOPERATIVE_CLAUSE],
                        }
                    ],
                    "field_source_coverage": [
                        {
                            "field_source_id": "field:" + "A" * 24,
                            "component_path": "/nodes/0/text",
                            "field_name": "text",
                            "ownership": "primary",
                            "owner_ref": None,
                            "unit_ids": [OPERATIVE_CLAUSE, NONOPERATIVE_CLAUSE],
                        }
                    ],
                    "record_sha256": "A" * 64,
                },
                "immediate_parent": "chapter:P-1",
                "ancestor_chain": ["chapter:P-1"],
                "preceding_rule_ids": [],
                "following_rule_ids": [],
                "outgoing_source_references": [],
                "incoming_source_references": [],
                "reference_occurrences": [],
                "reference_resolutions": [],
                "incoming_reference_occurrence_ids": [],
                "correction_overlay_ids": [],
            }
        ],
        "context_records": [],
        "correction_overlays": [],
        "relation_edges": [
            {
                "source": RULE_ID,
                "relation": "hierarchy_parent",
                "target": "chapter:P-1",
                "target_kind": "chapter",
            }
        ],
    }
    packet["packet_sha256"] = digest_without_field(packet, "packet_sha256")
    return packet


def build_chunk(packet: dict[str, Any]) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "format": "iupac-bluebook-normalized-rule-chunk",
        "format_version": "1.0.0",
        "packet_id": PACKET_ID,
        "packet_sha256": packet["packet_sha256"],
        "schema_sha256": language_schema_sha256(),
        "source_corpus_sha256": packet["source_corpus_sha256"],
        "document_nodes_sha256": packet["document_nodes_sha256"],
        "correction_overlays_sha256": packet["correction_overlays_sha256"],
        "clause_inventory_sha256": packet["clause_inventory_sha256"],
        "reference_occurrences_sha256": packet["reference_occurrences_sha256"],
        "reference_resolutions_sha256": packet["reference_resolutions_sha256"],
        "assigned_rule_ids": [RULE_ID],
        "symbol_declarations": [],
        "clause_dispositions": [
            {
                "clause_id": OPERATIVE_CLAUSE,
                "role": "effect",
                "force": "normative",
                "disposition": {
                    "kind": "compiled",
                    "targets": [
                        object_ref("semantic_unit", UNIT_ID),
                        object_ref("expression", "expr.when"),
                        object_ref("statement", STATEMENT_ID),
                        object_ref("exception", SPECIFIC_EXCEPTION_ID),
                        object_ref("exception", GENERAL_EXCEPTION_ID),
                        object_ref("reference", REFERENCE_ID),
                    ],
                },
            },
            {
                "clause_id": NONOPERATIVE_CLAUSE,
                "role": "note",
                "force": "informative",
                "disposition": {
                    "kind": "nonoperative",
                    "reason_code": "explanatory_note",
                },
            },
        ],
        "records": [
            {
                "record_id": RECORD_ID,
                "source_rule_id": RULE_ID,
                "chapter": "P-1",
                "clause_ids": [OPERATIVE_CLAUSE, NONOPERATIVE_CLAUSE],
                "operative": True,
                "semantic_unit_ids": [UNIT_ID],
                "exception_ids": [SPECIFIC_EXCEPTION_ID, GENERAL_EXCEPTION_ID],
                "table_ids": [],
                "figure_ids": [],
                "example_ids": [],
                "correction_application_ids": [],
                "reference_ids": [REFERENCE_ID],
            }
        ],
        "semantic_units": [
            {
                "unit_id": UNIT_ID,
                "kind": "rule",
                "force": "required",
                "clause_ids": [OPERATIVE_CLAUSE],
                "scope": {
                    "regimes": ["all"],
                    "applies_to": expression("expr.scope"),
                },
                "inputs": [],
                "outputs": [],
                "when": expression("expr.when"),
                "then": [
                    {
                        "statement_id": STATEMENT_ID,
                        "clause_ids": [OPERATIVE_CLAUSE],
                        "op": "emit",
                        "value": expression("expr.output", "synthetic-name"),
                    }
                ],
                "else": [],
            }
        ],
        "exceptions": [
            {
                "exception_id": SPECIFIC_EXCEPTION_ID,
                "clause_ids": [OPERATIVE_CLAUSE],
                "when": expression("expr.exception.specific"),
                "target": object_ref("statement", STATEMENT_ID),
                "effect": {
                    "mode": "suppress",
                    "replacement": None,
                    "guard": None,
                    "redirect": None,
                },
                "precedence": {"specificity": 2, "source_order": 2},
            },
            {
                "exception_id": GENERAL_EXCEPTION_ID,
                "clause_ids": [OPERATIVE_CLAUSE],
                "when": expression("expr.exception.general"),
                "target": object_ref("semantic_unit", UNIT_ID),
                "effect": {
                    "mode": "suppress",
                    "replacement": None,
                    "guard": None,
                    "redirect": None,
                },
                "precedence": {"specificity": 1, "source_order": 1},
            },
        ],
        "tables": [],
        "figures": [],
        "examples": [],
        "correction_applications": [],
        "references": [
            {
                "reference_id": REFERENCE_ID,
                "clause_ids": [OPERATIVE_CLAUSE],
                "relation": "constrains",
                "source": object_ref("semantic_unit", UNIT_ID),
                "target": object_ref("statement", STATEMENT_ID),
                "resolution": "exact",
                "ordered_member_refs": [],
            }
        ],
        "chunk_metrics": {},
        "chunk_sha256": "0" * 64,
    }
    stamp_chunk(chunk)
    return chunk


def stamp_chunk(chunk: dict[str, Any], *, metrics: bool = True) -> None:
    if metrics:
        chunk["chunk_metrics"] = _expected_metrics(chunk)
    chunk["chunk_sha256"] = digest_without_field(chunk, "chunk_sha256")


@pytest.fixture
def packet() -> dict[str, Any]:
    return build_packet()


@pytest.fixture
def chunk(packet: dict[str, Any]) -> dict[str, Any]:
    return build_chunk(packet)


def error_codes(report: dict[str, Any]) -> set[str]:
    return {error["code"] for error in report["errors"]}


def assert_error(report: dict[str, Any], code: str) -> None:
    assert not report["passed"]
    assert code in error_codes(report), report["errors"]


def test_valid_synthetic_chunk_passes_strict_validation(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    report = validate_chunk(chunk, packet)

    assert report == {
        "passed": True,
        "error_count": 0,
        "errors": [],
        "metrics": chunk["chunk_metrics"],
    }


def test_registered_language_schema_refs_validate_nested_objects(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    del chunk["semantic_units"][0]["scope"]
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet)

    assert_error(report, "chunk.schema")
    assert any(error["path"] == "/semantic_units/0" for error in report["errors"])


def test_schema_preflight_rejects_an_unresolvable_registered_ref() -> None:
    chunk_schema = load_schema(CHUNK_SCHEMA_PATH)
    language_schema = load_schema(LANGUAGE_SCHEMA_PATH)
    language_id = language_schema["$id"]
    chunk_schema["properties"]["records"]["items"]["$ref"] = (
        f"{language_id}#/$defs/not_a_definition"
    )
    audit = Audit()

    validator = build_schema_validator(chunk_schema, language_schema, audit)

    assert validator is None
    assert "schema.ref" in {error["code"] for error in audit.errors}


@pytest.mark.parametrize(
    "field",
    [
        "packet_id",
        "source_corpus_sha256",
        "document_nodes_sha256",
        "correction_overlays_sha256",
        "clause_inventory_sha256",
        "reference_occurrences_sha256",
        "reference_resolutions_sha256",
    ],
)
def test_source_snapshot_fields_are_pinned_to_packet(
    chunk: dict[str, Any], packet: dict[str, Any], field: str
) -> None:
    chunk[field] = "P-2-part-001" if field == "packet_id" else "0" * 64
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet)

    expected_code = "snapshot.packet_id" if field == "packet_id" else "snapshot.source_hash"
    assert_error(report, expected_code)


@pytest.mark.parametrize("mode", ["missing", "duplicate"])
def test_every_packet_clause_requires_exactly_one_disposition(
    chunk: dict[str, Any], packet: dict[str, Any], mode: str
) -> None:
    if mode == "missing":
        chunk["clause_dispositions"].pop()
    else:
        chunk["clause_dispositions"].append(
            deepcopy(chunk["clause_dispositions"][0])
        )
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet)

    assert_error(
        report,
        "coverage.disposition_missing"
        if mode == "missing"
        else "coverage.disposition_duplicate",
    )


def test_duplicate_nested_ast_id_is_not_addressable(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    chunk["semantic_units"][0]["then"][0]["value"]["expression_id"] = "expr.when"
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet)

    assert_error(report, "id.duplicate")
    assert "ast.addressability" in error_codes(report)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["clause_dispositions"][0]["disposition"]["targets"][
            1
        ].update(id="expr.missing"),
        lambda value: value["references"][0]["target"].update(id="stmt.missing"),
    ],
    ids=["compiled-target", "reference-edge"],
)
def test_dangling_typed_refs_are_rejected(
    chunk: dict[str, Any],
    packet: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    mutate(chunk)
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet)

    assert_error(report, "ref.unresolved")


def test_record_member_ids_must_resolve(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    chunk["records"][0]["reference_ids"] = ["reference.missing"]
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet)

    assert_error(report, "record.ref")
    assert "record.membership" in error_codes(report)


def edge_capable_schema() -> dict[str, Any]:
    schema = load_schema(CHUNK_SCHEMA_PATH)
    language_id = load_schema(LANGUAGE_SCHEMA_PATH)["$id"]
    schema["properties"]["dependency_edges"] = {
        "type": "array",
        "items": {"$ref": f"{language_id}#/$defs/dependency_edge"},
    }
    schema["properties"]["chunk_metrics"]["properties"][
        "dependency_edge_count"
    ] = {"type": "integer", "minimum": 0}
    return schema


def test_dependency_edge_ids_refs_and_provenance_are_checked(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    chunk["dependency_edges"] = [
        {
            "edge_id": "edge.rule_constraint",
            "from": object_ref("semantic_unit", UNIT_ID),
            "relation": "constrains",
            "to": object_ref("statement", STATEMENT_ID),
            "clause_ids": [OPERATIVE_CLAUSE],
            "derived_from_object_ids": [REFERENCE_ID, "expr.not_addressable"],
        }
    ]
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet, chunk_schema=edge_capable_schema())

    assert_error(report, "edge.provenance")


def test_dependency_edge_must_project_its_reference(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    chunk["dependency_edges"] = [
        {
            "edge_id": "edge.wrong_relation",
            "from": object_ref("semantic_unit", UNIT_ID),
            "relation": "cites",
            "to": object_ref("statement", STATEMENT_ID),
            "clause_ids": [OPERATIVE_CLAUSE],
            "derived_from_object_ids": [REFERENCE_ID],
        }
    ]
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet, chunk_schema=edge_capable_schema())

    assert_error(report, "edge.reference_projection")


def test_exception_precedence_has_one_deterministic_order(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    chunk["exceptions"].reverse()
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet)

    assert_error(report, "exception.order")


def test_packet_hash_is_recomputed_not_merely_copied(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    packet["context_records"].append({"source_rule_id": "P-1.2"})

    report = validate_chunk(chunk, packet)

    assert_error(report, "hash.packet")


def test_packet_must_match_the_strict_work_packet_schema(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    packet["output_path"] = "not-a-semantic-chunk.json"
    packet["packet_sha256"] = digest_without_field(packet, "packet_sha256")
    chunk["packet_sha256"] = packet["packet_sha256"]
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet)

    assert_error(report, "packet.schema")


def test_language_schema_hash_is_exact_file_snapshot(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    chunk["schema_sha256"] = "E" * 64
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet)

    assert_error(report, "hash.schema")


def test_chunk_hash_is_recomputed_from_canonical_content(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    chunk["chunk_sha256"] = "E" * 64

    report = validate_chunk(chunk, packet)

    assert_error(report, "hash.chunk")


def test_metrics_are_recomputed_independently_of_chunk_hash(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    chunk["chunk_metrics"]["exception_count"] += 1
    stamp_chunk(chunk, metrics=False)

    report = validate_chunk(chunk, packet)

    assert_error(report, "metrics.mismatch")
    assert "hash.chunk" not in error_codes(report)


@pytest.mark.parametrize(
    "marker",
    [
        "TODO",
        "manual_review",
        "unresolved",
        "placeholder",
        "not_started",
        "action:apply_parent_selection_rule",
        "candidate:satisfies_stated_preference_criterion",
    ],
)
def test_rehashed_schema_valid_review_markers_and_fallbacks_are_rejected(
    chunk: dict[str, Any], packet: dict[str, Any], marker: str
) -> None:
    chunk["semantic_units"][0]["then"][0]["value"]["value"] = marker
    stamp_chunk(chunk)

    report = validate_chunk(chunk, packet)

    assert_error(report, "chunk.forbidden_marker")


def test_cli_byte_mode_requires_canonical_json(
    chunk: dict[str, Any], packet: dict[str, Any]
) -> None:
    compact_chunk = json.dumps(chunk, sort_keys=True).encode("utf-8")

    report = validate_chunk(
        chunk,
        packet,
        chunk_bytes=compact_chunk,
        packet_bytes=canonical_json_bytes(packet),
    )

    assert_error(report, "json.chunk_canonical")
