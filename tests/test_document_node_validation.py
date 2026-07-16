from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

from scripts import document_node_store
from scripts import validate_pdf_rebuild as validator


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_NODES = document_node_store.DEFAULT_STORE
DOCUMENT_NODE_SCHEMA = ROOT / "data" / "bluebook_document_nodes.schema.json"
SOURCE_CORPUS = ROOT / "data" / "bluebook_v3" / "bluebook_v3_source_corpus.json"


@pytest.fixture(scope="session")
def artifacts() -> dict[str, Any]:
    missing = [
        path
        for path in (DOCUMENT_NODES, DOCUMENT_NODE_SCHEMA, SOURCE_CORPUS)
        if not path.exists()
    ]
    assert not missing, f"Generate the lossless document-node artifacts first: {missing}"
    return {
        "nodes": document_node_store.load_document_nodes(DOCUMENT_NODES),
        "schema": validator.load_json(DOCUMENT_NODE_SCHEMA),
        "source": validator.load_json(SOURCE_CORPUS),
    }


def validate(
    artifacts: dict[str, Any], *, check_schema: bool = False
) -> dict[str, Any]:
    # The positive case performs the expensive full-schema pass once. Mutation
    # cases use a valid permissive schema to focus on the corpus audit checks.
    schema = artifacts["schema"] if check_schema else {}
    return validator.validate_document_node_corpus(
        artifacts["nodes"], schema, artifacts["source"]
    )


def error_codes(result: dict[str, Any]) -> set[str]:
    return {error["code"] for error in result["errors"]}


