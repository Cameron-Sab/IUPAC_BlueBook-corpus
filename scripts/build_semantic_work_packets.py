from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    from scripts.document_node_store import (
        DEFAULT_STORE as DEFAULT_DOCUMENT_NODE_STORE,
        hash_document_nodes,
        load_document_nodes,
    )
except ModuleNotFoundError:  # Support direct script execution.
    from document_node_store import (  # type: ignore[no-redef]
        DEFAULT_STORE as DEFAULT_DOCUMENT_NODE_STORE,
        hash_document_nodes,
        load_document_nodes,
    )


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "bluebook_v3"
DEFAULT_SOURCE = BASE / "bluebook_v3_source_corpus.json"
DEFAULT_DOCUMENT_NODES = DEFAULT_DOCUMENT_NODE_STORE
DEFAULT_CORRECTIONS = BASE / "bluebook_v3_correction_overlays.json"
DEFAULT_CLAUSE_INVENTORY = BASE / "bluebook_v3_clause_inventory.json"
DEFAULT_REFERENCE_OCCURRENCES = BASE / "bluebook_v3_reference_occurrences.json"
DEFAULT_REFERENCE_RESOLUTIONS = BASE / "bluebook_v3_reference_resolutions.json"
DEFAULT_OUTPUT = ROOT / "work" / "semantic_packets"
EXPECTED_RULE_COUNT = 2554
EXPECTED_REFERENCE_OCCURRENCE_COUNT = 4023
EXPECTED_REFERENCE_RESOLUTION_COUNT = 3
RULE_ID_RE = re.compile(r"^P-\d+(?:\.\d+)*(?:\([a-z0-9]+\))?$")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def immediate_parent(rule_id: str, record: dict[str, Any], active_ids: set[str]) -> str:
    candidate = rule_id.rsplit(".", 1)[0] if "." in rule_id else ""
    if candidate in active_ids:
        return candidate
    return f"chapter:{record['chapter']}"


def ancestor_chain(
    rule_id: str,
    records_by_id: dict[str, dict[str, Any]],
    active_ids: set[str],
) -> list[str]:
    chain: list[str] = []
    current = rule_id
    while True:
        parent = immediate_parent(current, records_by_id[current], active_ids)
        chain.append(parent)
        if parent.startswith("chapter:"):
            return chain
        current = parent


def major_section(rule_id: str) -> str:
    return rule_id.split(".", 1)[0]


def partition_records(
    records: list[dict[str, Any]], max_assigned_records: int
) -> list[list[dict[str, Any]]]:
    if max_assigned_records < 1:
        raise ValueError("max_assigned_records must be positive")
    groups: list[list[dict[str, Any]]] = []
    by_major: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_major = ""
    for record in records:
        major = major_section(record["source_rule_id"])
        if current and major != current_major:
            by_major.append(current)
            current = []
        current_major = major
        current.append(record)
    if current:
        by_major.append(current)
    for major_records in by_major:
        groups.extend(
            major_records[index : index + max_assigned_records]
            for index in range(0, len(major_records), max_assigned_records)
        )
    return groups


def correction_rule_targets(record: dict[str, Any]) -> set[str]:
    targets = {
        selector["rule_id"]
        for selector in record["target"]["selectors"]
        if selector["kind"] == "rule" and selector.get("relation") == "target"
    }
    targets.update(
        reference["target"]
        for reference in record.get("references", [])
        if reference.get("target_type") == "rule"
        and reference.get("relation") in {"target", "conflicts_with", "renamed_from"}
    )
    return targets


