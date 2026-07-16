from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scripts import build_reference_resolution_overlays as builder
from scripts import validate_pdf_rebuild as release_validator


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "bluebook_v3"


def load_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str]]:
    paths = {
        "references": BASE / "bluebook_v3_reference_occurrences.json",
        "source": BASE / "bluebook_v3_source_corpus.json",
        "corrections": BASE / "bluebook_v3_correction_overlays.json",
    }
    raw = {key: path.read_bytes() for key, path in paths.items()}
    return (
        builder.load_json(paths["references"]),
        builder.load_json(paths["source"]),
        builder.load_json(paths["corrections"]),
        {key: builder.sha256_bytes(value) for key, value in raw.items()},
    )


def build(
    references: dict[str, Any],
    source: dict[str, Any],
    corrections: dict[str, Any],
    hashes: dict[str, str],
) -> dict[str, Any]:
    return builder.build_reference_resolutions(
        references,
        source,
        corrections,
        references_sha256=hashes["references"],
        source_sha256=hashes["source"],
        corrections_sha256=hashes["corrections"],
    )


def test_all_raw_unresolved_references_receive_exact_typed_resolutions() -> None:
    references, source, corrections, hashes = load_inputs()

    first = build(references, source, corrections, hashes)
    second = build(references, source, corrections, hashes)

    assert builder.canonical_json_bytes(first) == builder.canonical_json_bytes(second)
    builder.validate_schema(first)
    assert first["counters"] == {
        "raw_unresolved_occurrence_count": 3,
        "resolution_record_count": 3,
        "resolution_kind_counts": {
            "source_alias": 2,
            "historical_deleted_rule": 1,
        },
        "remaining_unresolved_occurrence_count": 0,
    }
    assert {
        (record["occurrence_id"], record["resolution_kind"], record["resolved_rule_id"])
        for record in first["records"]
    } == {
        ("P-16.2.4.1:xref:0005", "source_alias", "P-66.1.2"),
        ("P-65.7:xref:0009", "historical_deleted_rule", "P-65.7.8"),
        ("P-66.2.1:xref:0001", "source_alias", "P-66.1.2"),
    }


def test_every_resolution_and_corpus_hash_reconstructs() -> None:
    references, source, corrections, hashes = load_inputs()
    corpus = build(references, source, corrections, hashes)

    for record in corpus["records"]:
        payload = {key: value for key, value in record.items() if key != "record_sha256"}
        assert record["record_sha256"] == builder.sha256_bytes(
            builder.canonical_json_bytes(payload)
        )
    payload = {key: value for key, value in corpus.items() if key != "corpus_sha256"}
    assert corpus["corpus_sha256"] == builder.sha256_bytes(
        builder.canonical_json_bytes(payload)
    )


def test_missing_raw_occurrence_cannot_be_hidden_by_rebasing_counts() -> None:
    references, source, corrections, hashes = load_inputs()
    mutant = deepcopy(references)
    unresolved = next(
        item
        for item in mutant["occurrences"]
        if item["occurrence_id"] == "P-16.2.4.1:xref:0005"
    )
    unresolved["target"]["resolution"] = "active_rule"

    with pytest.raises(builder.ResolutionError, match="exactly cover"):
        build(mutant, source, corrections, hashes)


def test_alias_requires_absent_nominal_and_present_explicit_target() -> None:
    references, source, corrections, hashes = load_inputs()
    mutant = deepcopy(source)
    mutant["records"].append({"source_rule_id": "P-66.1.2.1"})

    with pytest.raises(builder.ResolutionError, match="Invalid explicit alias"):
        build(references, mutant, corrections, hashes)


def test_historical_resolution_requires_its_exact_deletion_overlay() -> None:
    references, source, corrections, hashes = load_inputs()
    mutant = deepcopy(corrections)
    overlay = next(
        item
        for item in mutant["records"]
        if item["overlay_id"] == "BBV3-CORR-707A0F8B4E94258D"
    )
    overlay["status"] = "replaced"

    with pytest.raises(builder.ResolutionError, match="lacks its declared deletion"):
        build(references, source, mutant, hashes)


def test_generated_artifact_passes_the_source_release_gate() -> None:
    references, source, corrections, hashes = load_inputs()
    resolutions = builder.load_json(builder.DEFAULT_OUTPUT)
    schema = builder.load_json(builder.DEFAULT_SCHEMA)

    result = release_validator.validate_reference_resolution_corpus(
        resolutions,
        schema,
        references,
        source,
        corrections,
        references_sha256=hashes["references"],
        source_sha256=hashes["source"],
        corrections_sha256=hashes["corrections"],
    )

    assert result["passed"] is True
    assert result["errors"] == []
    assert result["metrics"]["remaining_unresolved_occurrence_count"] == 0