def find_fragment(
    corpus: dict[str, Any], rule_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    for document in corpus["documents"]:
        for fragment in document["fragments"]:
            if fragment["rule_id"] == rule_id:
                return document, fragment
    raise AssertionError(f"Missing document-node fragment: {rule_id}")


def corpus_digest(corpus: dict[str, Any]) -> str:
    payload = {
        "documents": corpus["documents"],
        "counters": corpus["counters"],
        "metrics": corpus["metrics"],
    }
    return validator.sha256_bytes(validator.canonical_json_bytes(payload))


@contextmanager
def replaced(
    mapping: dict[str, Any], key: str, replacement: Any
) -> Iterator[None]:
    original = mapping[key]
    mapping[key] = replacement
    try:
        yield
    finally:
        mapping[key] = original


@contextmanager
def appended(items: list[Any], item: Any) -> Iterator[None]:
    items.append(item)
    try:
        yield
    finally:
        assert items.pop() is item


def test_document_node_corpus_passes_all_validation_layers(
    artifacts: dict[str, Any],
) -> None:
    result = validate(artifacts, check_schema=True)

    assert result["passed"] is True
    assert result["error_count"] == 0
    assert result["errors"] == []
    metrics = result["metrics"]
    assert {
        key: metrics[key]
        for key in (*artifacts["nodes"]["counters"], *artifacts["nodes"]["metrics"])
    } == {
        **artifacts["nodes"]["counters"],
        **artifacts["nodes"]["metrics"],
    }
    assert metrics["provenance_manifest_count"] == 37_974
    assert metrics["multipart_provenance_manifest_count"] == 15_871
    assert {
        key: metrics[key]
        for key in (
            "field_source_count",
            "field_mapping_count",
            "primary_field_source_count",
            "alias_field_source_count",
            "aggregate_field_source_count",
            "synthetic_field_source_count",
        )
    } == {
        "field_source_count": 38_256,
        "field_mapping_count": 190_762,
        "primary_field_source_count": 25_413,
        "alias_field_source_count": 7_282,
        "aggregate_field_source_count": 0,
        "synthetic_field_source_count": 5_561,
    }
    assert metrics["physical_audit"] == {
        "document_count": 11,
        "fragment_count": 2_554,
        "artifact_occurrence_count": 18_820,
        "primary_occurrence_count": 18_820,
        "aggregate_occurrence_count": 0,
        "alias_occurrence_reference_count": 190,
        "correction_event_count": 190,
        "footnote_marker_count": 7,
        "counts": {
            "table": 567,
            "tr": 3_782,
            "td": 9_100,
            "th": 0,
            "img": 5_371,
            "correction_img": 190,
            "footnote_candidate": 7,
        },
    }


def test_rejects_tampered_source_mapping_and_fragment_hashes(
    artifacts: dict[str, Any],
) -> None:
    corpus = artifacts["nodes"]
    document, fragment = find_fragment(corpus, "P-10")
    fragment_source = fragment["source"]

    with ExitStack() as changes:
        changes.enter_context(replaced(document, "source_sha256", "0" * 64))
        changes.enter_context(replaced(fragment, "anchor", "tampered-anchor"))
        changes.enter_context(replaced(fragment_source, "raw_sha256", "0" * 64))
        changes.enter_context(replaced(fragment_source, "active_sha256", "0" * 64))
        changes.enter_context(
            replaced(corpus, "corpus_sha256", corpus_digest(corpus))
        )

        result = validate(artifacts)

    assert error_codes(result) == {
        "document_nodes.source_manifest",
        "document_nodes.fragment_source",
        "document_nodes.fragment_hash",
        "document_nodes.active_fragment_hash",
        "document_nodes.physical_occurrences",
    }


def test_rejects_source_rule_without_exact_fragment_coverage(
    artifacts: dict[str, Any],
) -> None:
    records = artifacts["source"]["records"]
    unexpected_record = {"source_rule_id": "P-999.999"}

    with appended(records, unexpected_record):
        result = validate(artifacts)

    assert error_codes(result) == {"document_nodes.rule_coverage"}


def test_rejects_fragment_and_document_counter_tampering(
    artifacts: dict[str, Any],
) -> None:
    corpus = artifacts["nodes"]
    document, fragment = find_fragment(corpus, "P-10")

    with ExitStack() as changes:
        changes.enter_context(
            replaced(fragment, "node_count", fragment["node_count"] + 1)
        )
        changes.enter_context(
            replaced(
                document,
                "active_rule_fragment_count",
                document["active_rule_fragment_count"] + 1,
            )
        )
        changes.enter_context(
            replaced(
                document,
                "document_node_count",
                document["document_node_count"] + 1,
            )
        )
        changes.enter_context(
            replaced(corpus, "corpus_sha256", corpus_digest(corpus))
        )

        result = validate(artifacts)

    assert error_codes(result) == {
        "document_nodes.fragment_counter",
        "document_nodes.document_fragment_counter",
        "document_nodes.document_node_counter",
    }


def test_rejects_provenance_part_hash_range_and_manifest_tampering(
    artifacts: dict[str, Any],
) -> None:
    corpus = artifacts["nodes"]
    _, fragment = find_fragment(corpus, "P-10")
    manifest_target, hash_target, range_target = list(
        validator.iter_provenance_objects(fragment["nodes"])
    )[:3]
    hash_part = hash_target["parts"][0]
    range_part = range_target["parts"][0]

    with ExitStack() as changes:
        changes.enter_context(
            replaced(manifest_target, "manifest_sha256", "0" * 64)
        )
        changes.enter_context(replaced(hash_part, "raw_sha256", "0" * 64))
        changes.enter_context(
            replaced(
                hash_target,
                "manifest_sha256",
                validator.sha256_bytes(
                    validator.canonical_json_bytes(hash_target["parts"])
                ),
            )
        )
        changes.enter_context(
            replaced(
                range_part,
                "fragment_end_byte",
                range_part["fragment_start_byte"],
            )
        )
        changes.enter_context(
            replaced(
                range_target,
                "manifest_sha256",
                validator.sha256_bytes(
                    validator.canonical_json_bytes(range_target["parts"])
                ),
            )
        )
        changes.enter_context(
            replaced(corpus, "corpus_sha256", corpus_digest(corpus))
        )

        result = validate(artifacts)

    assert error_codes(result) == {
        "document_nodes.provenance_manifest",
        "document_nodes.provenance_hash",
        "document_nodes.provenance_range",
    }


def test_rejects_corpus_counter_and_digest_tampering(
    artifacts: dict[str, Any],
) -> None:
    corpus = artifacts["nodes"]
    counters = corpus["counters"]

    with ExitStack() as changes:
        changes.enter_context(
            replaced(
                counters,
                "document_node_count",
                counters["document_node_count"] + 1,
            )
        )
        changes.enter_context(replaced(corpus, "corpus_sha256", "0" * 64))

        result = validate(artifacts)

    assert error_codes(result) == {
        "document_nodes.counters",
        "document_nodes.corpus_hash",
    }


def test_source_invariants_cannot_be_rebased_to_tampered_nodes(
    artifacts: dict[str, Any],
) -> None:
    corpus = artifacts["nodes"]
    document, fragment = find_fragment(corpus, "P-10")
    node = next(item for item in fragment["nodes"] if item["kind"] == "paragraph")
    document_counts = document["node_kind_counts"]
    corpus_counts = corpus["counters"]["node_kind_counts"]

    with ExitStack() as changes:
        changes.enter_context(replaced(node, "kind", "prose"))
        changes.enter_context(
            replaced(
                document_counts, "paragraph", document_counts["paragraph"] - 1
            )
        )
        changes.enter_context(
            replaced(document_counts, "prose", document_counts["prose"] + 1)
        )
        changes.enter_context(
            replaced(corpus_counts, "paragraph", corpus_counts["paragraph"] - 1)
        )
        changes.enter_context(
            replaced(corpus_counts, "prose", corpus_counts["prose"] + 1)
        )
        changes.enter_context(
            replaced(corpus, "corpus_sha256", corpus_digest(corpus))
        )

        result = validate(artifacts)

    assert error_codes(result) == {"document_nodes.source_invariants"}
