from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts import build_reference_dependency_graph as builder


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "bluebook_v3"
INPUT_PATH = BASE / "bluebook_v3_reference_occurrences.json"
RESOLUTIONS_PATH = BASE / "bluebook_v3_reference_resolutions.json"
INPUT_SCHEMA_PATH = ROOT / "data" / "bluebook_reference_occurrences.schema.json"
RESOLUTION_SCHEMA_PATH = ROOT / "data" / "bluebook_reference_resolutions.schema.json"
SCHEMA_PATH = ROOT / "data" / "bluebook_reference_dependency_graph.schema.json"
INPUT_ARTIFACT_SHA256 = (
    "8353CF239D4D4E2D4C3362C24DEC14988B98BEEF0CC43F57F575E850906718EF"
)
RESOLUTION_ARTIFACT_SHA256 = (
    "D9B8839AC4475887EA84152B7F60A6709DD7907956A7BF4674EC47CEB03356E9"
)


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


@pytest.fixture(scope="module")
def official() -> tuple[
    bytes,
    dict[str, Any],
    bytes,
    dict[str, Any],
    dict[str, Any],
]:
    input_bytes = INPUT_PATH.read_bytes()
    resolution_bytes = RESOLUTIONS_PATH.read_bytes()
    corpus = json.loads(input_bytes.decode("utf-8-sig"))
    resolutions = json.loads(resolution_bytes.decode("utf-8-sig"))
    graph = builder.build_reference_dependency_graph(
        corpus,
        resolutions,
        source_artifact_sha256=builder.sha256_bytes(input_bytes),
        resolution_artifact_sha256=builder.sha256_bytes(resolution_bytes),
    )
    return input_bytes, corpus, resolution_bytes, resolutions, graph


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return load(SCHEMA_PATH)


def synthetic_corpus(official_corpus: dict[str, Any]) -> dict[str, Any]:
    active = deepcopy(
        next(
            occurrence
            for occurrence in official_corpus["occurrences"]
            if occurrence["target"]["resolution"] == "active_rule"
        )
    )
    duplicate = deepcopy(active)
    duplicate["occurrence_id"] = f"{active['source_rule_id']}:xref:9999"
    document = deepcopy(
        next(
            occurrence
            for occurrence in official_corpus["occurrences"]
            if occurrence["target"]["resolution"] == "document"
        )
    )
    unresolved = deepcopy(
        next(
            occurrence
            for occurrence in official_corpus["occurrences"]
            if occurrence["target"]["resolution"] == "unresolved"
        )
    )
    return {
        "format": official_corpus["format"],
        "version": official_corpus["version"],
        "corpus_sha256": official_corpus["corpus_sha256"],
        "source_artifact_manifest_sha256": official_corpus[
            "source_artifact_manifest_sha256"
        ],
        "occurrences": [active, duplicate, document, unresolved],
    }


def rehash_resolutions(resolutions: dict[str, Any]) -> None:
    kind_counts = Counter(
        record["resolution_kind"] for record in resolutions["records"]
    )
    resolutions["counters"] = {
        "raw_unresolved_occurrence_count": 3,
        "resolution_record_count": len(resolutions["records"]),
        "resolution_kind_counts": {
            kind: kind_counts[kind] for kind in builder.RESOLUTION_KINDS
        },
        "remaining_unresolved_occurrence_count": 0,
    }
    resolutions["corpus_sha256"] = builder.sha256_bytes(
        builder.canonical_json_bytes(
            builder._without_digest(resolutions, "corpus_sha256")
        )
    )