def context_summary(
    record: dict[str, Any],
    document_fragment: dict[str, Any],
    reference_occurrences: list[dict[str, Any]],
    reference_resolutions: list[dict[str, Any]],
) -> dict[str, Any]:
    heading = next(
        (node for node in document_fragment["nodes"] if node["kind"] == "heading"),
        None,
    )
    return {
        "record_id": record["record_id"],
        "source_rule_id": record["source_rule_id"],
        "chapter": record["chapter"],
        "source_kind": record["source_kind"],
        "title": heading.get("title", "") if heading else "",
        "pdf_pages": record["pdf"]["pages"],
        "html_url": record["html"]["url"],
        "html_anchor": record["html"]["anchor"],
        "alignment_kind": record["alignment"]["kind"],
        "reference_occurrences": reference_occurrences,
        "reference_resolutions": reference_resolutions,
    }


def packet_digest(packet_without_digest: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(packet_without_digest))


def validate_reference_occurrences(
    source_records: list[dict[str, Any]],
    reference_occurrences: dict[str, Any],
    reference_occurrences_sha256: str,
) -> list[dict[str, Any]]:
    if reference_occurrences.get("format") != "iupac-bluebook-reference-occurrences":
        raise ValueError("Reference occurrence corpus format is invalid")
    if reference_occurrences.get("version") != "1.0.0":
        raise ValueError("Reference occurrence corpus version is invalid")
    if (
        sha256_bytes(canonical_json_bytes(reference_occurrences))
        != reference_occurrences_sha256
    ):
        raise ValueError(
            "Reference occurrence corpus SHA-256 does not match its canonical JSON bytes"
        )

    occurrences = reference_occurrences["occurrences"]
    counter = reference_occurrences["counters"]["reference_occurrence_count"]
    if counter != len(occurrences):
        raise ValueError("Reference occurrence counter does not match its records")
    if len(occurrences) != EXPECTED_REFERENCE_OCCURRENCE_COUNT:
        raise ValueError(
            "Reference occurrence corpus must contain exactly "
            f"{EXPECTED_REFERENCE_OCCURRENCE_COUNT} records; found {len(occurrences)}"
        )
    if (
        reference_occurrences["counters"]["indexed_active_rule_fragment_count"]
        != EXPECTED_RULE_COUNT
    ):
        raise ValueError("Reference occurrence corpus does not index exactly 2554 rules")

    digest_payload = {
        "context_characters": reference_occurrences["context_characters"],
        "source_document_ids": reference_occurrences["source_document_ids"],
        "source_artifact_manifest_sha256": reference_occurrences[
            "source_artifact_manifest_sha256"
        ],
        "source_artifacts": reference_occurrences["source_artifacts"],
        "counters": reference_occurrences["counters"],
        "occurrences": occurrences,
    }
    if reference_occurrences["corpus_sha256"] != packet_digest(digest_payload):
        raise ValueError("Reference occurrence corpus_sha256 is invalid")

    source_position = {
        record["source_rule_id"]: index for index, record in enumerate(source_records)
    }
    seen_ids: set[str] = set()
    ordinals: dict[str, int] = defaultdict(int)
    previous_source_position = -1
    for occurrence in occurrences:
        source_rule_id = occurrence["source_rule_id"]
        if source_rule_id not in source_position:
            raise ValueError(
                f"Reference occurrence source is not an active rule: {source_rule_id}"
            )
        if source_position[source_rule_id] < previous_source_position:
            raise ValueError("Reference occurrences do not preserve source rule order")
        previous_source_position = source_position[source_rule_id]

        occurrence_id = occurrence["occurrence_id"]
        if occurrence_id in seen_ids:
            raise ValueError(f"Duplicate reference occurrence id: {occurrence_id}")
        seen_ids.add(occurrence_id)
        ordinals[source_rule_id] += 1
        expected_id = f"{source_rule_id}:xref:{ordinals[source_rule_id]:04d}"
        if occurrence_id != expected_id:
            raise ValueError(
                f"Reference occurrence id is not source ordered: {occurrence_id}"
            )

        target_rule_id = occurrence["target"]["rule_id"]
        if not RULE_ID_RE.fullmatch(target_rule_id):
            raise ValueError(
                f"Reference occurrence target is not a rule id: {occurrence_id}"
            )
    return occurrences


