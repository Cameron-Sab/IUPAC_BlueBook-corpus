from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ROOT / "data" / "bluebook_v3" / "bluebook_v3_reference_occurrences.json"
)
DEFAULT_RESOLUTIONS = (
    ROOT / "data" / "bluebook_v3" / "bluebook_v3_reference_resolutions.json"
)
DEFAULT_INPUT_SCHEMA = ROOT / "data" / "bluebook_reference_occurrences.schema.json"
DEFAULT_RESOLUTION_SCHEMA = ROOT / "data" / "bluebook_reference_resolutions.schema.json"
DEFAULT_SCHEMA = ROOT / "data" / "bluebook_reference_dependency_graph.schema.json"

SHA256_RE = re.compile(r"^[0-9A-F]{64}$")
TARGET_KIND_ORDER = {
    "rule": 0,
    "document": 1,
    "historical_rule": 2,
    "unresolved": 3,
}
RESOLUTION_KINDS = ("source_alias", "historical_deleted_rule")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def corpus_digest_payload(graph: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in graph.items() if key != "corpus_sha256"}


def occurrence_manifest_sha256(manifest: Sequence[Mapping[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(manifest))


def graph_corpus_sha256(graph: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(corpus_digest_payload(graph)))


def _without_digest(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def _resolution_artifact_binding(
    resolutions: Mapping[str, Any], artifact_sha256: str
) -> dict[str, Any]:
    return {
        "format": resolutions["format"],
        "format_version": resolutions["format_version"],
        "artifact_sha256": artifact_sha256,
        "declared_corpus_sha256": resolutions["corpus_sha256"],
        "reference_occurrences_sha256": resolutions[
            "reference_occurrences_sha256"
        ],
        "source_corpus_sha256": resolutions["source_corpus_sha256"],
        "correction_overlays_sha256": resolutions["correction_overlays_sha256"],
        "policy": resolutions["policy"],
    }


def edge_id(source_node_id: str, target_node_id: str) -> str:
    key = {
        "relation": "source_reference",
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
    }
    return f"edge:{sha256_bytes(canonical_json_bytes(key))}"


def raw_target_projection(occurrence: Mapping[str, Any]) -> tuple[str, str, str]:
    target = occurrence.get("target")
    if not isinstance(target, Mapping):
        raise ValueError(f"Occurrence {occurrence.get('occurrence_id')!r} has no target")

    resolution = target.get("resolution")
    if resolution == "active_rule":
        target_id = target.get("rule_id")
        target_kind = "rule"
    elif resolution == "document":
        document = target.get("document")
        if not isinstance(document, Mapping):
            raise ValueError(
                f"Document occurrence {occurrence.get('occurrence_id')!r} has no document"
            )
        target_id = document.get("document_id")
        target_kind = "document"
    elif resolution == "unresolved":
        target_id = target.get("rule_id")
        target_kind = "unresolved"
    else:
        occurrence_id = occurrence.get("occurrence_id")
        raise ValueError(
            f"Occurrence {occurrence_id!r} has unsupported resolution "
            f"{resolution!r}"
        )

    if not isinstance(target_id, str) or not target_id:
        raise ValueError(
            f"Occurrence {occurrence.get('occurrence_id')!r} has no projected target ID"
        )
    return target_kind, target_id, f"{target_kind}:{target_id}"


def occurrence_projection(
    occurrence: Mapping[str, Any],
    resolution_record: Mapping[str, Any] | None,
) -> dict[str, str]:
    source_rule_id = occurrence.get("source_rule_id")
    if not isinstance(source_rule_id, str) or not source_rule_id:
        raise ValueError(
            f"Occurrence {occurrence.get('occurrence_id')!r} has no source_rule_id"
        )
    source_node_id = f"rule:{source_rule_id}"
    raw_target_kind, raw_target_id, _ = raw_target_projection(occurrence)
    if raw_target_kind == "unresolved":
        if resolution_record is None:
            raise ValueError(
                f"Unresolved occurrence {occurrence.get('occurrence_id')!r} has no "
                "explicit resolution record"
            )
        resolution_kind = resolution_record.get("resolution_kind")
        target_id = resolution_record.get("resolved_rule_id")
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("Resolution record has no resolved_rule_id")
        if resolution_kind == "source_alias":
            target_kind = "rule"
        elif resolution_kind == "historical_deleted_rule":
            target_kind = "historical_rule"
        else:
            raise ValueError(f"Unsupported resolution kind: {resolution_kind!r}")
    else:
        if resolution_record is not None:
            raise ValueError(
                f"Resolved occurrence {occurrence.get('occurrence_id')!r} has an "
                "unused resolution record"
            )
        target_kind = raw_target_kind
        target_id = raw_target_id
    target_node_id = f"{target_kind}:{target_id}"
    return {
        "edge_id": edge_id(source_node_id, target_node_id),
        "source_node_id": source_node_id,
        "source_rule_id": source_rule_id,
        "target_node_id": target_node_id,
        "target_kind": target_kind,
        "target_id": target_id,
        "raw_target_kind": raw_target_kind,
        "raw_target_id": raw_target_id,
    }


def _node(node_id: str, kind: str, identifier: str, roles: set[str]) -> dict[str, Any]:
    node: dict[str, Any] = {
        "node_id": node_id,
        "kind": kind,
        "roles": [role for role in ("source", "target") if role in roles],
    }
    if kind == "rule":
        node["rule_id"] = identifier
    elif kind == "document":
        node["document_id"] = identifier
    elif kind == "historical_rule":
        node["historical_rule_id"] = identifier
        node["tombstone"] = True
    else:
        node["cited_rule_id"] = identifier
    return node


def _index_resolution_records(
    occurrences: Sequence[Mapping[str, Any]],
    resolutions: Mapping[str, Any],
    *,
    reference_occurrences_sha256: str,
    resolution_artifact_sha256: str,
) -> dict[str, Mapping[str, Any]]:
    if resolutions.get("format") != "iupac-bluebook-reference-resolutions":
        raise ValueError("Resolution input is not a Blue Book resolution artifact")
    if not SHA256_RE.fullmatch(resolution_artifact_sha256):
        raise ValueError("resolution_artifact_sha256 must be an uppercase SHA-256 digest")
    if resolutions.get("policy") != "exact_occurrence_only_no_generic_parent_fallback":
        raise ValueError("Resolution artifact does not declare exact-occurrence policy")
    if resolutions.get("reference_occurrences_sha256") != reference_occurrences_sha256:
        raise ValueError(
            "Resolution artifact is not bound to the exact reference-occurrence artifact"
        )
    expected_corpus_hash = sha256_bytes(
        canonical_json_bytes(_without_digest(resolutions, "corpus_sha256"))
    )
    if resolutions.get("corpus_sha256") != expected_corpus_hash:
        raise ValueError("Resolution artifact corpus_sha256 does not replay")

    occurrence_by_id: dict[str, Mapping[str, Any]] = {}
    for occurrence in occurrences:
        occurrence_id = occurrence.get("occurrence_id")
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise ValueError("Reference occurrence has no occurrence_id")
        if occurrence_id in occurrence_by_id:
            raise ValueError(f"Duplicate occurrence_id: {occurrence_id}")
        occurrence_by_id[occurrence_id] = occurrence

    records = resolutions.get("records")
    if not isinstance(records, list):
        raise ValueError("Resolution artifact has no records array")
    record_by_occurrence: dict[str, Mapping[str, Any]] = {}
    seen_resolution_ids: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("Resolution record is not an object")
        resolution_id = record.get("resolution_id")
        occurrence_id = record.get("occurrence_id")
        if not isinstance(resolution_id, str) or not resolution_id:
            raise ValueError("Resolution record has no resolution_id")
        if resolution_id in seen_resolution_ids:
            raise ValueError(f"Duplicate resolution_id: {resolution_id}")
        seen_resolution_ids.add(resolution_id)
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise ValueError(f"Resolution {resolution_id} has no occurrence_id")
        if occurrence_id in record_by_occurrence:
            raise ValueError(f"Duplicate resolution for occurrence {occurrence_id}")
        expected_record_hash = sha256_bytes(
            canonical_json_bytes(_without_digest(record, "record_sha256"))
        )
        if record.get("record_sha256") != expected_record_hash:
            raise ValueError(f"Resolution record hash does not replay: {resolution_id}")
        record_by_occurrence[occurrence_id] = record

    unresolved_ids = {
        occurrence_id
        for occurrence_id, occurrence in occurrence_by_id.items()
        if occurrence.get("target", {}).get("resolution") == "unresolved"
    }
    resolution_occurrence_ids = set(record_by_occurrence)
    missing = unresolved_ids - resolution_occurrence_ids
    unused = resolution_occurrence_ids - unresolved_ids
    if missing or unused:
        raise ValueError(
            "Explicit resolution coverage mismatch: "
            f"missing={sorted(missing)}, unused={sorted(unused)}"
        )

    for occurrence_id, record in record_by_occurrence.items():
        occurrence = occurrence_by_id[occurrence_id]
        _, raw_target_id, _ = raw_target_projection(occurrence)
        if record.get("nominal_rule_id") != raw_target_id:
            raise ValueError(
                f"Resolution {record.get('resolution_id')} does not match raw target"
            )
        expected_occurrence_hash = sha256_bytes(canonical_json_bytes(occurrence))
        if record.get("reference_occurrence_sha256") != expected_occurrence_hash:
            raise ValueError(
                f"Resolution {record.get('resolution_id')} occurrence hash does not replay"
            )
        if record.get("resolution_kind") not in RESOLUTION_KINDS:
            raise ValueError(
                f"Unsupported resolution kind: {record.get('resolution_kind')!r}"
            )

    kind_counts = Counter(record["resolution_kind"] for record in records)
    expected_counters = {
        "raw_unresolved_occurrence_count": len(unresolved_ids),
        "resolution_record_count": len(records),
        "resolution_kind_counts": {
            kind: kind_counts[kind] for kind in RESOLUTION_KINDS
        },
        "remaining_unresolved_occurrence_count": 0,
    }
    if resolutions.get("counters") != expected_counters:
        raise ValueError("Resolution artifact counters do not replay")
    return record_by_occurrence


def _counter_payload(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    manifest: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    node_kinds = Counter(node["kind"] for node in nodes)
    edge_kinds = Counter(edge["target_kind"] for edge in edges)
    occurrence_kinds = Counter(entry["target_kind"] for entry in manifest)
    resolution_kinds = Counter(
        entry["resolution_kind"]
        for entry in manifest
        if entry["resolution_kind"] is not None
    )
    raw_occurrence_kinds = Counter(entry["raw_target_kind"] for entry in manifest)
    return {
        "node_count": len(nodes),
        "rule_node_count": node_kinds["rule"],
        "document_node_count": node_kinds["document"],
        "historical_rule_node_count": node_kinds["historical_rule"],
        "unresolved_node_count": node_kinds["unresolved"],
        "source_rule_count": sum(
            node["kind"] == "rule" and "source" in node["roles"] for node in nodes
        ),
        "target_node_count": sum("target" in node["roles"] for node in nodes),
        "edge_count": len(edges),
        "occurrence_count": len(manifest),
        "edge_target_kind_counts": {
            kind: edge_kinds[kind] for kind in TARGET_KIND_ORDER
        },
        "occurrence_target_kind_counts": {
            kind: occurrence_kinds[kind] for kind in TARGET_KIND_ORDER
        },
        "raw_occurrence_target_kind_counts": {
            kind: raw_occurrence_kinds[kind] for kind in TARGET_KIND_ORDER
        },
        "resolution_record_count": sum(
            entry["resolution_id"] is not None for entry in manifest
        ),
        "resolution_kind_counts": {
            kind: resolution_kinds[kind] for kind in RESOLUTION_KINDS
        },
    }


def build_reference_dependency_graph(
    corpus: Mapping[str, Any],
    resolutions: Mapping[str, Any],
    *,
    source_artifact_sha256: str,
    resolution_artifact_sha256: str,
) -> dict[str, Any]:
    if corpus.get("format") != "iupac-bluebook-reference-occurrences":
        raise ValueError("Input is not an IUPAC Blue Book reference-occurrence corpus")
    if not SHA256_RE.fullmatch(source_artifact_sha256):
        raise ValueError("source_artifact_sha256 must be an uppercase SHA-256 digest")
    occurrences = corpus.get("occurrences")
    if not isinstance(occurrences, list):
        raise ValueError("Input occurrence corpus has no occurrences array")
    resolution_by_occurrence = _index_resolution_records(
        occurrences,
        resolutions,
        reference_occurrences_sha256=source_artifact_sha256,
        resolution_artifact_sha256=resolution_artifact_sha256,
    )

    node_roles: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    edge_evidence: dict[tuple[str, str, str, str, str], list[str]] = defaultdict(list)
    manifest: list[dict[str, Any]] = []
    seen_occurrence_ids: set[str] = set()

    for ordinal, occurrence in enumerate(occurrences, start=1):
        if not isinstance(occurrence, Mapping):
            raise ValueError(f"Occurrence at input ordinal {ordinal} is not an object")
        occurrence_id = occurrence.get("occurrence_id")
        if not isinstance(occurrence_id, str) or not occurrence_id:
            raise ValueError(f"Occurrence at input ordinal {ordinal} has no occurrence_id")
        if occurrence_id in seen_occurrence_ids:
            raise ValueError(f"Duplicate occurrence_id: {occurrence_id}")
        seen_occurrence_ids.add(occurrence_id)

        resolution_record = resolution_by_occurrence.get(occurrence_id)
        projection = occurrence_projection(occurrence, resolution_record)
        source_key = (
            projection["source_node_id"],
            "rule",
            projection["source_rule_id"],
        )
        target_key = (
            projection["target_node_id"],
            projection["target_kind"],
            projection["target_id"],
        )
        node_roles[source_key].add("source")
        node_roles[target_key].add("target")

        edge_key = (
            projection["source_node_id"],
            projection["target_node_id"],
            projection["source_rule_id"],
            projection["target_kind"],
            projection["target_id"],
        )
        edge_evidence[edge_key].append(occurrence_id)
        target = occurrence["target"]
        manifest.append(
            {
                "input_ordinal": ordinal,
                "occurrence_id": occurrence_id,
                "occurrence_sha256": sha256_bytes(canonical_json_bytes(occurrence)),
                "edge_id": projection["edge_id"],
                "source_rule_id": projection["source_rule_id"],
                "target_kind": projection["target_kind"],
                "target_id": projection["target_id"],
                "raw_target_kind": projection["raw_target_kind"],
                "raw_target_id": projection["raw_target_id"],
                "resolution_id": (
                    resolution_record["resolution_id"]
                    if resolution_record is not None
                    else None
                ),
                "resolution_kind": (
                    resolution_record["resolution_kind"]
                    if resolution_record is not None
                    else None
                ),
                "reference_kind": occurrence["reference_kind"],
                "resolution_basis": target["resolution_basis"],
            }
        )

    nodes = [
        _node(node_id, kind, identifier, roles)
        for (node_id, kind, identifier), roles in sorted(
            node_roles.items(), key=lambda item: item[0][0]
        )
    ]
    edges: list[dict[str, Any]] = []
    for (
        source_node_id,
        target_node_id,
        source_rule_id,
        target_kind,
        target_id,
    ), occurrence_ids in sorted(
        edge_evidence.items(),
        key=lambda item: (
            item[0][2],
            TARGET_KIND_ORDER[item[0][3]],
            item[0][4],
        ),
    ):
        evidence = sorted(occurrence_ids)
        edges.append(
            {
                "edge_id": edge_id(source_node_id, target_node_id),
                "relation": "source_reference",
                "source_node_id": source_node_id,
                "source_rule_id": source_rule_id,
                "target_node_id": target_node_id,
                "target_kind": target_kind,
                "target_id": target_id,
                "occurrence_count": len(evidence),
                "evidence_occurrence_ids": evidence,
            }
        )

    manifest.sort(key=lambda entry: entry["occurrence_id"])
    graph: dict[str, Any] = {
        "format": "iupac-bluebook-reference-dependency-graph",
        "format_version": "2.0.0",
        "conversion_stage": "occurrence_reference_aggregation",
        "relation_semantics": "mechanical_source_reference_only",
        "source_artifact": {
            "format": corpus["format"],
            "format_version": corpus["version"],
            "artifact_sha256": source_artifact_sha256,
            "declared_corpus_sha256": corpus["corpus_sha256"],
            "upstream_artifact_manifest_sha256": corpus[
                "source_artifact_manifest_sha256"
            ],
        },
        "resolution_artifact": _resolution_artifact_binding(
            resolutions, resolution_artifact_sha256
        ),
        "occurrence_manifest_sha256": occurrence_manifest_sha256(manifest),
        "counters": _counter_payload(nodes, edges, manifest),
        "nodes": nodes,
        "occurrence_manifest": manifest,
        "edges": edges,
    }
    graph["corpus_sha256"] = graph_corpus_sha256(graph)
    return graph


def validate_schema(instance: Any, schema: Mapping[str, Any], label: str) -> None:
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(instance), key=lambda error: list(error.absolute_path)
    )
    if errors:
        details = "\n".join(
            f"- /{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in errors[:25]
        )
        raise ValueError(f"{label} failed schema validation:\n{details}")


def audit_graph(
    graph: Mapping[str, Any],
    *,
    input_corpus: Mapping[str, Any] | None = None,
    input_artifact_sha256: str | None = None,
    resolution_corpus: Mapping[str, Any] | None = None,
    resolution_artifact_sha256: str | None = None,
) -> list[str]:
    errors: list[str] = []
    manifest = graph.get("occurrence_manifest", [])
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not isinstance(manifest, list) or not isinstance(nodes, list) or not isinstance(
        edges, list
    ):
        return ["graph.shape: nodes, occurrence_manifest, and edges must be arrays"]

    expected_manifest_hash = occurrence_manifest_sha256(manifest)
    if graph.get("occurrence_manifest_sha256") != expected_manifest_hash:
        errors.append("hash.manifest: occurrence_manifest_sha256 does not replay")
    if graph.get("corpus_sha256") != graph_corpus_sha256(graph):
        errors.append("hash.corpus: corpus_sha256 does not replay")

    node_by_id = {node.get("node_id"): node for node in nodes}
    if len(node_by_id) != len(nodes):
        errors.append("nodes.unique: node_id values are not unique")
    edge_by_id = {edge.get("edge_id"): edge for edge in edges}
    if len(edge_by_id) != len(edges):
        errors.append("edges.unique: edge_id values are not unique")
    manifest_by_id = {entry.get("occurrence_id"): entry for entry in manifest}
    if len(manifest_by_id) != len(manifest):
        errors.append("manifest.unique: occurrence_id values are not unique")

    observed_evidence: list[str] = []
    for edge in edges:
        source = node_by_id.get(edge.get("source_node_id"))
        target = node_by_id.get(edge.get("target_node_id"))
        if source is None or source.get("kind") != "rule" or "source" not in source.get(
            "roles", []
        ):
            errors.append(f"edge.source: invalid source for {edge.get('edge_id')}")
        elif source.get("rule_id") != edge.get("source_rule_id"):
            errors.append(f"edge.source_id: source rule mismatch for {edge.get('edge_id')}")
        if target is None or "target" not in target.get("roles", []):
            errors.append(f"edge.target: invalid target for {edge.get('edge_id')}")
        else:
            identifier_field = {
                "rule": "rule_id",
                "document": "document_id",
                "historical_rule": "historical_rule_id",
                "unresolved": "cited_rule_id",
            }.get(edge.get("target_kind"))
            if (
                target.get("kind") != edge.get("target_kind")
                or identifier_field is None
                or target.get(identifier_field) != edge.get("target_id")
            ):
                errors.append(
                    f"edge.target_id: target identity mismatch for {edge.get('edge_id')}"
                )
        expected_edge_id = edge_id(
            str(edge.get("source_node_id")), str(edge.get("target_node_id"))
        )
        if edge.get("edge_id") != expected_edge_id:
            errors.append(f"edge.hash_id: edge_id does not replay for {edge.get('edge_id')}")

        evidence = edge.get("evidence_occurrence_ids", [])
        if edge.get("occurrence_count") != len(evidence):
            errors.append(
                f"edge.multiplicity: occurrence_count mismatch for {edge.get('edge_id')}"
            )
        if len(set(evidence)) != len(evidence):
            errors.append(f"edge.evidence_unique: duplicate evidence on {edge.get('edge_id')}")
        observed_evidence.extend(evidence)

    expected_evidence = list(manifest_by_id)
    if Counter(observed_evidence) != Counter(expected_evidence):
        errors.append(
            "coverage.evidence: edge evidence must cover every manifest occurrence exactly once"
        )
    for entry in manifest:
        edge = edge_by_id.get(entry.get("edge_id"))
        if edge is None:
            errors.append(
                f"manifest.edge: missing edge for {entry.get('occurrence_id')}"
            )
            continue
        for field in ("source_rule_id", "target_kind", "target_id"):
            if entry.get(field) != edge.get(field):
                errors.append(
                    f"manifest.projection: {field} mismatch for {entry.get('occurrence_id')}"
                )

    if graph.get("counters") != _counter_payload(nodes, edges, manifest):
        errors.append("counters.replay: graph counters do not replay")

    if input_artifact_sha256 is not None:
        actual = graph.get("source_artifact", {}).get("artifact_sha256")
        if actual != input_artifact_sha256:
            errors.append("source.hash: exact input artifact SHA-256 does not match")
    if resolution_artifact_sha256 is not None:
        actual = graph.get("resolution_artifact", {}).get("artifact_sha256")
        if actual != resolution_artifact_sha256:
            errors.append(
                "resolutions.hash: exact resolution artifact SHA-256 does not match"
            )

    resolution_by_occurrence: dict[str, Mapping[str, Any]] = {}
    if input_corpus is not None:
        if resolution_corpus is None or resolution_artifact_sha256 is None:
            errors.append(
                "resolutions.required: input replay requires the explicit resolution artifact"
            )
        elif input_artifact_sha256 is None:
            errors.append(
                "source.required: resolution replay requires the reference artifact hash"
            )
        else:
            try:
                resolution_by_occurrence = _index_resolution_records(
                    input_corpus.get("occurrences", []),
                    resolution_corpus,
                    reference_occurrences_sha256=input_artifact_sha256,
                    resolution_artifact_sha256=resolution_artifact_sha256,
                )
            except ValueError as error:
                errors.append(f"resolutions.input: {error}")

    if resolution_corpus is not None and resolution_artifact_sha256 is not None:
        expected_resolution_source = _resolution_artifact_binding(
            resolution_corpus, resolution_artifact_sha256
        )
        if graph.get("resolution_artifact") != expected_resolution_source:
            errors.append(
                "resolutions.binding: resolution artifact metadata does not match input"
            )

    if input_corpus is not None:
        source = graph.get("source_artifact", {})
        expected_source = {
            "format": input_corpus.get("format"),
            "format_version": input_corpus.get("version"),
            "declared_corpus_sha256": input_corpus.get("corpus_sha256"),
            "upstream_artifact_manifest_sha256": input_corpus.get(
                "source_artifact_manifest_sha256"
            ),
        }
        for field, expected in expected_source.items():
            if source.get(field) != expected:
                errors.append(f"source.binding: {field} does not match input")

        input_occurrences = input_corpus.get("occurrences", [])
        input_ids = [occurrence.get("occurrence_id") for occurrence in input_occurrences]
        if Counter(input_ids) != Counter(manifest_by_id.keys()):
            errors.append("coverage.input: manifest does not cover every input occurrence")
        for ordinal, occurrence in enumerate(input_occurrences, start=1):
            occurrence_id = occurrence.get("occurrence_id")
            entry = manifest_by_id.get(occurrence_id)
            if entry is None:
                continue
            resolution_record = resolution_by_occurrence.get(occurrence_id)
            try:
                projection = occurrence_projection(occurrence, resolution_record)
            except ValueError as error:
                errors.append(f"manifest.input_projection: {occurrence_id}: {error}")
                continue
            expected_entry = {
                "input_ordinal": ordinal,
                "occurrence_sha256": sha256_bytes(canonical_json_bytes(occurrence)),
                "edge_id": projection["edge_id"],
                "source_rule_id": projection["source_rule_id"],
                "target_kind": projection["target_kind"],
                "target_id": projection["target_id"],
                "raw_target_kind": projection["raw_target_kind"],
                "raw_target_id": projection["raw_target_id"],
                "resolution_id": (
                    resolution_record.get("resolution_id")
                    if resolution_record is not None
                    else None
                ),
                "resolution_kind": (
                    resolution_record.get("resolution_kind")
                    if resolution_record is not None
                    else None
                ),
                "reference_kind": occurrence.get("reference_kind"),
                "resolution_basis": occurrence.get("target", {}).get(
                    "resolution_basis"
                ),
            }
            for field, expected in expected_entry.items():
                if entry.get(field) != expected:
                    errors.append(
                        f"manifest.input_replay: {field} mismatch for {occurrence_id}"
                    )
    if resolution_corpus is not None:
        expected_resolution_ids = Counter(
            record.get("resolution_id")
            for record in resolution_corpus.get("records", [])
        )
        observed_resolution_ids = Counter(
            entry.get("resolution_id")
            for entry in manifest
            if entry.get("resolution_id") is not None
        )
        if observed_resolution_ids != expected_resolution_ids:
            errors.append(
                "coverage.resolutions: every resolution record must be used exactly once"
            )
    return errors


def validate_graph(
    graph: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    input_corpus: Mapping[str, Any] | None = None,
    input_artifact_sha256: str | None = None,
    resolution_corpus: Mapping[str, Any] | None = None,
    resolution_artifact_sha256: str | None = None,
) -> None:
    validate_schema(graph, schema, "Reference dependency graph")
    errors = audit_graph(
        graph,
        input_corpus=input_corpus,
        input_artifact_sha256=input_artifact_sha256,
        resolution_corpus=resolution_corpus,
        resolution_artifact_sha256=resolution_artifact_sha256,
    )
    if errors:
        raise ValueError(
            "Reference dependency graph failed invariant audit:\n"
            + "\n".join(f"- {error}" for error in errors[:50])
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert occurrence-level Blue Book references into a mechanically "
            "aggregated dependency graph using required exact-occurrence "
            "resolution overlays."
        )
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--input-schema", type=Path, default=DEFAULT_INPUT_SCHEMA)
    parser.add_argument("--resolutions", type=Path, default=DEFAULT_RESOLUTIONS)
    parser.add_argument(
        "--resolution-schema", type=Path, default=DEFAULT_RESOLUTION_SCHEMA
    )
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    input_bytes = args.input.read_bytes()
    resolution_bytes = args.resolutions.read_bytes()
    corpus = json.loads(input_bytes.decode("utf-8-sig"))
    resolutions = json.loads(resolution_bytes.decode("utf-8-sig"))
    validate_schema(corpus, load_json(args.input_schema), "Reference occurrence input")
    validate_schema(
        resolutions,
        load_json(args.resolution_schema),
        "Reference resolution input",
    )
    input_sha256 = sha256_bytes(input_bytes)
    resolution_sha256 = sha256_bytes(resolution_bytes)
    graph = build_reference_dependency_graph(
        corpus,
        resolutions,
        source_artifact_sha256=input_sha256,
        resolution_artifact_sha256=resolution_sha256,
    )
    validate_graph(
        graph,
        load_json(args.schema),
        input_corpus=corpus,
        input_artifact_sha256=input_sha256,
        resolution_corpus=resolutions,
        resolution_artifact_sha256=resolution_sha256,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(canonical_json_bytes(graph))
    print(
        f"Wrote {graph['counters']['edge_count']} edges preserving "
        f"{graph['counters']['occurrence_count']} occurrences to {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
