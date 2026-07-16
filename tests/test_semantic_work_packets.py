from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts import document_node_store
from scripts.build_semantic_work_packets import (
    build_packets,
    canonical_json_bytes,
    packet_digest,
    validate_clause_inventory,
    validate_reference_occurrences,
    validate_reference_resolutions,
)


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "bluebook_v3"


def load_with_hash(name: str) -> tuple[dict, str]:
    raw = (BASE / name).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest().upper()


@lru_cache(maxsize=1)
def packet_inputs() -> tuple[
    dict, dict, dict, dict, dict, dict, str, str, str, str, str, str
]:
    source, source_hash = load_with_hash("bluebook_v3_source_corpus.json")
    nodes = document_node_store.load_document_nodes()
    nodes_hash = document_node_store.hash_document_nodes()
    corrections, corrections_hash = load_with_hash("bluebook_v3_correction_overlays.json")
    clause_inventory, clause_inventory_hash = load_with_hash(
        "bluebook_v3_clause_inventory.json"
    )
    reference_occurrences, reference_occurrences_hash = load_with_hash(
        "bluebook_v3_reference_occurrences.json"
    )
    reference_resolutions, reference_resolutions_hash = load_with_hash(
        "bluebook_v3_reference_resolutions.json"
    )
    return (
        source,
        nodes,
        corrections,
        clause_inventory,
        reference_occurrences,
        reference_resolutions,
        source_hash,
        nodes_hash,
        corrections_hash,
        clause_inventory_hash,
        reference_occurrences_hash,
        reference_resolutions_hash,
    )


def build_from_inputs() -> tuple[list[dict], dict]:
    return build_packets(*packet_inputs())


@lru_cache(maxsize=1)
def packets_and_manifest() -> tuple[list[dict], dict]:
    return build_from_inputs()


def without_digest(value: dict, field: str) -> dict:
    result = dict(value)
    result.pop(field)
    return result


def test_packets_cover_every_rule_once_in_source_order() -> None:
    (
        source,
        _,
        _,
        clause_inventory,
        reference_occurrences,
        reference_resolutions,
        *input_hashes,
    ) = packet_inputs()
    packets, manifest = packets_and_manifest()
    assigned = [rule_id for packet in packets for rule_id in packet["assigned_rule_ids"]]
    assigned_inventory = [
        assignment["clause_inventory_record"]["source_rule_id"]
        for packet in packets
        for assignment in packet["assigned"]
    ]
    source_rule_ids = [record["source_rule_id"] for record in source["records"]]
    inventory_rule_ids = [
        record["source_rule_id"] for record in clause_inventory["records"]
    ]
    assigned_occurrence_ids = [
        occurrence["occurrence_id"]
        for packet in packets
        for assignment in packet["assigned"]
        for occurrence in assignment["reference_occurrences"]
    ]
    assigned_occurrences = [
        occurrence
        for packet in packets
        for assignment in packet["assigned"]
        for occurrence in assignment["reference_occurrences"]
    ]
    corpus_occurrence_ids = [
        occurrence["occurrence_id"]
        for occurrence in reference_occurrences["occurrences"]
    ]
    assigned_resolutions = [
        resolution
        for packet in packets
        for assignment in packet["assigned"]
        for resolution in assignment["reference_resolutions"]
    ]

    assert assigned == assigned_inventory == inventory_rule_ids == source_rule_ids
    assert len(assigned) == len(set(assigned)) == 2554
    assert manifest["assigned_rule_count"] == 2554
    assert manifest["packet_count"] == len(packets)
    assert assigned_occurrences == reference_occurrences["occurrences"]
    assert assigned_occurrence_ids == corpus_occurrence_ids
    assert len(assigned_occurrence_ids) == len(set(assigned_occurrence_ids)) == 4_023
    assert assigned_resolutions == reference_resolutions["records"]
    assert len(assigned_resolutions) == 3
    assert manifest["assigned_reference_occurrence_count"] == 4_023
    assert manifest["assigned_reference_resolution_count"] == 3
    assert manifest["semantically_unresolved_relation_target_count"] == 0
    assert all(1 <= len(packet["assigned_rule_ids"]) <= 24 for packet in packets)
    clause_inventory_hash = input_hashes[-3]
    reference_occurrences_hash = input_hashes[-2]
    reference_resolutions_hash = input_hashes[-1]
    assert manifest["clause_inventory_sha256"] == clause_inventory_hash
    assert manifest["reference_occurrences_sha256"] == reference_occurrences_hash
    assert manifest["reference_resolutions_sha256"] == reference_resolutions_hash
    assert all(
        packet["clause_inventory_sha256"] == clause_inventory_hash for packet in packets
    )
    assert all(
        packet["reference_occurrences_sha256"] == reference_occurrences_hash
        for packet in packets
    )
    assert all(
        packet["reference_resolutions_sha256"] == reference_resolutions_hash
        for packet in packets
    )


