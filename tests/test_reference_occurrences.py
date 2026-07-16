from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.extract_reference_occurrences import (
    COMMENT_RE,
    DEFAULT_CACHE_DIR,
    canonical_json_bytes,
    extract_reference_occurrences,
    sha256_bytes,
)
from scripts import validate_pdf_rebuild as rebuild_validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "data" / "bluebook_reference_occurrences.schema.json"
OFFICIAL_ARTIFACT = (
    ROOT / "data" / "bluebook_v3" / "bluebook_v3_reference_occurrences.json"
)
SOURCE_CORPUS = ROOT / "data" / "bluebook_v3" / "bluebook_v3_source_corpus.json"
SYNTHETIC_CHAPTERS = (("P-1", "P1.html"), ("P-2", "P2.html"))


@pytest.fixture(scope="module")
def synthetic_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    cache = tmp_path_factory.mktemp("reference-occurrences")
    p1 = (
        b"<html><head><base href=\"https://example.test/BlueBook/P1.html\"></head>"
        b"<body>\r\n"
        b"<!-- <a name=\"ghost\"><b>P-9.9</b></a> Hidden P-8.8. -->\r\n"
        b"<a name=\"10\"><b>P-1</b></a> Caf\xc3\xa9 references "
        b"<a href=\"P2.html#210\">P-2.1</a> and plain P-2.1. "
        b"Use <a href='P2.html#220'><i>the next rule</i></a>; missing P-404. "
        b"Chapter <a href=P2.html>P-2</a>. "
        b"Malformed <a href=\"P2.html#230\">P-2.3<a> then plain P-2.1.\r\n"
        b"<a name=\"110\"><b>P-1.1</b></a> See P-2.1 and "
        b"<a href=\"P2.html#dupe\">P-2.4</a>. "
        b"<!-- Inactive P-7.7 remains source history. -->\r\n"
        b"<hr>ignored P-6.6</body></html>"
    )
    p2 = (
        b"<html><head><base href=\"https://example.test/BlueBook/P2.html\"></head>"
        b"<body>\n"
        b"<a name=\"20\"><b>P-2</b></a> Chapter root.\n"
        b"<a name=\"210\"><b>P-2.1</b></a> First target.\n"
        b"<a name=\"220\"><b>P-2.2</b></a> Href-only target.\n"
        b"<a name=\"230\"><b>P-2.3</b></a> Malformed-link target.\n"
        b"<a name=\"dupe\"><b>P-2.4</b></a> Matching duplicate anchor.\n"
        b"<a name=\"dupe\"><b>P-2.5</b></a> Other duplicate anchor.\n"
        b"</body></html>"
    )
    (cache / "P1.html").write_bytes(p1)
    (cache / "P2.html").write_bytes(p2)
    return cache


