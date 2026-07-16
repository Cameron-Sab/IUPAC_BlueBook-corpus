from __future__ import annotations

import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts.extract_html_document_nodes import (
    CHAPTER_FILES,
    NODE_KINDS,
    canonical_json_bytes,
    extract_corpus,
    sha256_bytes,
    validate_fragment_field_sources,
)


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "bluebook_html"
SCHEMA = ROOT / "data" / "bluebook_document_nodes.schema.json"

COMMENT_RE = re.compile(rb"<!--.*?-->", re.DOTALL)
EXAMPLE_LABEL_RE = re.compile(
    r"^Examples?(?:\s+\d+)?(?:\s*\([^\n]*\))?(?:[:.]|$)", re.IGNORECASE
)
NOTE_LABEL_RE = re.compile(r"^Notes?(?:\s+\d+)?(?:[:.]|$)", re.IGNORECASE)


@pytest.fixture(scope="module")
def corpus() -> dict[str, Any]:
    return extract_corpus(CACHE)


def document(corpus: dict[str, Any], document_id: str) -> dict[str, Any]:
    return next(item for item in corpus["documents"] if item["document_id"] == document_id)


def fragment(corpus: dict[str, Any], rule_id: str) -> dict[str, Any]:
    return next(
        item
        for source_document in corpus["documents"]
        for item in source_document["fragments"]
        if item["rule_id"] == rule_id
    )


