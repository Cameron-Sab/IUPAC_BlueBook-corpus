from __future__ import annotations

from copy import deepcopy

from scripts import finalize_normalized_rule_chunk as finalizer
from scripts import validate_normalized_rule_chunks as validator


def test_finalizer_binds_packet_snapshot_metrics_and_hash_without_changing_semantics(
    monkeypatch,
) -> None:
    monkeypatch.setattr(validator, "language_schema_sha256", lambda: "E" * 64)
    packet = {
        "packet_id": "P-10-part-001",
        "packet_sha256": "A" * 64,
        "assigned_rule_ids": ["P-10"],
        **{field: "B" * 64 for field in validator.SOURCE_HASH_FIELDS},
    }
    chunk = {
        "symbol_declarations": [],
        "clause_dispositions": [
            {
                "clause_id": "P-10:clause:0001",
                "role": "heading",
                "force": "informative",
                "disposition": {
                    "kind": "nonoperative",
                    "reason_code": "heading_or_title",
                },
            }
        ],
        "records": [{"record_id": "bluebook-v3:P-10"}],
        "semantic_units": [],
        "exceptions": [],
        "tables": [],
        "figures": [],
        "examples": [],
        "correction_applications": [],
        "references": [],
    }
    semantic_payload = deepcopy(chunk)

    result = finalizer.finalize_chunk(chunk, packet)

    for key, value in semantic_payload.items():
        assert result[key] == value
    assert result["packet_id"] == packet["packet_id"]
    assert result["packet_sha256"] == packet["packet_sha256"]
    assert result["schema_sha256"] == "E" * 64
    assert result["assigned_rule_ids"] == ["P-10"]
    assert result["chunk_metrics"] == {
        "record_count": 1,
        "clause_disposition_count": 1,
        "compiled_clause_count": 0,
        "nonoperative_clause_count": 1,
        "superseded_clause_count": 0,
        "semantic_unit_count": 0,
        "exception_count": 0,
        "table_count": 0,
        "figure_count": 0,
        "example_count": 0,
        "correction_application_count": 0,
        "reference_count": 0,
        "symbol_declaration_count": 0,
    }
    assert result["chunk_sha256"] == validator.digest_without_field(
        result, "chunk_sha256"
    )