def validate_reference_resolutions(
    source_records: list[dict[str, Any]],
    corrections: dict[str, Any],
    occurrences: list[dict[str, Any]],
    reference_resolutions: dict[str, Any],
    source_sha256: str,
    corrections_sha256: str,
    reference_occurrences_sha256: str,
    reference_resolutions_sha256: str,
) -> dict[str, dict[str, Any]]:
    if reference_resolutions.get("format") != "iupac-bluebook-reference-resolutions":
        raise ValueError("Reference resolution corpus format is invalid")
    if reference_resolutions.get("format_version") != "1.0.0":
        raise ValueError("Reference resolution corpus version is invalid")
    if (
        reference_resolutions.get("policy")
        != "exact_occurrence_only_no_generic_parent_fallback"
    ):
        raise ValueError("Reference resolution policy is invalid")
    if (
        sha256_bytes(canonical_json_bytes(reference_resolutions))
        != reference_resolutions_sha256
    ):
        raise ValueError(
            "Reference resolution corpus SHA-256 does not match its canonical JSON bytes"
        )

    dependency_hashes = {
        "source_corpus_sha256": source_sha256,
        "correction_overlays_sha256": corrections_sha256,
        "reference_occurrences_sha256": reference_occurrences_sha256,
    }
    for field, expected in dependency_hashes.items():
        if reference_resolutions.get(field) != expected:
            raise ValueError(
                f"Reference resolution {field} does not match its packet input"
            )

    corpus_without_digest = dict(reference_resolutions)
    corpus_digest = corpus_without_digest.pop("corpus_sha256")
    if corpus_digest != packet_digest(corpus_without_digest):
        raise ValueError("Reference resolution corpus_sha256 is invalid")

    records = reference_resolutions["records"]
    counters = reference_resolutions["counters"]
    if len(records) != EXPECTED_REFERENCE_RESOLUTION_COUNT:
        raise ValueError(
            "Reference resolution corpus must contain exactly "
            f"{EXPECTED_REFERENCE_RESOLUTION_COUNT} records; found {len(records)}"
        )
    if counters["resolution_record_count"] != len(records):
        raise ValueError("Reference resolution counter does not match its records")
    if counters["remaining_unresolved_occurrence_count"] != 0:
        raise ValueError("Reference resolution corpus leaves unresolved occurrences")

    occurrence_by_id = {
        occurrence["occurrence_id"]: occurrence for occurrence in occurrences
    }
    unresolved_occurrence_ids = [
        occurrence["occurrence_id"]
        for occurrence in occurrences
        if occurrence["target"]["resolution"] == "unresolved"
    ]
    resolution_occurrence_ids = [record["occurrence_id"] for record in records]
    if resolution_occurrence_ids != unresolved_occurrence_ids:
        raise ValueError(
            "Reference resolutions do not exactly cover raw unresolved occurrences"
        )
    if counters["raw_unresolved_occurrence_count"] != len(
        unresolved_occurrence_ids
    ):
        raise ValueError("Reference resolution unresolved counter is invalid")

    active_ids = {record["source_rule_id"] for record in source_records}
    correction_by_id = {
        record["overlay_id"]: record for record in corrections["records"]
    }
    records_by_occurrence: dict[str, dict[str, Any]] = {}
    kind_counts: dict[str, int] = defaultdict(int)
    for ordinal, record in enumerate(records, start=1):
        resolution_id = f"BBV3-XREF-RES-{ordinal:04d}"
        if record["resolution_id"] != resolution_id:
            raise ValueError(
                f"Reference resolution id is not source ordered: {record['resolution_id']}"
            )
        occurrence = occurrence_by_id[record["occurrence_id"]]
        if record["nominal_rule_id"] not in {
            occurrence["target"]["rule_id"],
            occurrence["cited_rule_id"],
        } or occurrence["target"]["rule_id"] != occurrence["cited_rule_id"]:
            raise ValueError(
                f"Reference resolution nominal target differs: {resolution_id}"
            )
        if record["reference_occurrence_sha256"] != packet_digest(occurrence):
            raise ValueError(
                f"Reference occurrence SHA-256 is invalid for {resolution_id}"
            )

        record_without_digest = dict(record)
        record_digest = record_without_digest.pop("record_sha256")
        if record_digest != packet_digest(record_without_digest):
            raise ValueError(f"Reference resolution record_sha256 is invalid: {resolution_id}")

        resolution_kind = record["resolution_kind"]
        resolved_rule_id = record["resolved_rule_id"]
        correction_overlay_id = record["correction_overlay_id"]
        if resolution_kind == "source_alias":
            if (
                record["nominal_rule_id"] in active_ids
                or resolved_rule_id not in active_ids
                or correction_overlay_id is not None
                or record["rationale_code"]
                != "nonexistent_subrule_resolved_to_exact_active_parent"
            ):
                raise ValueError(f"Reference source alias is invalid: {resolution_id}")
        elif resolution_kind == "historical_deleted_rule":
            overlay = correction_by_id.get(correction_overlay_id)
            if (
                resolved_rule_id in active_ids
                or resolved_rule_id != record["nominal_rule_id"]
                or overlay is None
                or overlay["status"] != "deleted"
                or resolved_rule_id not in correction_rule_targets(overlay)
                or record["rationale_code"] != "deleted_by_official_correction"
            ):
                raise ValueError(
                    f"Reference historical deleted target is invalid: {resolution_id}"
                )
        else:
            raise ValueError(f"Reference resolution kind is invalid: {resolution_id}")

        kind_counts[resolution_kind] += 1
        records_by_occurrence[record["occurrence_id"]] = record

    if counters["resolution_kind_counts"] != {
        "source_alias": kind_counts["source_alias"],
        "historical_deleted_rule": kind_counts["historical_deleted_rule"],
    }:
        raise ValueError("Reference resolution kind counters are invalid")
    return records_by_occurrence