def test_packets_are_schema_valid_and_self_hashed() -> None:
    packets, manifest = packets_and_manifest()
    schema = json.loads(
        (ROOT / "data" / "bluebook_semantic_work_packet.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for packet in packets:
        assert list(validator.iter_errors(packet)) == []
        assert packet["packet_sha256"] == packet_digest(
            without_digest(packet, "packet_sha256")
        )
    assert manifest["manifest_sha256"] == packet_digest(
        without_digest(manifest, "manifest_sha256")
    )


def test_assigned_records_are_exact_source_document_and_clause_objects() -> None:
    source, nodes, _, clause_inventory, _, _, *_ = packet_inputs()
    records = {record["source_rule_id"]: record for record in source["records"]}
    fragments = {
        fragment["rule_id"]: fragment
        for document in nodes["documents"]
        for fragment in document["fragments"]
    }
    clause_records = {
        record["source_rule_id"]: record for record in clause_inventory["records"]
    }
    packets, _ = packets_and_manifest()
    for assignment in (
        assignment for packet in packets for assignment in packet["assigned"]
    ):
        rule_id = assignment["source_rule_id"]
        assert assignment["source_record"] == records[rule_id]
        assert assignment["document_fragment"] == fragments[rule_id]
        assert assignment["clause_inventory_record"] == clause_records[rule_id]
        clause_record = assignment["clause_inventory_record"]
        assert clause_record["record_id"] == records[rule_id]["record_id"]
        assert clause_record["record_sha256"] == packet_digest(
            without_digest(clause_record, "record_sha256")
        )


def test_clause_inventory_validation_rejects_order_and_record_hash_tampering() -> None:
    (
        source,
        _,
        _,
        clause_inventory,
        _,
        _,
        source_hash,
        nodes_hash,
        corrections_hash,
        clause_inventory_hash,
        _,
        _,
    ) = packet_inputs()

    reordered = dict(clause_inventory)
    reordered["records"] = list(clause_inventory["records"])
    reordered["records"][0], reordered["records"][1] = (
        reordered["records"][1],
        reordered["records"][0],
    )
    with pytest.raises(ValueError, match="exact source rule order and coverage"):
        validate_clause_inventory(
            source["records"],
            reordered,
            source_hash,
            nodes_hash,
            corrections_hash,
            clause_inventory_hash,
        )

    tampered_record = dict(clause_inventory["records"][0])
    tampered_record["fragment_ordinal"] += 1
    tampered = dict(clause_inventory)
    tampered["records"] = [tampered_record, *clause_inventory["records"][1:]]
    tampered["corpus_sha256"] = packet_digest(
        without_digest(tampered, "corpus_sha256")
    )
    tampered_hash = hashlib.sha256(canonical_json_bytes(tampered)).hexdigest().upper()
    with pytest.raises(ValueError, match="record_sha256 is invalid"):
        validate_clause_inventory(
            source["records"],
            tampered,
            source_hash,
            nodes_hash,
            corrections_hash,
            tampered_hash,
        )


def test_reference_occurrence_validation_rejects_digest_and_id_tampering() -> None:
    source, _, _, _, reference_occurrences, _, *input_hashes = packet_inputs()
    reference_occurrences_hash = input_hashes[-2]

    stale_digest = deepcopy(reference_occurrences)
    stale_digest["occurrences"][0]["reference_text"] = "P-11"
    with pytest.raises(ValueError, match="SHA-256 does not match"):
        validate_reference_occurrences(
            source["records"], stale_digest, reference_occurrences_hash
        )

    stale_digest_hash = hashlib.sha256(
        canonical_json_bytes(stale_digest)
    ).hexdigest().upper()
    with pytest.raises(ValueError, match="corpus_sha256 is invalid"):
        validate_reference_occurrences(
            source["records"], stale_digest, stale_digest_hash
        )

    duplicate_id = deepcopy(reference_occurrences)
    duplicate_id["occurrences"][1]["occurrence_id"] = duplicate_id["occurrences"][0][
        "occurrence_id"
    ]
    digest_payload = {
        key: duplicate_id[key]
        for key in (
            "context_characters",
            "source_document_ids",
            "source_artifact_manifest_sha256",
            "source_artifacts",
            "counters",
            "occurrences",
        )
    }
    duplicate_id["corpus_sha256"] = packet_digest(digest_payload)
    duplicate_id_hash = hashlib.sha256(
        canonical_json_bytes(duplicate_id)
    ).hexdigest().upper()
    with pytest.raises(ValueError, match="Duplicate reference occurrence id"):
        validate_reference_occurrences(
            source["records"], duplicate_id, duplicate_id_hash
        )


def test_reference_resolution_validation_rejects_tampering_and_coverage_gaps() -> None:
    (
        source,
        _,
        corrections,
        _,
        reference_occurrences,
        reference_resolutions,
        source_hash,
        _,
        corrections_hash,
        _,
        reference_occurrences_hash,
        reference_resolutions_hash,
    ) = packet_inputs()

    def validate(value: dict, value_hash: str) -> dict[str, dict]:
        return validate_reference_resolutions(
            source["records"],
            corrections,
            reference_occurrences["occurrences"],
            value,
            source_hash,
            corrections_hash,
            reference_occurrences_hash,
            value_hash,
        )

    def rehash(value: dict) -> str:
        value["corpus_sha256"] = packet_digest(
            without_digest(value, "corpus_sha256")
        )
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()

    stale_file_hash = deepcopy(reference_resolutions)
    stale_file_hash["records"][0]["rationale_code"] = "deleted_by_official_correction"
    with pytest.raises(ValueError, match="SHA-256 does not match"):
        validate(stale_file_hash, reference_resolutions_hash)

    stale_corpus_hash = deepcopy(reference_resolutions)
    stale_corpus_hash["corpus_sha256"] = "0" * 64
    stale_corpus_file_hash = hashlib.sha256(
        canonical_json_bytes(stale_corpus_hash)
    ).hexdigest().upper()
    with pytest.raises(ValueError, match="corpus_sha256 is invalid"):
        validate(stale_corpus_hash, stale_corpus_file_hash)

    bad_occurrence_hash = deepcopy(reference_resolutions)
    bad_occurrence_hash["records"][0]["reference_occurrence_sha256"] = "0" * 64
    bad_occurrence_hash["records"][0]["record_sha256"] = packet_digest(
        without_digest(bad_occurrence_hash["records"][0], "record_sha256")
    )
    with pytest.raises(ValueError, match="Reference occurrence SHA-256 is invalid"):
        validate(bad_occurrence_hash, rehash(bad_occurrence_hash))

    bad_alias = deepcopy(reference_resolutions)
    bad_alias["records"][0]["rationale_code"] = "deleted_by_official_correction"
    bad_alias["records"][0]["record_sha256"] = packet_digest(
        without_digest(bad_alias["records"][0], "record_sha256")
    )
    with pytest.raises(ValueError, match="Reference source alias is invalid"):
        validate(bad_alias, rehash(bad_alias))

    incomplete = deepcopy(reference_resolutions)
    incomplete["records"][2]["occurrence_id"] = incomplete["records"][1][
        "occurrence_id"
    ]
    incomplete["records"][2]["record_sha256"] = packet_digest(
        without_digest(incomplete["records"][2], "record_sha256")
    )
    with pytest.raises(ValueError, match="do not exactly cover"):
        validate(incomplete, rehash(incomplete))


def test_context_preserves_hierarchy_neighbors_references_and_corrections() -> None:
    packets, _ = packets_and_manifest()
    assignments = {
        assignment["source_rule_id"]: assignment
        for packet in packets
        for assignment in packet["assigned"]
    }

    restored = assignments["P-65.1.2.1"]
    assert restored["immediate_parent"] == "P-65.1.2"
    assert "P-65.1.2" in restored["ancestor_chain"]
    assert restored["preceding_rule_ids"][-1] == "P-65.1.2"

    deleted = next(
        overlay
        for packet in packets
        for overlay in packet["correction_overlays"]
        if overlay["target"]["selector_text"].startswith("P-65.7.8.")
    )
    assert deleted["status"] == "deleted"

    cited = assignments["P-65.1.2.2"]
    assert "P-65.1.2.1" in cited["outgoing_source_references"]
    assert "P-65.1.2.2" in assignments["P-65.1.2.1"]["incoming_source_references"]


def test_occurrence_evidence_replaces_legacy_reference_deduplication() -> None:
    packets, _ = packets_and_manifest()
    assignments = {
        assignment["source_rule_id"]: assignment
        for packet in packets
        for assignment in packet["assigned"]
    }

    corrected = assignments["P-15.3.4.1.3"]
    assert corrected["outgoing_source_references"] == ["P-14.5"]
    assert [
        occurrence["target"]["rule_id"]
        for occurrence in corrected["reference_occurrences"]
    ] == ["P-14.5"]

    repeated_edge = next(
        edge
        for packet in packets
        for edge in packet["relation_edges"]
        if edge["relation"] == "source_citation"
        and edge["source"] == "P-12.1"
        and edge["target"] == "P-13.1"
    )
    assert repeated_edge["multiplicity"] == 8
    assert len(repeated_edge["occurrence_ids"]) == 8
    assert len(set(repeated_edge["occurrence_ids"])) == 8


def test_reference_resolutions_attach_exactly_and_project_effective_targets() -> None:
    _, _, _, _, reference_occurrences, reference_resolutions, *_ = packet_inputs()
    packets, manifest = packets_and_manifest()
    assignments = {
        assignment["source_rule_id"]: assignment
        for packet in packets
        for assignment in packet["assigned"]
    }
    resolution_by_occurrence = {
        record["occurrence_id"]: record
        for record in reference_resolutions["records"]
    }
    edge_by_occurrence = {
        occurrence_id: edge
        for packet in packets
        for edge in packet["relation_edges"]
        if edge["relation"] == "source_citation"
        for occurrence_id in edge["occurrence_ids"]
    }

    for occurrence_id, resolution in resolution_by_occurrence.items():
        source_rule_id = occurrence_id.split(":xref:", 1)[0]
        assert resolution in assignments[source_rule_id]["reference_resolutions"]

    for occurrence_id in (
        "P-16.2.4.1:xref:0005",
        "P-66.2.1:xref:0001",
    ):
        edge = edge_by_occurrence[occurrence_id]
        assert edge["target"] == "P-66.1.2"
        assert edge["target_kind"] == "rule"

    deleted_edge = edge_by_occurrence["P-65.7:xref:0009"]
    assert deleted_edge["target"] == "P-65.7.8"
    assert deleted_edge["target_kind"] == "historical_deleted_rule"

    unresolved_raw_ids = {
        occurrence["occurrence_id"]
        for occurrence in reference_occurrences["occurrences"]
        if occurrence["target"]["resolution"] == "unresolved"
    }
    assert unresolved_raw_ids == set(resolution_by_occurrence)
    assert unresolved_raw_ids <= set(edge_by_occurrence)
    assert manifest["semantically_unresolved_relation_target_count"] == 0
    assert "P-66.1.2.1" not in assignments["P-16.2.4.1"][
        "outgoing_source_references"
    ]
    assert "P-66.1.2" in assignments["P-16.2.4.1"][
        "outgoing_source_references"
    ]

    deleted_packet = next(
        packet
        for packet in packets
        if "P-65.7" in packet["assigned_rule_ids"]
    )
    assert any(
        overlay["overlay_id"] == "BBV3-CORR-707A0F8B4E94258D"
        for overlay in deleted_packet["correction_overlays"]
    )


def test_relation_edges_and_context_retain_occurrence_evidence() -> None:
    _, _, _, _, reference_occurrences, reference_resolutions, *_ = packet_inputs()
    packets, _ = packets_and_manifest()
    corpus_ids = {
        occurrence["occurrence_id"]
        for occurrence in reference_occurrences["occurrences"]
    }
    edge_ids = [
        occurrence_id
        for packet in packets
        for edge in packet["relation_edges"]
        if edge["relation"] == "source_citation"
        for occurrence_id in edge["occurrence_ids"]
    ]
    assert len(edge_ids) == len(set(edge_ids)) == 4_023
    assert set(edge_ids) == corpus_ids
    assert all(
        edge["multiplicity"] == len(edge["occurrence_ids"])
        for packet in packets
        for edge in packet["relation_edges"]
        if edge["relation"] == "source_citation"
    )

    resolution_by_occurrence = {
        record["occurrence_id"]: record
        for record in reference_resolutions["records"]
    }
    context_evidence_count = 0
    for packet in packets:
        assigned_ids = set(packet["assigned_rule_ids"])
        for context in packet["context_records"]:
            context_rule_id = context["source_rule_id"]
            assert context["reference_resolutions"] == [
                resolution_by_occurrence[occurrence["occurrence_id"]]
                for occurrence in context["reference_occurrences"]
                if occurrence["occurrence_id"] in resolution_by_occurrence
            ]
            for occurrence in context["reference_occurrences"]:
                context_evidence_count += 1
                effective_target = resolution_by_occurrence.get(
                    occurrence["occurrence_id"], occurrence["target"]
                )
                target_rule_id = effective_target.get(
                    "resolved_rule_id", effective_target.get("rule_id")
                )
                assert (
                    occurrence["source_rule_id"] == context_rule_id
                    and target_rule_id in assigned_ids
                ) or (
                    occurrence["source_rule_id"] in assigned_ids
                    and target_rule_id == context_rule_id
                )
    assert context_evidence_count > 0


def test_packet_generation_is_byte_deterministic() -> None:
    first_packets, first_manifest = packets_and_manifest()
    second_packets, second_manifest = build_from_inputs()
    assert canonical_json_bytes(first_packets) == canonical_json_bytes(second_packets)
    assert canonical_json_bytes(first_manifest) == canonical_json_bytes(second_manifest)
    assert [packet["packet_sha256"] for packet in first_packets] == [
        packet["packet_sha256"] for packet in second_packets
    ]
    assert first_manifest["manifest_sha256"] == second_manifest["manifest_sha256"]