def walk_nodes(nodes: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for node in nodes:
        yield node
        yield from walk_nodes(node.get("children", []))


def corpus_nodes(corpus: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for source_document in corpus["documents"]:
        for source_fragment in source_document["fragments"]:
            yield from walk_nodes(source_fragment["nodes"])


def provenance_objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if set(value) == {"parts", "manifest_sha256", "ownership"}:
            yield value
            return
        for child in value.values():
            yield from provenance_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from provenance_objects(child)


def test_full_corpus_is_schema_valid_and_two_generations_are_byte_identical(
    corpus: dict[str, Any],
):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(corpus)

    for document_id, _ in CHAPTER_FILES:
        regenerated = extract_corpus(CACHE, {document_id})["documents"][0]
        assert canonical_json_bytes(regenerated) == canonical_json_bytes(
            document(corpus, document_id)
        )

    assert corpus["version"] == "2.0.0"
    assert [item["document_id"] for item in corpus["documents"]] == [
        document_id for document_id, _ in CHAPTER_FILES
    ]
    assert [item["cache_path"] for item in corpus["documents"]] == [
        filename for _, filename in CHAPTER_FILES
    ]
    assert corpus["counters"] == {
        "document_count": 11,
        "active_rule_fragment_count": 2554,
        "document_node_count": 14453,
        "node_kind_counts": {
            "heading": 2554,
            "paragraph": 3174,
            "prose": 1153,
            "list_item": 845,
            "table": 567,
            "figure": 4130,
            "example_block": 1745,
            "note": 103,
            "caption": 9,
            "source_event": 168,
            "footnote": 3,
            "orphan_cell": 2,
        },
    }
    assert tuple(corpus["counters"]["node_kind_counts"]) == NODE_KINDS


def test_every_declared_source_part_replays_exact_cached_bytes(corpus: dict[str, Any]):
    provenance_count = 0
    multipart_count = 0
    for source_document in corpus["documents"]:
        raw = (CACHE / source_document["cache_path"]).read_bytes()
        assert len(raw) == source_document["source_byte_count"]
        assert sha256_bytes(raw) == source_document["source_sha256"]
        comment_ranges = [match.span() for match in COMMENT_RE.finditer(raw)]

        source_fragments = source_document["fragments"]
        assert all(
            left["source"]["end_byte"] == right["source"]["start_byte"]
            for left, right in zip(source_fragments, source_fragments[1:])
        )
        for source_fragment in source_fragments:
            fragment_source = source_fragment["source"]
            fragment_start = fragment_source["start_byte"]
            fragment_end = fragment_source["end_byte"]
            raw_fragment = raw[fragment_start:fragment_end]
            assert sha256_bytes(raw_fragment) == fragment_source["raw_sha256"]
            assert sha256_bytes(COMMENT_RE.sub(b"", raw_fragment)) == fragment_source[
                "active_sha256"
            ]

            for provenance in provenance_objects(source_fragment["nodes"]):
                provenance_count += 1
                parts = provenance["parts"]
                multipart_count += len(parts) > 1
                assert provenance["manifest_sha256"] == sha256_bytes(
                    canonical_json_bytes(parts)
                )
                assert parts == sorted(
                    parts,
                    key=lambda part: (
                        part["document_start_byte"],
                        part["document_end_byte"],
                        part["dom_path"],
                    ),
                )
                for part in parts:
                    fragment_part_start = part["fragment_start_byte"]
                    fragment_part_end = part["fragment_end_byte"]
                    document_part_start = part["document_start_byte"]
                    document_part_end = part["document_end_byte"]
                    assert 0 <= fragment_part_start < fragment_part_end <= len(raw_fragment)
                    assert fragment_start <= document_part_start < document_part_end <= fragment_end
                    assert document_part_start == fragment_start + fragment_part_start
                    assert document_part_end == fragment_start + fragment_part_end
                    exact = raw[document_part_start:document_part_end]
                    assert exact == raw_fragment[fragment_part_start:fragment_part_end]
                    assert sha256_bytes(exact) == part["raw_sha256"]
                    assert not any(
                        comment_start < document_part_end
                        and document_part_start < comment_end
                        for comment_start, comment_end in comment_ranges
                    )

    assert provenance_count > 20_000
    assert multipart_count > 1_000


def test_comments_remain_history_and_never_become_active_nodes(corpus: dict[str, Any]):
    p6 = document(corpus, "P-6a")
    rule_ids = {item["rule_id"] for item in p6["fragments"]}
    raw = (CACHE / p6["cache_path"]).read_bytes()
    deleted_history = next(
        match.group(0)
        for match in COMMENT_RE.finditer(raw)
        if b"P-65.7.8" in match.group(0)
    )

    assert b"Polyfunctional anhydrides" in deleted_history
    assert "P-65.7.8" not in rule_ids
    assert "P-65.7.7.3" in rule_ids
    active_icon_count = sum(
        COMMENT_RE.sub(b"", (CACHE / filename).read_bytes()).lower().count(b"alter.gif")
        for _, filename in CHAPTER_FILES
    )
    assert active_icon_count == 190


def test_example_grammar_is_exhaustive_and_labels_are_preserved(corpus: dict[str, Any]):
    nodes = list(corpus_nodes(corpus))
    examples = [node for node in nodes if node["kind"] == "example_block"]
    numbered = [node for node in examples if node["number"] is not None]
    punctuationless = [
        node for node in examples if node["label"][-1:] not in {":", "."}
    ]

    assert len(examples) == 1745
    assert len(numbered) == 104
    assert len(punctuationless) == 6
    assert all(EXAMPLE_LABEL_RE.fullmatch(node["label"]) for node in examples)
    assert all(node["children"] for node in examples)
    assert all(
        node["number"] == int(re.search(r"\d+", node["label"]).group(0))
        for node in numbered
    )
    assert not [
        node
        for node in nodes
        if node["kind"] in {"paragraph", "prose", "list_item"}
        and EXAMPLE_LABEL_RE.match(node["text"])
    ]


def test_named_example_scope_stress_cases(corpus: dict[str, Any]):
    p15 = fragment(corpus, "P-15.6.3")["nodes"]
    p15_examples = [node for node in p15 if node["kind"] == "example_block"]
    assert [node["label"] for node in p15_examples] == ["Example 1:", "Example 2:"]
    assert [len(node["children"]) for node in p15_examples] == [5, 4]
    assert p15[1]["kind"] == "note"

    p25 = fragment(corpus, "P-25.3.2.4")["nodes"]
    scoped = p25[3:]
    assert [node["kind"] for node in scoped] == ["list_item", "example_block"] * 10
    assert [node["marker"] for node in scoped[::2]] == [
        "(a)", "(b)", "(c)", "(d)", "(e)", "(f)", "(g)", "(h)", "(i)", "(j)"
    ]
    assert scoped[5]["label"] == "Examples"
    assert len(scoped[5]["children"]) == 2

    p92_examples = [
        node
        for node in fragment(corpus, "P-92.5.2.2")["nodes"]
        if node["kind"] == "example_block"
    ]
    assert [node["label"] for node in p92_examples] == [
        "Example 1:",
        "Example 2:",
        "Example 3.",
        "Example 4:",
        "Example 5:",
        "Example 6:",
    ]
    assert [node["number"] for node in p92_examples] == [1, 2, 3, 4, 5, 6]

    p93 = fragment(corpus, "P-93.5.1.4.2.2")["nodes"]
    p93_examples = [node for node in p93 if node["kind"] == "example_block"]
    assert [node["number"] for node in p93_examples] == [1, 2, 3, 4]
    assert [node["marker"] for node in p93 if node["kind"] == "list_item"] == [
        "(1)", "(2)"
    ]
    assert p93[-1]["kind"] == "note"


def test_notes_include_numbered_forms_and_split_cleanly_from_figures(
    corpus: dict[str, Any],
):
    nodes = list(corpus_nodes(corpus))
    notes = [node for node in nodes if node["kind"] == "note"]
    assert Counter(node["label"] for node in notes) == {
        "Note:": 91,
        "Note 1:": 6,
        "Note 2:": 5,
        "Note 3:": 1,
    }
    assert all(NOTE_LABEL_RE.fullmatch(node["label"]) for node in notes)
    assert all("children" in node for node in notes)
    assert not [
        node
        for node in nodes
        if node["kind"] in {"paragraph", "prose", "list_item"}
        and NOTE_LABEL_RE.match(node["text"])
    ]

    p24 = fragment(corpus, "P-24.3.3")["nodes"]
    assert [node["kind"] for node in p24] == [
        "heading",
        "example_block",
        "note",
        "figure",
        "note",
        "note",
        "figure",
    ]
    assert [node["label"] for node in p24 if node["kind"] == "note"] == [
        "Note:", "Note 1:", "Note 2:"
    ]

    p91 = fragment(corpus, "P-91.3")["nodes"]
    last_example = [node for node in p91 if node["kind"] == "example_block"][-1]
    assert [child["kind"] for child in last_example["children"]] == ["figure"] * 4
    assert p91[-1]["kind"] == "note"
    assert p91[-1]["label"] == "Note:"
    assert "stereodescriptor" in p91[-1]["text"]
    assert "Note:" not in (last_example["children"][-1]["caption"] or "")


def test_table_roles_and_visible_caption_census(corpus: dict[str, Any]):
    nodes = list(corpus_nodes(corpus))
    tables = [node for node in nodes if node["kind"] == "table"]
    roles = Counter(node["table_role"] for node in tables)
    assert roles == {
        "example_layout": 289,
        "callout": 142,
        "figure_layout": 64,
        "uncaptioned_mapping": 38,
        "captioned_semantic": 32,
        "layout": 2,
    }
    assert all(
        (node["caption_kind"] == "table") == (node["table_role"] == "captioned_semantic")
        for node in tables
    )

    visible = [
        node
        for node in nodes
        if node["kind"] in {"table", "figure", "caption"}
        and node.get("caption_label")
    ]
    table_labels = [
        node["caption_label"] for node in visible if node["caption_kind"] == "table"
    ]
    figure_labels = [
        node["caption_label"] for node in visible if node["caption_kind"] == "figure"
    ]
    assert len(table_labels) == 41
    assert len({label.rstrip(".") for label in table_labels}) == 40
    assert Counter(label.rstrip(".") for label in table_labels)["Table 4.3"] == 2
    assert len(figure_labels) == 8
    assert len({label.rstrip(".") for label in figure_labels}) == 8
    assert sum(node["kind"] == "caption" for node in visible) == 9
    assert all(re.fullmatch(r"Table \d+(?:\.\d+)*[.:]?", label) for label in table_labels)
    assert all(
        re.fullmatch(r"(?:Figure|Fig\.) \d+(?:\.\d+)*\.?", label)
        for label in figure_labels
    )


def test_list_family_marker_nesting_and_semantics_are_independent(
    corpus: dict[str, Any],
):
    list_items = [node for node in corpus_nodes(corpus) if node["kind"] == "list_item"]
    assert len(list_items) == 845
    assert Counter(node["semantics_cue"]["kind"] for node in list_items) == {
        "unspecified": 283,
        "alternatives": 279,
        "enumeration": 111,
        "explicit_order": 84,
        "criteria": 84,
        "procedure": 4,
    }
    assert all(node["marker"] and node["list_family"] for node in list_items)
    assert all(
        node["semantics_cue"]["source"] is not None
        for node in list_items
        if node["semantics_cue"]["text"] is not None
    )
    assert not any(EXAMPLE_LABEL_RE.match(node["text"]) for node in list_items)

    p60 = [
        node
        for node in fragment(corpus, "P-60.2")["nodes"]
        if node["kind"] == "list_item"
    ]
    assert [node["marker"] for node in p60] == ["(a)", "(b)", "(c)", "(d)", "(e)"]
    assert all(node["list_kind"] == "ordered" for node in p60)
    assert all(node["list_family"] == "lower_alpha" for node in p60)
    assert all(node["nesting"] == 0 for node in p60)
    assert all(node["semantics_cue"]["kind"] == "alternatives" for node in p60)


def test_image_and_correction_event_metrics_reconcile_to_source(corpus: dict[str, Any]):
    nodes = list(corpus_nodes(corpus))
    standalone_events = [node for node in nodes if node["kind"] == "source_event"]
    linked_events: list[dict[str, Any]] = []
    regular_images: list[dict[str, Any]] = []
    for node in nodes:
        if node["kind"] == "figure":
            regular_images.extend(node["images"])
        elif node["kind"] == "orphan_cell":
            regular_images.extend(node["images"])
            linked_events.extend(node["source_events"])
        elif node["kind"] == "table":
            regular_images.extend(node["images"])
            linked_events.extend(node["source_events"])
            for row in node["rows"]:
                regular_images.extend(row["images"])
                linked_events.extend(row["source_events"])
                for cell in row["cells"]:
                    regular_images.extend(cell["images"])
                    linked_events.extend(cell["source_events"])
            for cell in node["orphan_cells"]:
                regular_images.extend(cell["images"])
                linked_events.extend(cell["source_events"])

    assert len(standalone_events) == 168
    assert len(linked_events) == 22
    assert len(standalone_events) + len(linked_events) == 190
    assert len(regular_images) == 5181
    all_events = standalone_events + linked_events
    all_image_ids = [image["occurrence_id"] for image in regular_images] + [
        event["icon"]["occurrence_id"] for event in all_events
    ]
    assert len(all_image_ids) == len(set(all_image_ids)) == 5371
    assert corpus["metrics"] == {
        "physical_table_occurrence_count": 567,
        "physical_row_occurrence_count": 3782,
        "physical_cell_occurrence_count": 9100,
        "physical_image_occurrence_count": 5371,
        "correction_event_count": 190,
        "footnote_block_count": 7,
        "visible_table_caption_count": 41,
        "distinct_visible_table_caption_count": 40,
        "visible_figure_caption_count": 8,
        "distinct_visible_figure_caption_count": 8,
    }
    assert all(event["event_kind"] == "correction" for event in all_events)
    assert all(event["source"]["ownership"]["kind"] == "alias" for event in all_events)
    assert all(event["icon"]["source"]["ownership"]["kind"] == "primary" for event in all_events)
    assert all(event["source"]["parts"] == event["icon"]["source"]["parts"] for event in all_events)
    assert all(event["target_url_source"]["ownership"]["kind"] == "alias" for event in all_events)
    assert all(event["icon"]["link_url_source"]["ownership"]["kind"] == "primary" for event in all_events)
    assert all(event["icon"]["source_src"].lower().endswith("alter.gif") for event in all_events)
    assert sum(
        source_document["source_metrics"]["physical_image_occurrence_count"]
        for source_document in corpus["documents"]
    ) == 5371
    assert sum(
        source_document["source_metrics"]["correction_event_count"]
        for source_document in corpus["documents"]
    ) == 190


def test_every_physical_table_row_cell_and_image_occurrence_is_represented_once(
    corpus: dict[str, Any],
):
    nodes = list(corpus_nodes(corpus))
    tables = [node for node in nodes if node["kind"] == "table"]
    rows = [row for table in tables for row in table["rows"]]
    cells = [cell for row in rows for cell in row["cells"]]
    cells.extend(cell for table in tables for cell in table["orphan_cells"])
    cells.extend(node for node in nodes if node["kind"] == "orphan_cell")

    assert len(tables) == 567
    assert len(rows) == 3782
    assert len(cells) == 9100
    for values in (tables, rows, cells):
        occurrence_ids = [value["occurrence_id"] for value in values]
        assert len(occurrence_ids) == len(set(occurrence_ids))

    p14_tables = [
        node
        for node in walk_nodes(fragment(corpus, "P-14.1.1")["nodes"])
        if node["kind"] == "table"
    ]
    assert len(p14_tables) == 1
    assert not p14_tables[0]["rows"]
    assert len(p14_tables[0]["orphan_cells"]) == 12

    p33_tables = [
        node
        for node in walk_nodes(fragment(corpus, "P-33.1")["nodes"])
        if node["kind"] == "table"
    ]
    assert [len(node["rows"]) for node in p33_tables] == [2, 0, 5]
    assert sum(
        len(row["cells"]) for node in p33_tables for row in node["rows"]
    ) == 27

    p59_row_images = sum(
        len(row["images"])
        for source_document in corpus["documents"]
        for source_fragment in source_document["fragments"]
        if source_fragment["rule_id"].startswith("P-59.2.")
        for node in walk_nodes(source_fragment["nodes"])
        if node["kind"] == "table"
        for row in node["rows"]
    )
    assert p59_row_images == 20

    p101_orphans = [
        node
        for node in fragment(corpus, "P-101.3.7.1")["nodes"]
        if node["kind"] == "orphan_cell"
    ]
    assert len(p101_orphans) == 2
    assert sum(len(node["images"]) for node in p101_orphans) == 2


def test_footnote_blocks_are_typed_and_table_footnotes_are_attached(
    corpus: dict[str, Any],
):
    nodes = list(corpus_nodes(corpus))
    standalone = [node for node in nodes if node["kind"] == "footnote"]
    attached = [
        footnote
        for node in nodes
        if node["kind"] == "table"
        for footnote in node["footnotes"]
    ]
    assert len(standalone) == 3
    assert len(attached) == 4
    assert Counter(item["marker"] for item in standalone + attached) == {
        "*": 5,
        "†": 2,
    }
    assert all(
        item["field_sources"]["marker"]["ownership"]["kind"] == "primary"
        and item["field_sources"]["text"]["ownership"]["kind"] == "primary"
        for item in standalone + attached
    )
    assert len(
        next(
            node
            for node in fragment(corpus, "P-15.5.3.2")["nodes"]
            if node["kind"] == "table"
        )["footnotes"]
    ) == 1


def test_every_normalized_field_replays_from_exact_html_bytes_and_mutation_fails(
    corpus: dict[str, Any],
):
    totals: Counter[str] = Counter()
    for source_document in corpus["documents"]:
        raw = (CACHE / source_document["cache_path"]).read_bytes()
        for source_fragment in source_document["fragments"]:
            start = source_fragment["source"]["start_byte"]
            end = source_fragment["source"]["end_byte"]
            metrics = validate_fragment_field_sources(
                source_fragment, raw[start:end]
            )
            assert metrics == source_fragment["field_source_metrics"]
            totals.update(metrics)

    assert totals == {
        "field_source_count": 38256,
        "field_mapping_count": 190762,
        "primary_field_source_count": 25413,
        "alias_field_source_count": 7282,
        "aggregate_field_source_count": 0,
        "synthetic_field_source_count": 5561,
    }

    source_fragment = copy.deepcopy(fragment(corpus, "P-14.1.1"))
    source_fragment["nodes"][0]["text"] = "X" + source_fragment["nodes"][0][
        "text"
    ][1:]
    source_document = document(corpus, "P-1")
    raw = (CACHE / source_document["cache_path"]).read_bytes()
    start = source_fragment["source"]["start_byte"]
    end = source_fragment["source"]["end_byte"]
    with pytest.raises(ValueError, match="value digest mismatch"):
        validate_fragment_field_sources(source_fragment, raw[start:end])