def synthetic_resolutions(
    official_resolutions: dict[str, Any],
    corpus: dict[str, Any],
    *,
    reference_artifact_sha256: str,
) -> dict[str, Any]:
    unresolved_ids = {
        occurrence["occurrence_id"]
        for occurrence in corpus["occurrences"]
        if occurrence["target"]["resolution"] == "unresolved"
    }
    records = [
        deepcopy(record)
        for record in official_resolutions["records"]
        if record["occurrence_id"] in unresolved_ids
    ]
    resolutions = {
        key: deepcopy(value)
        for key, value in official_resolutions.items()
        if key not in {"records", "counters", "corpus_sha256"}
    }
    resolutions["reference_occurrences_sha256"] = reference_artifact_sha256
    resolutions["records"] = records
    kind_counts = Counter(record["resolution_kind"] for record in records)
    resolutions["counters"] = {
        "raw_unresolved_occurrence_count": len(unresolved_ids),
        "resolution_record_count": len(records),
        "resolution_kind_counts": {
            kind: kind_counts[kind] for kind in builder.RESOLUTION_KINDS
        },
        "remaining_unresolved_occurrence_count": 0,
    }
    resolutions["corpus_sha256"] = builder.sha256_bytes(
        builder.canonical_json_bytes(resolutions)
    )
    return resolutions


def rehash_graph(graph: dict[str, Any]) -> None:
    graph["occurrence_manifest_sha256"] = builder.occurrence_manifest_sha256(
        graph["occurrence_manifest"]
    )
    graph["corpus_sha256"] = builder.graph_corpus_sha256(graph)


def test_official_graph_is_strict_valid_dual_bound_and_deterministic(
    official: tuple[bytes, dict[str, Any], bytes, dict[str, Any], dict[str, Any]],
    schema: dict[str, Any],
) -> None:
    input_bytes, corpus, resolution_bytes, resolutions, graph = official
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(graph)
    builder.validate_graph(
        graph,
        schema,
        input_corpus=corpus,
        input_artifact_sha256=INPUT_ARTIFACT_SHA256,
        resolution_corpus=resolutions,
        resolution_artifact_sha256=RESOLUTION_ARTIFACT_SHA256,
    )

    assert builder.sha256_bytes(input_bytes) == INPUT_ARTIFACT_SHA256
    assert builder.sha256_bytes(resolution_bytes) == RESOLUTION_ARTIFACT_SHA256
    assert graph["source_artifact"]["artifact_sha256"] == INPUT_ARTIFACT_SHA256
    assert graph["resolution_artifact"] == {
        "format": "iupac-bluebook-reference-resolutions",
        "format_version": "1.0.0",
        "artifact_sha256": RESOLUTION_ARTIFACT_SHA256,
        "declared_corpus_sha256": resolutions["corpus_sha256"],
        "reference_occurrences_sha256": INPUT_ARTIFACT_SHA256,
        "source_corpus_sha256": resolutions["source_corpus_sha256"],
        "correction_overlays_sha256": resolutions["correction_overlays_sha256"],
        "policy": "exact_occurrence_only_no_generic_parent_fallback",
    }
    assert graph["occurrence_manifest_sha256"] == (
        "E4EA41AEE8D255D0790F26E3D2346945A7EB4529B8B987FC2BAB72B66F6B9958"
    )
    assert graph["corpus_sha256"] == (
        "658BFF65300B86E8C6A8F9A47143D8183B2A17C484F77C3568011020D2765197"
    )
    rebuilt = builder.build_reference_dependency_graph(
        corpus,
        resolutions,
        source_artifact_sha256=INPUT_ARTIFACT_SHA256,
        resolution_artifact_sha256=RESOLUTION_ARTIFACT_SHA256,
    )
    assert builder.canonical_json_bytes(rebuilt) == builder.canonical_json_bytes(graph)


