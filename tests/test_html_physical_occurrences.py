from __future__ import annotations

import copy
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from scripts import audit_html_physical_occurrences as audit


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "bluebook_html"
ARTIFACT = audit.DEFAULT_ARTIFACT


@pytest.fixture(scope="session")
def census() -> audit.CorpusCensus:
    return audit.build_census(CACHE)


def _document(census: audit.CorpusCensus, document_id: str) -> audit.DocumentCensus:
    return next(
        document for document in census.documents if document.document_id == document_id
    )


def _part(
    occurrence: audit.Occurrence,
    *,
    part_kind: str = "element",
) -> dict[str, Any]:
    return {
        "dom_path": f"/fragment/audit/{occurrence.kind}[{occurrence.ordinal}]",
        "part_kind": part_kind,
        "fragment_start_byte": occurrence.fragment_start_byte,
        "fragment_end_byte": occurrence.fragment_end_byte,
        "document_start_byte": occurrence.document_start_byte,
        "document_end_byte": occurrence.document_end_byte,
        "raw_sha256": occurrence.raw_sha256,
    }


def _primary_source(
    occurrence: audit.Occurrence,
) -> dict[str, Any]:
    parts = [_part(occurrence)]
    return {
        "parts": parts,
        "manifest_sha256": audit.sha256_bytes(audit.canonical_json_bytes(parts)),
        "ownership": {
            "kind": "primary",
            "owner_ref": occurrence.occurrence_id,
        },
    }


def _minimal_artifact(census: audit.CorpusCensus) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    occurrences_by_rule: dict[str, list[audit.Occurrence]] = {}
    for occurrence in census.occurrences:
        occurrences_by_rule.setdefault(occurrence.rule_id, []).append(occurrence)

    for document in census.documents:
        fragments: list[dict[str, Any]] = []
        for fragment in document.fragments:
            nodes: list[dict[str, Any]] = []
            for occurrence in occurrences_by_rule[fragment.rule_id]:
                if occurrence.kind in audit.TARGET_TAGS:
                    nodes.append(
                        {
                            "occurrence_id": occurrence.occurrence_id,
                            "source": _primary_source(occurrence),
                        }
                    )
                elif occurrence.kind == "footnote_candidate":
                    mapping = _part(occurrence, part_kind="text")
                    nodes.append(
                        {
                            "kind": "footnote",
                            "marker": "*",
                            "text": "candidate",
                            "field_sources": {
                                "marker": {
                                    "ownership": {
                                        "kind": "primary",
                                        "owner_ref": None,
                                    },
                                    "mapping": [mapping],
                                }
                            },
                        }
                    )
            fragments.append(
                {
                    "rule_id": fragment.rule_id,
                    "anchor": fragment.anchor,
                    "ordinal": fragment.ordinal,
                    "source": {
                        "offset_unit": "byte",
                        "start_byte": fragment.start_byte,
                        "end_byte": fragment.end_byte,
                        "anchor_start_byte": fragment.anchor_start_byte,
                        "raw_sha256": fragment.raw_sha256,
                        "active_sha256": fragment.active_sha256,
                    },
                    "nodes": nodes,
                }
            )
        documents.append(
            {
                "document_id": document.document_id,
                "cache_path": document.cache_path,
                "source_byte_count": len(document.raw),
                "source_sha256": document.source_sha256,
                "source_metrics": audit.artifact_metric_counts(
                    document.occurrences
                ),
                "fragments": fragments,
            }
        )
    return {
        "documents": documents,
        "metrics": audit.artifact_metric_counts(census.occurrences),
    }


def _synthetic_census(tmp_path: Path) -> tuple[audit.CorpusCensus, Path]:
    raw = (
        b"<html><body>\n"
        b'<a name="100"><b>P-1.0</b></a> synthetic<p>\n'
        b'<!-- <table><tr><td><img src="ignored.gif"></td></tr></table> -->\n'
        b'<table><tr><td>one<img src="x.gif"></td><tr><td>two</td></table>\n'
        b"* candidate\n"
        b"<hr>\n"
        b"</body></html>\n"
    )
    path = tmp_path / "P1.html"
    path.write_bytes(raw)
    return audit.build_census(tmp_path, (("P-1", "P1.html"),)), path


def test_independent_raw_census_pins_every_requested_count(
    census: audit.CorpusCensus,
) -> None:
    assert len(census.documents) == 11
    assert len(census.fragments) == 2554
    assert audit.assert_pinned_counts(census) == {
        "table": 567,
        "tr": 3782,
        "td": 9100,
        "th": 0,
        "img": 5371,
        "correction_img": 190,
        "footnote_candidate": 7,
    }

    # The second row is a physical, malformed <tr> used where </tr> was meant.
    # Keeping this witness makes the 3,782 count an audited fact, not a copied pin.
    p1 = _document(census, "P-1")
    malformed_rows = [
        occurrence
        for occurrence in p1.occurrences
        if occurrence.rule_id == "P-15.4.0" and occurrence.kind == "tr"
    ]
    assert len(malformed_rows) == 2
    assert p1.raw[
        malformed_rows[1].document_start_byte : malformed_rows[1].document_end_byte
    ].lower() == b"<tr>"


