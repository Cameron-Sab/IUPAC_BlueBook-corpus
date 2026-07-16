from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from collections import Counter
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.extract_correction_overlays import (
    HEADER_RE,
    canonical_json_bytes,
    extract_correction_overlays,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".cache" / "bluebook_html" / "changes2.html"
SCHEMA = ROOT / "data" / "bluebook_correction_overlay.schema.json"


@pytest.fixture(scope="module")
def corpus() -> dict:
    return extract_correction_overlays(SOURCE)


def find_record(corpus: dict, selector_prefix: str) -> dict:
    return next(
        record
        for record in corpus["records"]
        if record["target"]["selector_text"].startswith(selector_prefix)
    )


def test_all_dated_source_entries_are_extracted_and_schema_valid(corpus: dict):
    source_text = SOURCE.read_text(encoding="utf-8")
    source_entry_count = len(list(HEADER_RE.finditer(source_text)))

    assert source_entry_count == 90
    assert corpus["record_count"] == source_entry_count
    assert len(corpus["records"]) == source_entry_count

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(corpus),
        key=lambda error: list(error.absolute_path),
    )
    assert errors == []


def test_counters_and_identifiers_match_the_records(corpus: dict):
    records = corpus["records"]
    assert len({record["overlay_id"] for record in records}) == len(records)

    status_counts = Counter(record["status"] for record in records)
    event_counts = Counter(
        event["event_type"] for record in records for event in record["events"]
    )
    operation_counts = Counter(
        operation["kind"] for record in records for operation in record["operations"]
    )
    selector_counts = Counter(
        selector["kind"]
        for record in records
        for selector in record["target"]["selectors"]
    )

    for name, count in corpus["counters"]["status"].items():
        assert count == status_counts[name]
    for name, count in corpus["counters"]["event_type"].items():
        assert count == event_counts[name]
    for name, count in corpus["counters"]["operation_kind"].items():
        assert count == operation_counts[name]
    for name, count in corpus["counters"]["selector_kind"].items():
        assert count == selector_counts[name]


def test_effective_dates_and_target_selectors_are_typed(corpus: dict):
    revised = find_record(corpus, "P-44.4.1.2 example (4), structure 1.")
    assert revised["effective_date"] == "2026-01-22"
    assert revised["events"] == [
        {
            "effective_date": "2025-12-03",
            "event_type": "corrected",
            "source_text": "corrected 3 December 2025",
        },
        {
            "effective_date": "2026-01-22",
            "event_type": "modified",
            "source_text": "modified 22.1.2026",
        },
    ]

    page_target = find_record(corpus, "Page 345, P-28.4.1")
    assert {selector["kind"] for selector in page_target["target"]["selectors"]} >= {
        "page",
        "rule",
    }
    assert next(
        selector["page"]
        for selector in page_target["target"]["selectors"]
        if selector["kind"] == "page"
    ) == 345

    table_target = find_record(corpus, "P-32.4, Table 3.2")
    assert {"kind": "table", "label": "3.2"} in table_target["target"]["selectors"]

    figure_target = find_record(corpus, "P-15.1.1, Fig. 1.1")
    assert {"kind": "figure", "label": "1.1"} in figure_target["target"]["selectors"]

    appendix_target = find_record(corpus, "Appendix 2, heptanylidene")
    assert {"kind": "appendix", "number": 2} in appendix_target["target"]["selectors"]