def test_all_occurrences_and_resolution_history_are_preserved_exactly_once(
    official: tuple[bytes, dict[str, Any], bytes, dict[str, Any], dict[str, Any]],
) -> None:
    _, corpus, _, resolutions, graph = official
    assert graph["counters"] == {
        "node_count": 2029,
        "rule_node_count": 2018,
        "document_node_count": 10,
        "historical_rule_node_count": 1,
        "unresolved_node_count": 0,
        "source_rule_count": 1363,
        "target_node_count": 1531,
        "edge_count": 3587,
        "occurrence_count": 4023,
        "edge_target_kind_counts": {
            "rule": 3472,
            "document": 114,
            "historical_rule": 1,
            "unresolved": 0,
        },
        "occurrence_target_kind_counts": {
            "rule": 3898,
            "document": 124,
            "historical_rule": 1,
            "unresolved": 0,
        },
        "raw_occurrence_target_kind_counts": {
            "rule": 3896,
            "document": 124,
            "historical_rule": 0,
            "unresolved": 3,
        },
        "resolution_record_count": 3,
        "resolution_kind_counts": {
            "source_alias": 2,
            "historical_deleted_rule": 1,
        },
    }

    manifest = {entry["occurrence_id"]: entry for entry in graph["occurrence_manifest"]}
    input_ids = [occurrence["occurrence_id"] for occurrence in corpus["occurrences"]]
    evidence_ids = [
        occurrence_id
        for edge in graph["edges"]
        for occurrence_id in edge["evidence_occurrence_ids"]
    ]
    assert len(manifest) == len(input_ids) == 4023
    assert Counter(manifest.keys()) == Counter(input_ids) == Counter(evidence_ids)
    assert all(count == 1 for count in Counter(evidence_ids).values())

    resolution_by_occurrence = {
        record["occurrence_id"]: record for record in resolutions["records"]
    }
    edge_by_id = {edge["edge_id"]: edge for edge in graph["edges"]}
    for ordinal, occurrence in enumerate(corpus["occurrences"], start=1):
        entry = manifest[occurrence["occurrence_id"]]
        resolution = resolution_by_occurrence.get(occurrence["occurrence_id"])
        projection = builder.occurrence_projection(occurrence, resolution)
        assert entry["input_ordinal"] == ordinal
        assert entry["occurrence_sha256"] == builder.sha256_bytes(
            builder.canonical_json_bytes(occurrence)
        )
        for field in (
            "edge_id",
            "source_rule_id",
            "target_kind",
            "target_id",
            "raw_target_kind",
            "raw_target_id",
        ):
            assert entry[field] == projection[field]
        assert entry["resolution_id"] == (
            resolution["resolution_id"] if resolution is not None else None
        )
        assert entry["resolution_kind"] == (
            resolution["resolution_kind"] if resolution is not None else None
        )
        assert occurrence["occurrence_id"] in edge_by_id[entry["edge_id"]][
            "evidence_occurrence_ids"
        ]


def test_aliases_and_historical_deletion_have_exact_typed_targets(
    official: tuple[bytes, dict[str, Any], bytes, dict[str, Any], dict[str, Any]],
) -> None:
    _, _, _, _, graph = official
    manifest = {entry["occurrence_id"]: entry for entry in graph["occurrence_manifest"]}
    for occurrence_id in (
        "P-16.2.4.1:xref:0005",
        "P-66.2.1:xref:0001",
    ):
        entry = manifest[occurrence_id]
        assert (entry["raw_target_kind"], entry["raw_target_id"]) == (
            "unresolved",
            "P-66.1.2.1",
        )
        assert (entry["target_kind"], entry["target_id"]) == (
            "rule",
            "P-66.1.2",
        )
        assert entry["resolution_kind"] == "source_alias"

    deleted = manifest["P-65.7:xref:0009"]
    assert (deleted["raw_target_kind"], deleted["raw_target_id"]) == (
        "unresolved",
        "P-65.7.8",
    )
    assert (deleted["target_kind"], deleted["target_id"]) == (
        "historical_rule",
        "P-65.7.8",
    )
    assert deleted["resolution_kind"] == "historical_deleted_rule"
    assert next(
        node
        for node in graph["nodes"]
        if node["node_id"] == "historical_rule:P-65.7.8"
    ) == {
        "node_id": "historical_rule:P-65.7.8",
        "kind": "historical_rule",
        "roles": ["target"],
        "historical_rule_id": "P-65.7.8",
        "tombstone": True,
    }