def effective_reference_target(
    occurrence: dict[str, Any],
    resolution: dict[str, Any] | None,
    active_ids: set[str],
) -> tuple[str, str]:
    if resolution is not None:
        target_kind = (
            "rule"
            if resolution["resolution_kind"] == "source_alias"
            else "historical_deleted_rule"
        )
        return resolution["resolved_rule_id"], target_kind
    if occurrence["target"]["resolution"] == "unresolved":
        raise ValueError(
            f"Semantically unresolved reference occurrence: {occurrence['occurrence_id']}"
        )
    target = occurrence["target"]["rule_id"]
    target_kind = "rule" if target in active_ids else "external_or_historical"
    return target, target_kind


def validate_clause_inventory(
    source_records: list[dict[str, Any]],
    clause_inventory: dict[str, Any],
    source_sha256: str,
    document_nodes_sha256: str,
    corrections_sha256: str,
    clause_inventory_sha256: str,
) -> dict[str, dict[str, Any]]:
    source_rule_ids = [record["source_rule_id"] for record in source_records]
    if len(source_rule_ids) != EXPECTED_RULE_COUNT:
        raise ValueError(
            f"Source corpus must contain exactly {EXPECTED_RULE_COUNT} rules; "
            f"found {len(source_rule_ids)}"
        )

    inventory_records = clause_inventory["records"]
    inventory_rule_ids = [record["source_rule_id"] for record in inventory_records]
    if len(inventory_rule_ids) != EXPECTED_RULE_COUNT:
        raise ValueError(
            f"Clause inventory must contain exactly {EXPECTED_RULE_COUNT} records; "
            f"found {len(inventory_rule_ids)}"
        )
    if clause_inventory["counters"]["record_count"] != EXPECTED_RULE_COUNT:
        raise ValueError("Clause inventory record counter is not exactly 2554")
    if inventory_rule_ids != source_rule_ids:
        raise ValueError("Clause inventory does not preserve exact source rule order and coverage")

    dependency_hashes = {
        "source_corpus_sha256": source_sha256,
        "document_nodes_sha256": document_nodes_sha256,
        "correction_overlays_sha256": corrections_sha256,
    }
    for field, expected in dependency_hashes.items():
        if clause_inventory[field] != expected:
            raise ValueError(f"Clause inventory {field} does not match its packet input")

    if sha256_bytes(canonical_json_bytes(clause_inventory)) != clause_inventory_sha256:
        raise ValueError("Clause inventory SHA-256 does not match its canonical JSON bytes")

    inventory_without_digest = dict(clause_inventory)
    inventory_digest = inventory_without_digest.pop("corpus_sha256")
    if inventory_digest != packet_digest(inventory_without_digest):
        raise ValueError("Clause inventory corpus_sha256 is invalid")

    records_by_id: dict[str, dict[str, Any]] = {}
    for source_record, inventory_record in zip(source_records, inventory_records):
        rule_id = source_record["source_rule_id"]
        if inventory_record["record_id"] != source_record["record_id"]:
            raise ValueError(f"Clause inventory record_id does not link to source rule {rule_id}")
        record_without_digest = dict(inventory_record)
        record_digest = record_without_digest.pop("record_sha256")
        if record_digest != packet_digest(record_without_digest):
            raise ValueError(f"Clause inventory record_sha256 is invalid for {rule_id}")
        records_by_id[rule_id] = inventory_record
    return records_by_id