@pytest.fixture(scope="module")
def synthetic_generations(
    synthetic_cache: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    arguments = {
        "cache_dir": synthetic_cache,
        "document_ids": {"P-1"},
        "chapter_files": SYNTHETIC_CHAPTERS,
        "context_characters": 48,
    }
    return (
        extract_reference_occurrences(**arguments),
        extract_reference_occurrences(**arguments),
    )


@pytest.fixture(scope="module")
def synthetic_corpus(
    synthetic_generations: tuple[dict[str, Any], dict[str, Any]],
) -> dict[str, Any]:
    return synthetic_generations[0]


@pytest.fixture(scope="module")
def real_p1_corpus() -> dict[str, Any]:
    if not (DEFAULT_CACHE_DIR / "P1.html").exists():
        pytest.skip("official Blue Book HTML cache is not available")
    return extract_reference_occurrences(DEFAULT_CACHE_DIR, {"P-1"})


@pytest.fixture(scope="module")
def official_artifacts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    missing = [path for path in (OFFICIAL_ARTIFACT, SOURCE_CORPUS) if not path.exists()]
    if missing:
        pytest.skip(f"Generated official reference artifacts are absent: {missing}")
    return (
        json.loads(OFFICIAL_ARTIFACT.read_text(encoding="utf-8")),
        json.loads(SCHEMA.read_text(encoding="utf-8")),
        json.loads(SOURCE_CORPUS.read_text(encoding="utf-8")),
    )


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _artifact_map(corpus: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        artifact["artifact_id"]: artifact for artifact in corpus["source_artifacts"]
    }


def _digest_payload(corpus: dict[str, Any]) -> dict[str, Any]:
    return {
        "context_characters": corpus["context_characters"],
        "source_document_ids": corpus["source_document_ids"],
        "source_artifact_manifest_sha256": corpus[
            "source_artifact_manifest_sha256"
        ],
        "source_artifacts": corpus["source_artifacts"],
        "counters": corpus["counters"],
        "occurrences": corpus["occurrences"],
    }


def test_synthetic_output_is_schema_valid_deterministic_and_censused(
    synthetic_generations: tuple[dict[str, Any], dict[str, Any]],
):
    first, second = synthetic_generations
    _schema_validator().validate(first)

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first["source_document_ids"] == ["P-1"]
    assert first["counters"] == {
        "source_artifact_count": 2,
        "source_document_count": 1,
        "indexed_active_rule_fragment_count": 8,
        "source_active_rule_fragment_count": 2,
        "reference_occurrence_count": 9,
        "reference_kind_counts": {"href": 5, "text": 4},
        "target_resolution_counts": {
            "active_rule": 7,
            "document": 1,
            "unresolved": 1,
        },
        "distinct_source_rule_count": 2,
        "distinct_target_rule_count": 6,
    }
    assert first["source_artifact_manifest_sha256"] == sha256_bytes(
        canonical_json_bytes(first["source_artifacts"])
    )
    assert first["corpus_sha256"] == sha256_bytes(
        canonical_json_bytes(_digest_payload(first))
    )


def test_synthetic_links_text_and_duplicate_anchors_are_distinct_occurrences(
    synthetic_corpus: dict[str, Any],
):
    occurrences = synthetic_corpus["occurrences"]
    keys = [
        (
            item["source"]["span"]["document_start_byte"],
            item["source"]["span"]["document_end_byte"],
            item["target"]["rule_id"],
        )
        for item in occurrences
    ]
    assert len(keys) == len(set(keys))

    cited_p21 = [item for item in occurrences if item["cited_rule_id"] == "P-2.1"]
    assert len(cited_p21) == 4
    assert [item["reference_kind"] for item in cited_p21].count("href") == 1
    assert [item["reference_kind"] for item in cited_p21].count("text") == 3

    href_only = next(item for item in occurrences if item["cited_rule_id"] is None)
    assert href_only["reference_text"] == "the next rule"
    assert href_only["target"]["rule_id"] == "P-2.2"
    assert href_only["target"]["resolution_basis"] == "href_anchor"

    ambiguous = next(
        item for item in occurrences if item["cited_rule_id"] == "P-2.4"
    )
    assert ambiguous["href"]["target_anchor"] == "dupe"
    assert ambiguous["href"]["target_rule_id"] == "P-2.4"
    assert ambiguous["href"]["cited_target_match"] is True
    assert ambiguous["target"]["active_fragment"]["anchor"] == "dupe"

    malformed = next(
        item
        for item in occurrences
        if item["cited_rule_id"] == "P-2.3"
    )
    assert malformed["reference_kind"] == "href"
    following_plain = next(
        item
        for item in cited_p21
        if "then plain" in item["context"]["text"]
    )
    assert following_plain["reference_kind"] == "text"


def test_synthetic_resolution_preserves_documents_and_unresolved_targets(
    synthetic_corpus: dict[str, Any],
):
    occurrences = synthetic_corpus["occurrences"]
    chapter = next(item for item in occurrences if item["cited_rule_id"] == "P-2")
    assert chapter["target"]["resolution"] == "document"
    assert chapter["target"]["resolution_basis"] == "href_document"
    assert chapter["target"]["document"]["document_id"] == "P-2"
    assert chapter["target"]["active_fragment"] is None

    missing = next(item for item in occurrences if item["cited_rule_id"] == "P-404")
    assert missing["reference_kind"] == "text"
    assert missing["target"] == {
        "rule_id": "P-404",
        "resolution": "unresolved",
        "resolution_basis": "reference_text",
        "document": None,
        "active_fragment": None,
    }

    cited_ids = {item["cited_rule_id"] for item in occurrences}
    assert cited_ids.isdisjoint({"P-1", "P-1.1", "P-9.9", "P-8.8", "P-7.7"})
    assert {item["source_rule_id"] for item in occurrences} == {"P-1", "P-1.1"}


def test_every_synthetic_span_digest_context_and_artifact_hash_replays(
    synthetic_cache: Path, synthetic_corpus: dict[str, Any]
):
    artifacts = _artifact_map(synthetic_corpus)
    raw_by_artifact = {
        artifact_id: (synthetic_cache / metadata["cache_path"]).read_bytes()
        for artifact_id, metadata in artifacts.items()
    }

    for occurrence in synthetic_corpus["occurrences"]:
        source = occurrence["source"]
        artifact = artifacts[source["artifact_id"]]
        raw = raw_by_artifact[source["artifact_id"]]
        assert sha256_bytes(raw) == artifact["source_sha256"]
        assert source["source_artifact_sha256"] == artifact["source_sha256"]

        fragment = source["fragment"]
        fragment_bytes = raw[
            fragment["document_start_byte"] : fragment["document_end_byte"]
        ]
        assert sha256_bytes(fragment_bytes) == fragment["raw_sha256"]
        assert sha256_bytes(COMMENT_RE.sub(b"", fragment_bytes)) == fragment[
            "active_sha256"
        ]

        span = source["span"]
        exact = raw[span["document_start_byte"] : span["document_end_byte"]]
        assert exact == fragment_bytes[
            span["fragment_start_byte"] : span["fragment_end_byte"]
        ]
        assert sha256_bytes(exact) == span["raw_sha256"]

        href = occurrence["href"]
        if href is not None:
            href_span = href["source"]
            href_bytes = raw[
                href_span["document_start_byte"] : href_span["document_end_byte"]
            ]
            assert sha256_bytes(href_bytes) == href_span["raw_sha256"]
            assert href_bytes.decode("utf-8") == href["value"]

        context = occurrence["context"]
        assert sha256_bytes(context["text"].encode("utf-8")) == context[
            "text_sha256"
        ]
        if context["reference_start"] is not None:
            focused = context["text"][
                context["reference_start"] : context["reference_end"]
            ]
            assert focused == occurrence["reference_text"]


def test_schema_rejects_forged_or_incoherent_occurrences(
    synthetic_corpus: dict[str, Any],
):
    validator = _schema_validator()

    forged_hash = deepcopy(synthetic_corpus)
    forged_hash["occurrences"][0]["source"]["span"]["raw_sha256"] = "0" * 63

    text_with_href = deepcopy(synthetic_corpus)
    text_occurrence = next(
        item
        for item in text_with_href["occurrences"]
        if item["reference_kind"] == "text"
    )
    text_occurrence["href"] = deepcopy(
        next(
            item["href"]
            for item in text_with_href["occurrences"]
            if item["href"] is not None
        )
    )

    unexpected = deepcopy(synthetic_corpus)
    unexpected["occurrences"][0]["guessed_relation"] = "see_also"

    missing_binding = deepcopy(synthetic_corpus)
    del missing_binding["occurrences"][0]["source"]["source_artifact_sha256"]

    for mutant in (forged_hash, text_with_href, unexpected, missing_binding):
        assert list(validator.iter_errors(mutant))


def test_real_p1_census_resolution_and_plain_text_coverage(
    real_p1_corpus: dict[str, Any],
):
    _schema_validator().validate(real_p1_corpus)
    assert real_p1_corpus["counters"] == {
        "source_artifact_count": 11,
        "source_document_count": 1,
        "indexed_active_rule_fragment_count": 2554,
        "source_active_rule_fragment_count": 285,
        "reference_occurrence_count": 661,
        "reference_kind_counts": {"href": 658, "text": 3},
        "target_resolution_counts": {
            "active_rule": 634,
            "document": 26,
            "unresolved": 1,
        },
        "distinct_source_rule_count": 174,
        "distinct_target_rule_count": 417,
    }

    chapter_link = real_p1_corpus["occurrences"][0]
    assert (
        chapter_link["source_rule_id"],
        chapter_link["reference_text"],
        chapter_link["target"]["resolution"],
        chapter_link["target"]["document"]["document_id"],
    ) == ("P-11", "P-10", "document", "P-10")

    p69 = next(
        item
        for item in real_p1_corpus["occurrences"]
        if item["source_rule_id"] == "P-11" and item["target"]["rule_id"] == "P-69"
    )
    assert p69["target"]["resolution"] == "active_rule"
    assert p69["target"]["document"]["document_id"] == "P-6b"
    assert p69["href"]["cited_target_match"] is True

    plain = [
        (item["source_rule_id"], item["cited_rule_id"])
        for item in real_p1_corpus["occurrences"]
        if item["reference_kind"] == "text"
    ]
    assert plain == [
        ("P-11", "P-2"),
        ("P-15.1.7.2", "P-14.4"),
        ("P-15.1.8.2", "P-67"),
    ]


def test_real_p1_occurrence_spans_are_unique_and_replay_official_bytes(
    real_p1_corpus: dict[str, Any],
):
    artifacts = _artifact_map(real_p1_corpus)
    seen: set[tuple[str, int, int, str]] = set()
    for occurrence in real_p1_corpus["occurrences"]:
        source = occurrence["source"]
        artifact = artifacts[source["artifact_id"]]
        raw = (DEFAULT_CACHE_DIR / artifact["cache_path"]).read_bytes()
        span = source["span"]
        key = (
            source["artifact_id"],
            span["document_start_byte"],
            span["document_end_byte"],
            occurrence["target"]["rule_id"],
        )
        assert key not in seen
        seen.add(key)

        exact = raw[span["document_start_byte"] : span["document_end_byte"]]
        assert hashlib.sha256(exact).hexdigest().upper() == span["raw_sha256"]
        assert sha256_bytes(raw) == source["source_artifact_sha256"]
        assert (
            span["document_start_byte"]
            == source["fragment"]["document_start_byte"]
            + span["fragment_start_byte"]
        )
        assert (
            span["document_end_byte"]
            == source["fragment"]["document_start_byte"]
            + span["fragment_end_byte"]
        )


def test_official_artifact_passes_release_gate_and_exact_source_replay(
    official_artifacts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    corpus, schema, source = official_artifacts

    result = rebuild_validator.validate_reference_occurrence_corpus(
        corpus, schema, source
    )

    assert result["passed"] is True
    assert result["errors"] == []
    assert result["metrics"]["reference_occurrence_count"] == 4_023
    assert result["metrics"]["target_resolution_counts"] == {
        "active_rule": 3_896,
        "document": 124,
        "unresolved": 3,
    }


def test_release_gate_rejects_self_consistently_rehashed_reference_mutation(
    official_artifacts: tuple[dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    corpus, schema, source = official_artifacts
    mutant = deepcopy(corpus)
    mutant["occurrences"][0]["target"]["rule_id"] = "P-11"
    mutant["corpus_sha256"] = sha256_bytes(
        canonical_json_bytes(_digest_payload(mutant))
    )

    result = rebuild_validator.validate_reference_occurrence_corpus(
        mutant, schema, source
    )

    assert result["passed"] is False
    assert "references.source_replay" in {
        error["code"] for error in result["errors"]
    }
    assert "references.corpus_hash" not in {
        error["code"] for error in result["errors"]
    }