def test_operations_preserve_replacements_additions_deletions_and_source_instructions(corpus: dict):
    replacement = find_record(corpus, "P-13.3.5")
    assert replacement["status"] == "replaced"
    assert replacement["operations"][0]["before_text"] == "carbon monoxide\u2014methylborane (PIN)"
    assert replacement["operations"][0]["after_text"] == "carbon monoxide\u2014methylborane (1/1) (PIN)"

    addition = find_record(corpus, "P-31.1.6.1 before examples")
    assert addition["status"] == "applied"
    assert addition["operations"][0]["kind"] == "addition"
    assert addition["operations"][0]["after_text"] == "This is a change from PhII-5.3.2."

    deletion = find_record(corpus, "P-65.7.8.")
    assert deletion["status"] == "deleted"
    assert len(deletion["operations"]) == 1
    assert deletion["operations"][0]["kind"] == "deletion"
    assert deletion["operations"][0]["before_text"] == (
        "this section (conflict with P-65.7.1)"
    )

    instruction = find_record(corpus, "P-31.1.4.3.")
    assert instruction["status"] == "applied"
    assert instruction["operations"][0]["kind"] == "instruction"
    assert instruction["operations"][0]["instruction_text"] == (
        "The order of the three criteria changed to make minimum number of compound locants first."
    )
    assert instruction["operations"][0]["source_html"].strip().startswith("<br>")


def test_exact_source_ranges_hashes_and_dom_paths_round_trip(corpus: dict):
    source_bytes = SOURCE.read_bytes()
    dom_paths = set()

    for record in corpus["records"]:
        provenance = record["provenance"]
        fragment = source_bytes[
            provenance["source_byte_start"] : provenance["source_byte_end"]
        ]
        assert fragment.decode("utf-8") == record["source_html"]
        assert hashlib.sha256(fragment).hexdigest().upper() == provenance["fragment_sha256"]
        assert provenance["source_dom_path"] not in dom_paths
        dom_paths.add(provenance["source_dom_path"])

        for operation in record["operations"]:
            operation_provenance = operation["provenance"]
            operation_fragment = source_bytes[
                operation_provenance["source_byte_start"] : operation_provenance[
                    "source_byte_end"
                ]
            ]
            assert operation_fragment.decode("utf-8") == operation["source_html"]
            assert (
                hashlib.sha256(operation_fragment).hexdigest().upper()
                == operation_provenance["fragment_sha256"]
            )
            assert operation_provenance["source_dom_path"] not in dom_paths
            dom_paths.add(operation_provenance["source_dom_path"])


def test_assets_and_cross_references_are_first_class(corpus: dict):
    digraph = find_record(corpus, "P-92.6")
    assert digraph["operations"][0]["assets"] == [
        {
            "kind": "image",
            "source_path": "../bibliog/bibgif/92.6d.gif",
            "source_url": "https://iupac.qmul.ac.uk/bibliog/bibgif/92.6d.gif",
            "width": 268,
        }
    ]

    historical = find_record(corpus, "P-68.1.6.3 (was P-68.1.6.1.2)")
    assert any(
        reference["relation"] == "renamed_from"
        and reference["target"] == "P-68.1.6.1.2"
        for reference in historical["references"]
    )

    conflict = find_record(corpus, "P-65.7.8.")
    assert any(
        reference["relation"] == "conflicts_with"
        and reference["target"] == "P-65.7.1"
        for reference in conflict["references"]
    )

    see_also = find_record(corpus, "P-31.1.1.1")
    assert any(
        reference["relation"] == "see_also" and reference["target"] == "P-91.2.2"
        for reference in see_also["references"]
    )


def test_output_is_canonical_and_contains_no_placeholder_semantics(corpus: dict):
    encoded = canonical_json_bytes(corpus)
    assert encoded.endswith(b"\n")
    assert json.loads(encoded) == corpus

    serialized = encoded.decode("utf-8").lower()
    for forbidden in ("todo", "unresolved", "placeholder"):
        assert forbidden not in serialized


def test_schema_rejects_corrupted_overlay_semantics(corpus: dict):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    bad_status = deepcopy(corpus)
    bad_status["records"][0]["status"] = "unresolved"

    missing_source = deepcopy(corpus)
    del missing_source["records"][0]["operations"][0]["source_html"]

    forged_hash = deepcopy(corpus)
    forged_hash["records"][0]["provenance"]["fragment_sha256"] = "0" * 63

    unexpected_field = deepcopy(corpus)
    unexpected_field["records"][0]["semantic_guess"] = True

    for mutant in (bad_status, missing_source, forged_hash, unexpected_field):
        assert list(validator.iter_errors(mutant))