def build_packets(
    source: dict[str, Any],
    document_nodes: dict[str, Any],
    corrections: dict[str, Any],
    clause_inventory: dict[str, Any],
    reference_occurrences: dict[str, Any],
    reference_resolutions: dict[str, Any],
    source_sha256: str,
    document_nodes_sha256: str,
    corrections_sha256: str,
    clause_inventory_sha256: str,
    reference_occurrences_sha256: str,
    reference_resolutions_sha256: str,
    max_assigned_records: int = 24,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = source["records"]
    records_by_id = {record["source_rule_id"]: record for record in records}
    active_ids = set(records_by_id)
    if len(active_ids) != len(records):
        raise ValueError("Source records are not uniquely keyed by source_rule_id")

    clause_records_by_id = validate_clause_inventory(
        records,
        clause_inventory,
        source_sha256,
        document_nodes_sha256,
        corrections_sha256,
        clause_inventory_sha256,
    )
    occurrences = validate_reference_occurrences(
        records, reference_occurrences, reference_occurrences_sha256
    )
    resolutions_by_occurrence = validate_reference_resolutions(
        records,
        corrections,
        occurrences,
        reference_resolutions,
        source_sha256,
        corrections_sha256,
        reference_occurrences_sha256,
        reference_resolutions_sha256,
    )

    fragments_by_id: dict[str, dict[str, Any]] = {}
    for document in document_nodes["documents"]:
        for fragment in document["fragments"]:
            rule_id = fragment["rule_id"]
            if rule_id in fragments_by_id:
                raise ValueError(f"Duplicate document fragment: {rule_id}")
            fragments_by_id[rule_id] = fragment
    if set(fragments_by_id) != active_ids:
        missing = sorted(active_ids.difference(fragments_by_id))
        extra = sorted(set(fragments_by_id).difference(active_ids))
        raise ValueError(f"Document/source coverage differs: missing={missing}, extra={extra}")

    outgoing_occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
    incoming_occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
    occurrence_position: dict[str, int] = {}
    effective_targets: dict[str, tuple[str, str]] = {}
    for index, occurrence in enumerate(occurrences):
        outgoing_occurrences[occurrence["source_rule_id"]].append(occurrence)
        occurrence_id = occurrence["occurrence_id"]
        effective_targets[occurrence_id] = effective_reference_target(
            occurrence, resolutions_by_occurrence.get(occurrence_id), active_ids
        )
        target_rule_id = effective_targets[occurrence_id][0]
        if target_rule_id in active_ids:
            incoming_occurrences[target_rule_id].append(occurrence)
        occurrence_position[occurrence_id] = index

    outgoing = {
        rule_id: sorted(
            {
                effective_targets[occurrence["occurrence_id"]][0]
                for occurrence in outgoing_occurrences.get(rule_id, [])
            }
        )
        for rule_id in active_ids
    }
    incoming = {
        rule_id: sorted(
            {
                occurrence["source_rule_id"]
                for occurrence in incoming_occurrences.get(rule_id, [])
            }
        )
        for rule_id in active_ids
    }

    corrections_by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    corrections_by_id = {
        overlay["overlay_id"]: overlay for overlay in corrections["records"]
    }
    for overlay in corrections["records"]:
        for target in correction_rule_targets(overlay):
            corrections_by_rule[target].append(overlay)

    position = {record["source_rule_id"]: index for index, record in enumerate(records)}
    partitions = partition_records(records, max_assigned_records)
    major_part_numbers: dict[str, int] = defaultdict(int)
    packets: list[dict[str, Any]] = []
    for assigned_records in partitions:
        assigned_ids = [record["source_rule_id"] for record in assigned_records]
        assigned_set = set(assigned_ids)
        major = major_section(assigned_ids[0])
        major_part_numbers[major] += 1
        packet_id = f"{major}-part-{major_part_numbers[major]:03d}"

        context_ids: set[str] = set()
        for rule_id in assigned_ids:
            for ancestor in ancestor_chain(rule_id, records_by_id, active_ids):
                if ancestor in active_ids:
                    context_ids.add(ancestor)
            index = position[rule_id]
            for neighbor in records[max(0, index - 2) : index + 3]:
                context_ids.add(neighbor["source_rule_id"])
            context_ids.update(target for target in outgoing[rule_id] if target in active_ids)
            context_ids.update(incoming.get(rule_id, []))
        context_ids.difference_update(assigned_set)
        ordered_context_ids = sorted(context_ids, key=position.__getitem__)

        relation_edges: list[dict[str, Any]] = []
        for rule_id in assigned_ids:
            parent = immediate_parent(rule_id, records_by_id[rule_id], active_ids)
            relation_edges.append(
                {
                    "source": rule_id,
                    "relation": "hierarchy_parent",
                    "target": parent,
                    "target_kind": (
                        "chapter"
                        if parent.startswith("chapter:")
                        else "rule"
                    ),
                }
            )
            citation_occurrences_by_target: dict[
                tuple[str, str], list[dict[str, Any]]
            ] = defaultdict(list)
            for occurrence in outgoing_occurrences.get(rule_id, []):
                occurrence_id = occurrence["occurrence_id"]
                citation_occurrences_by_target[effective_targets[occurrence_id]].append(
                    occurrence
                )
            for target, target_kind in sorted(citation_occurrences_by_target):
                edge_occurrences = citation_occurrences_by_target[
                    (target, target_kind)
                ]
                relation_edges.append(
                    {
                        "source": rule_id,
                        "relation": "source_citation",
                        "target": target,
                        "target_kind": target_kind,
                        "occurrence_ids": [
                            occurrence["occurrence_id"]
                            for occurrence in edge_occurrences
                        ],
                        "multiplicity": len(edge_occurrences),
                    }
                )

        relevant_corrections = {
            overlay["overlay_id"]: overlay
            for rule_id in assigned_ids
            for overlay in corrections_by_rule.get(rule_id, [])
        }
        relevant_corrections.update(
            {
                resolution["correction_overlay_id"]: corrections_by_id[
                    resolution["correction_overlay_id"]
                ]
                for rule_id in assigned_ids
                for occurrence in outgoing_occurrences.get(rule_id, [])
                if (
                    resolution := resolutions_by_occurrence.get(
                        occurrence["occurrence_id"]
                    )
                )
                and resolution["correction_overlay_id"] is not None
            }
        )
        assigned = []
        for record in assigned_records:
            rule_id = record["source_rule_id"]
            assigned.append(
                {
                    "source_rule_id": rule_id,
                    "source_record": record,
                    "document_fragment": fragments_by_id[rule_id],
                    "clause_inventory_record": clause_records_by_id[rule_id],
                    "immediate_parent": immediate_parent(rule_id, record, active_ids),
                    "ancestor_chain": ancestor_chain(rule_id, records_by_id, active_ids),
                    "preceding_rule_ids": [
                        item["source_rule_id"]
                        for item in records[max(0, position[rule_id] - 2) : position[rule_id]]
                    ],
                    "following_rule_ids": [
                        item["source_rule_id"]
                        for item in records[position[rule_id] + 1 : position[rule_id] + 3]
                    ],
                    "outgoing_source_references": outgoing[rule_id],
                    "incoming_source_references": incoming.get(rule_id, []),
                    "reference_occurrences": outgoing_occurrences.get(rule_id, []),
                    "reference_resolutions": [
                        resolutions_by_occurrence[occurrence["occurrence_id"]]
                        for occurrence in outgoing_occurrences.get(rule_id, [])
                        if occurrence["occurrence_id"] in resolutions_by_occurrence
                    ],
                    "incoming_reference_occurrence_ids": [
                        occurrence["occurrence_id"]
                        for occurrence in incoming_occurrences.get(rule_id, [])
                    ],
                    "correction_overlay_ids": sorted(
                        overlay["overlay_id"]
                        for overlay in corrections_by_rule.get(rule_id, [])
                    ),
                }
            )

        packet: dict[str, Any] = {
            "format": "iupac-bluebook-semantic-work-packet",
            "format_version": "1.0.0",
            "packet_id": packet_id,
            "source_corpus_sha256": source_sha256,
            "document_nodes_sha256": document_nodes_sha256,
            "correction_overlays_sha256": corrections_sha256,
            "clause_inventory_sha256": clause_inventory_sha256,
            "reference_occurrences_sha256": reference_occurrences_sha256,
            "reference_resolutions_sha256": reference_resolutions_sha256,
            "output_path": f"data/bluebook_v3/semantic_chunks/{packet_id}.json",
            "assigned_rule_ids": assigned_ids,
            "assigned": assigned,
            "context_records": [],
            "correction_overlays": [
                relevant_corrections[overlay_id]
                for overlay_id in sorted(relevant_corrections)
            ],
            "relation_edges": relation_edges,
        }
        for rule_id in ordered_context_ids:
            context_occurrences = sorted(
                [
                    occurrence
                    for occurrence in outgoing_occurrences.get(rule_id, [])
                    if effective_targets[occurrence["occurrence_id"]][0]
                    in assigned_set
                ]
                + [
                    occurrence
                    for assigned_rule_id in assigned_ids
                    for occurrence in outgoing_occurrences.get(assigned_rule_id, [])
                    if effective_targets[occurrence["occurrence_id"]][0] == rule_id
                ],
                key=lambda occurrence: occurrence_position[occurrence["occurrence_id"]],
            )
            packet["context_records"].append(
                context_summary(
                    records_by_id[rule_id],
                    fragments_by_id[rule_id],
                    context_occurrences,
                    [
                        resolutions_by_occurrence[occurrence["occurrence_id"]]
                        for occurrence in context_occurrences
                        if occurrence["occurrence_id"] in resolutions_by_occurrence
                    ],
                )
            )
        packet["packet_sha256"] = packet_digest(packet)
        packets.append(packet)

    assigned_all = [rule_id for packet in packets for rule_id in packet["assigned_rule_ids"]]
    if assigned_all != [record["source_rule_id"] for record in records]:
        raise ValueError("Packet assignments do not preserve exact source order and coverage")
    assigned_clause_records = [
        assignment["clause_inventory_record"]["source_rule_id"]
        for packet in packets
        for assignment in packet["assigned"]
    ]
    if assigned_clause_records != assigned_all or len(assigned_all) != EXPECTED_RULE_COUNT:
        raise ValueError("Packet clause inventory linkage is not exact and source ordered")
    assigned_occurrence_ids = [
        occurrence["occurrence_id"]
        for packet in packets
        for assignment in packet["assigned"]
        for occurrence in assignment["reference_occurrences"]
    ]
    corpus_occurrence_ids = [occurrence["occurrence_id"] for occurrence in occurrences]
    if assigned_occurrence_ids != corpus_occurrence_ids:
        raise ValueError(
            "Packet reference occurrence assignments are not exact and source ordered"
        )
    assigned_resolution_records = [
        resolution
        for packet in packets
        for assignment in packet["assigned"]
        for resolution in assignment["reference_resolutions"]
    ]
    if assigned_resolution_records != reference_resolutions["records"]:
        raise ValueError(
            "Packet reference resolution assignments are not exact and source ordered"
        )
    manifest: dict[str, Any] = {
        "format": "iupac-bluebook-semantic-work-packet-manifest",
        "format_version": "1.0.0",
        "source_corpus_sha256": source_sha256,
        "document_nodes_sha256": document_nodes_sha256,
        "correction_overlays_sha256": corrections_sha256,
        "clause_inventory_sha256": clause_inventory_sha256,
        "reference_occurrences_sha256": reference_occurrences_sha256,
        "reference_resolutions_sha256": reference_resolutions_sha256,
        "packet_count": len(packets),
        "assigned_rule_count": len(assigned_all),
        "assigned_reference_occurrence_count": len(assigned_occurrence_ids),
        "assigned_reference_resolution_count": len(assigned_resolution_records),
        "semantically_unresolved_relation_target_count": 0,
        "packets": [
            {
                "packet_id": packet["packet_id"],
                "packet_sha256": packet["packet_sha256"],
                "output_path": packet["output_path"],
                "assigned_rule_ids": packet["assigned_rule_ids"],
            }
            for packet in packets
        ],
    }
    manifest["manifest_sha256"] = packet_digest(manifest)
    return packets, manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build source-complete semantic conversion work packets"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--document-nodes", type=Path, default=DEFAULT_DOCUMENT_NODES)
    parser.add_argument("--corrections", type=Path, default=DEFAULT_CORRECTIONS)
    parser.add_argument("--clause-inventory", type=Path, default=DEFAULT_CLAUSE_INVENTORY)
    parser.add_argument(
        "--reference-occurrences", type=Path, default=DEFAULT_REFERENCE_OCCURRENCES
    )
    parser.add_argument(
        "--reference-resolutions", type=Path, default=DEFAULT_REFERENCE_RESOLUTIONS
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-assigned-records", type=int, default=24)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_bytes = args.source.read_bytes()
    correction_bytes = args.corrections.read_bytes()
    clause_inventory_bytes = args.clause_inventory.read_bytes()
    reference_occurrence_bytes = args.reference_occurrences.read_bytes()
    reference_resolution_bytes = args.reference_resolutions.read_bytes()
    packets, manifest = build_packets(
        load_json(args.source),
        load_document_nodes(args.document_nodes),
        load_json(args.corrections),
        load_json(args.clause_inventory),
        load_json(args.reference_occurrences),
        load_json(args.reference_resolutions),
        sha256_bytes(source_bytes),
        hash_document_nodes(args.document_nodes),
        sha256_bytes(correction_bytes),
        sha256_bytes(clause_inventory_bytes),
        sha256_bytes(reference_occurrence_bytes),
        sha256_bytes(reference_resolution_bytes),
        args.max_assigned_records,
    )
    for packet in packets:
        write_json(args.output_dir / f"{packet['packet_id']}.json", packet)
    write_json(args.output_dir / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "packet_count": len(packets),
                "assigned_rule_count": manifest["assigned_rule_count"],
                "assigned_reference_occurrence_count": manifest[
                    "assigned_reference_occurrence_count"
                ],
                "assigned_reference_resolution_count": manifest[
                    "assigned_reference_resolution_count"
                ],
                "manifest_sha256": manifest["manifest_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