def test_exact_byte_replay_and_raw_occurrence_uniqueness(
    census: audit.CorpusCensus,
) -> None:
    audit.verify_replay_and_uniqueness(census)
    occurrences = census.occurrences
    assert len(occurrences) == sum(audit.PINNED_COUNTS[tag] for tag in audit.TARGET_TAGS) + 7
    assert len({occurrence.occurrence_id for occurrence in occurrences}) == len(
        occurrences
    )
    assert len(
        {
            (
                occurrence.document_id,
                occurrence.document_start_byte,
                occurrence.document_end_byte,
                occurrence.kind,
            )
            for occurrence in occurrences
        }
    ) == len(occurrences)

    documents = {document.document_id: document for document in census.documents}
    for occurrence in occurrences:
        document = documents[occurrence.document_id]
        exact = document.raw[
            occurrence.document_start_byte : occurrence.document_end_byte
        ]
        assert audit.sha256_bytes(exact) == occurrence.raw_sha256


def test_leading_footnote_candidates_and_correction_images_are_raw_facts(
    census: audit.CorpusCensus,
) -> None:
    documents = {document.document_id: document for document in census.documents}
    candidates = [
        occurrence
        for occurrence in census.occurrences
        if occurrence.kind == "footnote_candidate"
    ]
    candidate_bytes = [
        documents[occurrence.document_id].raw[
            occurrence.document_start_byte : occurrence.document_end_byte
        ]
        for occurrence in candidates
    ]
    assert Counter(candidate_bytes) == {b"*": 5, b"&#134;": 2}
    assert Counter(occurrence.rule_id for occurrence in candidates) == {
        "P-15.5.3.2": 1,
        "P-21.1.1.1": 1,
        "P-25.2.1": 3,
        "P-41": 1,
        "P-101.2.7": 1,
    }

    corrections = [
        occurrence
        for occurrence in census.occurrences
        if occurrence.kind == "img" and occurrence.correction
    ]
    assert len(corrections) == 190
    assert all(occurrence.correction_href for occurrence in corrections)


def test_document_artifact_represents_every_raw_id_and_span(
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts" / "audit_html_physical_occurrences.py"),
            "--artifact",
            str(ARTIFACT),
            "--json",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)

    assert report["counts"] == audit.PINNED_COUNTS
    physical_count = sum(
        audit.PINNED_COUNTS[tag] for tag in audit.TARGET_TAGS
    )
    assert report["artifact_occurrence_count"] == physical_count
    assert (
        report["primary_occurrence_count"]
        + report["aggregate_occurrence_count"]
        == physical_count
    )
    assert report["alias_occurrence_reference_count"] == 190
    assert report["correction_event_count"] == 190
    assert report["footnote_marker_count"] == 7


def test_comment_masking_and_malformed_rows_are_physical_not_dom_counts(
    tmp_path: Path,
) -> None:
    census, _ = _synthetic_census(tmp_path)

    assert census.counts() == {
        "table": 1,
        "tr": 2,
        "td": 2,
        "th": 0,
        "img": 1,
        "correction_img": 0,
        "footnote_candidate": 1,
    }
    assert [
        occurrence.occurrence_id
        for occurrence in census.occurrences
        if occurrence.kind == "tr"
    ] == ["P-1.0:tr:0001", "P-1.0:tr:0002"]


def test_same_length_raw_byte_mutation_is_detected(tmp_path: Path) -> None:
    census, path = _synthetic_census(tmp_path)
    artifact = _minimal_artifact(census)
    expected_counts = census.counts()
    audit.reconcile_artifact(census, artifact, expected_counts)

    path.write_bytes(path.read_bytes().replace(b"x.gif", b"y.gif"))
    mutated = audit.build_census(tmp_path, (("P-1", "P1.html"),))

    assert mutated.counts() == expected_counts
    with pytest.raises(audit.AuditError, match="source digest changed"):
        audit.reconcile_artifact(mutated, artifact, expected_counts)


def test_self_consistent_artifact_id_mutation_is_detected(tmp_path: Path) -> None:
    census, _ = _synthetic_census(tmp_path)
    artifact = _minimal_artifact(census)
    expected_counts = census.counts()
    mutated = copy.deepcopy(artifact)
    image = next(
        node
        for node in mutated["documents"][0]["fragments"][0]["nodes"]
        if node.get("occurrence_id", "").endswith(":img:0001")
    )
    image["occurrence_id"] = "P-1.0:img:9999"
    image["source"]["ownership"]["owner_ref"] = "P-1.0:img:9999"

    with pytest.raises(audit.AuditError, match="physical occurrence ids changed"):
        audit.reconcile_artifact(census, mutated, expected_counts)


def test_self_consistent_artifact_span_and_hash_mutation_is_detected(
    tmp_path: Path,
) -> None:
    census, _ = _synthetic_census(tmp_path)
    artifact = _minimal_artifact(census)
    expected_counts = census.counts()
    mutated = copy.deepcopy(artifact)
    image = next(
        node
        for node in mutated["documents"][0]["fragments"][0]["nodes"]
        if node.get("occurrence_id", "").endswith(":img:0001")
    )
    part = image["source"]["parts"][0]
    part["fragment_end_byte"] -= 1
    part["document_end_byte"] -= 1
    document = census.documents[0]
    part["raw_sha256"] = audit.sha256_bytes(
        document.raw[part["document_start_byte"] : part["document_end_byte"]]
    )
    image["source"]["manifest_sha256"] = audit.sha256_bytes(
        audit.canonical_json_bytes(image["source"]["parts"])
    )

    with pytest.raises(audit.AuditError, match="does not match raw span"):
        audit.reconcile_artifact(census, mutated, expected_counts)


def test_artifact_metric_mutation_is_detected(tmp_path: Path) -> None:
    census, _ = _synthetic_census(tmp_path)
    artifact = _minimal_artifact(census)
    artifact["metrics"]["physical_row_occurrence_count"] += 1

    with pytest.raises(audit.AuditError, match="physical source metrics changed"):
        audit.reconcile_artifact(census, artifact, census.counts())