def test_synthetic_aggregation_uses_an_explicit_resolution_subset(
    official: tuple[bytes, dict[str, Any], bytes, dict[str, Any], dict[str, Any]],
    schema: dict[str, Any],
) -> None:
    _, official_corpus, _, official_resolutions, _ = official
    corpus = synthetic_corpus(official_corpus)
    resolutions = synthetic_resolutions(
        official_resolutions, corpus, reference_artifact_sha256="A" * 64
    )
    graph = builder.build_reference_dependency_graph(
        corpus,
        resolutions,
        source_artifact_sha256="A" * 64,
        resolution_artifact_sha256="B" * 64,
    )
    builder.validate_graph(
        graph,
        schema,
        input_corpus=corpus,
        input_artifact_sha256="A" * 64,
        resolution_corpus=resolutions,
        resolution_artifact_sha256="B" * 64,
    )

    assert graph["counters"]["occurrence_count"] == 4
    assert graph["counters"]["edge_count"] == 3
    assert graph["counters"]["occurrence_target_kind_counts"] == {
        "rule": 3,
        "document": 1,
        "historical_rule": 0,
        "unresolved": 0,
    }
    repeated = next(edge for edge in graph["edges"] if edge["occurrence_count"] == 2)
    assert repeated["target_kind"] == "rule"


def test_cli_defaults_require_resolutions_and_write_identical_bytes(
    tmp_path: Path,
    official: tuple[bytes, dict[str, Any], bytes, dict[str, Any], dict[str, Any]],
) -> None:
    _, _, _, _, graph = official
    defaults = builder.parse_args(["--out", str(tmp_path / "unused.json")])
    assert defaults.resolutions == RESOLUTIONS_PATH
    assert defaults.resolution_schema == RESOLUTION_SCHEMA_PATH

    first = tmp_path / "graph-1.json"
    second = tmp_path / "graph-2.json"
    common = [
        "--input",
        str(INPUT_PATH),
        "--input-schema",
        str(INPUT_SCHEMA_PATH),
        "--resolutions",
        str(RESOLUTIONS_PATH),
        "--resolution-schema",
        str(RESOLUTION_SCHEMA_PATH),
        "--schema",
        str(SCHEMA_PATH),
    ]
    assert builder.main([*common, "--out", str(first)]) == 0
    assert builder.main([*common, "--out", str(second)]) == 0
    assert first.read_bytes() == second.read_bytes() == builder.canonical_json_bytes(graph)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda graph: graph.__setitem__("semantic_exception_relations", []),
        lambda graph: graph["edges"][0].__setitem__("relation", "exception"),
        lambda graph: graph["occurrence_manifest"][0].pop("raw_target_id"),
        lambda graph: next(
            node for node in graph["nodes"] if node["kind"] == "historical_rule"
        ).__setitem__("tombstone", False),
    ],
)
def test_strict_schema_rejects_forbidden_or_malformed_shapes(
    mutate: Callable[[dict[str, Any]], None],
    official: tuple[bytes, dict[str, Any], bytes, dict[str, Any], dict[str, Any]],
    schema: dict[str, Any],
) -> None:
    graph = deepcopy(official[4])
    mutate(graph)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(graph))


def test_missing_resolution_record_is_rejected_without_fallback(
    official: tuple[bytes, dict[str, Any], bytes, dict[str, Any], dict[str, Any]],
) -> None:
    _, corpus, _, official_resolutions, _ = official
    resolutions = deepcopy(official_resolutions)
    resolutions["records"].pop()
    rehash_resolutions(resolutions)
    with pytest.raises(ValueError, match="coverage mismatch.*missing"):
        builder.build_reference_dependency_graph(
            corpus,
            resolutions,
            source_artifact_sha256=INPUT_ARTIFACT_SHA256,
            resolution_artifact_sha256="B" * 64,
        )


def test_forged_resolution_record_is_rejected_after_rehashing(
    official: tuple[bytes, dict[str, Any], bytes, dict[str, Any], dict[str, Any]],
) -> None:
    _, corpus, _, official_resolutions, _ = official
    resolutions = deepcopy(official_resolutions)
    record = resolutions["records"][0]
    record["reference_occurrence_sha256"] = "C" * 64
    record["record_sha256"] = builder.sha256_bytes(
        builder.canonical_json_bytes(builder._without_digest(record, "record_sha256"))
    )
    rehash_resolutions(resolutions)
    with pytest.raises(ValueError, match="occurrence hash does not replay"):
        builder.build_reference_dependency_graph(
            corpus,
            resolutions,
            source_artifact_sha256=INPUT_ARTIFACT_SHA256,
            resolution_artifact_sha256="B" * 64,
        )


def test_unused_resolution_record_is_rejected_without_generic_matching(
    official: tuple[bytes, dict[str, Any], bytes, dict[str, Any], dict[str, Any]],
) -> None:
    _, corpus, _, official_resolutions, _ = official
    resolutions = deepcopy(official_resolutions)
    occurrence = next(
        item
        for item in corpus["occurrences"]
        if item["target"]["resolution"] == "active_rule"
    )
    record = deepcopy(resolutions["records"][0])
    record["resolution_id"] = "BBV3-XREF-RES-9999"
    record["occurrence_id"] = occurrence["occurrence_id"]
    record["nominal_rule_id"] = occurrence["target"]["rule_id"]
    record["reference_occurrence_sha256"] = builder.sha256_bytes(
        builder.canonical_json_bytes(occurrence)
    )
    record["record_sha256"] = builder.sha256_bytes(
        builder.canonical_json_bytes(builder._without_digest(record, "record_sha256"))
    )
    resolutions["records"].append(record)
    rehash_resolutions(resolutions)
    with pytest.raises(ValueError, match="coverage mismatch.*unused"):
        builder.build_reference_dependency_graph(
            corpus,
            resolutions,
            source_artifact_sha256=INPUT_ARTIFACT_SHA256,
            resolution_artifact_sha256="B" * 64,
        )


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        ("missing_evidence", "coverage.evidence"),
        ("forged_occurrence", "manifest.input_replay"),
        ("forged_source_hash", "source.hash"),
        ("forged_resolution_hash", "resolutions.hash"),
        ("forged_counters", "counters.replay"),
    ],
)
def test_rehashed_graph_mutations_fail_cross_record_audit(
    mutation: str,
    error_code: str,
    official: tuple[bytes, dict[str, Any], bytes, dict[str, Any], dict[str, Any]],
) -> None:
    _, corpus, _, resolutions, official_graph = official
    graph = deepcopy(official_graph)
    if mutation == "missing_evidence":
        edge = next(edge for edge in graph["edges"] if edge["occurrence_count"] > 1)
        edge["evidence_occurrence_ids"].pop()
        edge["occurrence_count"] -= 1
    elif mutation == "forged_occurrence":
        graph["occurrence_manifest"][0]["occurrence_sha256"] = "B" * 64
    elif mutation == "forged_source_hash":
        graph["source_artifact"]["artifact_sha256"] = "B" * 64
    elif mutation == "forged_resolution_hash":
        graph["resolution_artifact"]["artifact_sha256"] = "B" * 64
    elif mutation == "forged_counters":
        graph["counters"]["edge_count"] += 1
    rehash_graph(graph)

    errors = builder.audit_graph(
        graph,
        input_corpus=corpus,
        input_artifact_sha256=INPUT_ARTIFACT_SHA256,
        resolution_corpus=resolutions,
        resolution_artifact_sha256=RESOLUTION_ARTIFACT_SHA256,
    )
    assert any(error.startswith(error_code) for error in errors)
