from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import extract_pdf_rule_sections as extractor
from scripts import validate_pdf_rebuild as validator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CORPUS = ROOT / "data" / "bluebook_v3" / "bluebook_v3_source_corpus.json"
SOURCE_PAGES = ROOT / "data" / "bluebook_v3" / "bluebook_v3_source_pages.json"
SOURCE_SCHEMA = ROOT / "data" / "bluebook_source_corpus.schema.json"
SOURCE_PAGES_SCHEMA = ROOT / "data" / "bluebook_source_pages.schema.json"

SOURCE_IMAGE_COUNTERS = {
    "pdf_image_placement_count",
    "pdf_page_primary_image_object_count",
    "pdf_primary_image_object_count",
    "pdf_soft_mask_image_object_count",
    "pdf_explicit_mask_image_object_count",
    "pdf_image_object_count",
    "pdf_unique_decoded_image_payload_count",
}
SOURCE_CHARACTER_COUNTERS = {
    "source_character_count",
    "owned_rule_source_character_count",
    "owned_non_rule_source_character_count",
}
EXPECTED_CHAPTER_MASTHEADS = (
    ("P-16.9.6", "p0138:l020", 139, "P-2"),
    ("P-29.6.3", "p0313:l035", 314, "P-3"),
    ("P-35.5.1", "p0354:l063", 355, "P-4"),
    ("P-46.3.2", "p0426:l014", 427, "P-5"),
    ("P-59.2.5", "p0493:l014", 494, "P-6"),
    ("P-65.7.7.3", "p0636:l008", 637, "P-6"),
    ("P-69.5.3", "p0783:l015", 784, "P-7"),
    ("P-77.3.2", "p0843:l005", 844, "P-8"),
    ("P-84", "p0862:l018", 863, "P-9"),
    ("P-94.3.2.6", "p0953:l001", 954, "P-10"),
)


@pytest.fixture(scope="session")
def artifacts() -> dict[str, Any]:
    missing = [
        path
        for path in (SOURCE_CORPUS, SOURCE_PAGES, SOURCE_SCHEMA, SOURCE_PAGES_SCHEMA)
        if not path.exists()
    ]
    assert not missing, f"Generate the lossless source artifacts first: {missing}"
    pages = validator.load_json(SOURCE_PAGES)
    return {
        "source": validator.load_json(SOURCE_CORPUS),
        "pages": pages,
        "schema": validator.load_json(SOURCE_SCHEMA),
        "pages_schema": validator.load_json(SOURCE_PAGES_SCHEMA),
        "lines": {
            line["uid"]: line
            for page in pages["pages"]
            for line in page["lines"]
        },
    }


def validate(
    artifacts: dict[str, Any],
    source: dict[str, Any],
    pages: dict[str, Any] | None = None,
    *,
    validate_pages_schema: bool = False,
) -> dict[str, Any]:
    return validator.validate_source_corpus(
        source,
        pages if pages is not None else artifacts["pages"],
        artifacts["schema"],
        artifacts["pages_schema"] if validate_pages_schema else None,
    )


def error_codes(result: dict[str, Any]) -> set[str]:
    return {error["code"] for error in result["errors"]}


def record(source: dict[str, Any], rule_id: str) -> dict[str, Any]:
    return next(item for item in source["records"] if item["source_rule_id"] == rule_id)


def non_rule_block(source: dict[str, Any], block_id: str) -> dict[str, Any]:
    return next(
        item for item in source["non_rule_blocks"] if item["block_id"] == block_id
    )


def audited_anomaly(source: dict[str, Any], rule_id: str) -> dict[str, Any]:
    return next(
        item
        for item in source["reconciliation"]["audited_source_anomalies"]
        if item["rule_id"] == rule_id
    )


def rebuild_rule_pdf(item: dict[str, Any], lines: dict[str, dict[str, Any]]) -> None:
    pdf = item["pdf"]
    spans = pdf["source_spans"]
    pdf["source_line_ids"] = list(
        dict.fromkeys(span["line_id"] for span in spans)
    )
    source_lines = [lines[line_id] for line_id in pdf["source_line_ids"]]
    pdf["start_line"] = pdf["source_line_ids"][0]
    pdf["end_line"] = pdf["source_line_ids"][-1]
    pdf["pages"] = sorted({line["page"] for line in source_lines})
    restored = item["alignment"]["kind"] == "structural_heading_restored"
    pdf["text"] = validator.stripped_rule_text(
        pdf["printed_rule_id"], lines, spans, restored
    )
    pdf["text_sha256"] = validator.sha256_text(pdf["text"])


def rebuild_block(item: dict[str, Any], lines: dict[str, dict[str, Any]]) -> None:
    source_lines = [lines[line_id] for line_id in item["source_line_ids"]]
    item["pages"] = sorted({line["page"] for line in source_lines})
    item["text"] = "\n".join(line["text"] for line in source_lines).strip()
    item["text_sha256"] = validator.sha256_text(item["text"])


def with_mutated_page(
    pages: dict[str, Any], page_number: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    mutated = dict(pages)
    page_rows = list(pages["pages"])
    changed_page = copy.deepcopy(page_rows[page_number - 1])
    page_rows[page_number - 1] = changed_page
    mutated["pages"] = page_rows
    return mutated, changed_page


def with_mutated_image_object(
    pages: dict[str, Any], index: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    mutated = dict(pages)
    image_objects = list(pages["image_objects"])
    changed_object = copy.deepcopy(image_objects[index])
    image_objects[index] = changed_object
    mutated["image_objects"] = image_objects
    return mutated, changed_object


def pdf_record(rule_id: str, line_id: str) -> dict[str, Any]:
    text = f"Text for {rule_id}"
    return {
        "record_id": f"bluebook-v3:{rule_id}",
        "source_rule_id": rule_id,
        "chapter": "P-1",
        "source_kind": "rule_section",
        "pdf": {
            "printed_rule_id": rule_id,
            "pages": [48],
            "start_line": line_id,
            "end_line": line_id,
            "source_line_ids": [line_id],
            "text": text,
            "text_sha256": extractor.sha256_text(text),
        },
    }


def html_record(rule_id: str, anchor: str) -> extractor.HtmlRuleSection:
    return extractor.HtmlRuleSection(
        rule_id=rule_id,
        chapter="P-1",
        url="https://iupac.qmul.ac.uk/BlueBook/P1.html",
        anchor=anchor,
        text=f"Text for {rule_id}",
        fragment_sha256="0" * 64,
        references=[],
        image_urls=[],
        table_count=0,
    )


def test_generated_source_corpus_passes_baseline_validation(artifacts: dict[str, Any]) -> None:
    result = validate(
        artifacts,
        artifacts["source"],
        validate_pages_schema=True,
    )

    assert result["passed"], result["errors"]
    assert result["error_count"] == 0


def test_p254342_preserves_typed_source_gap_provenance(
    artifacts: dict[str, Any],
) -> None:
    source = artifacts["source"]
    item = record(source, "P-25.4.3.4.2")
    expected_defect = {
        "kind": "source_gap_restoration",
        "after_line": "p0247:l007",
        "before_line": "p0247:l008",
        "authoritative_source": "official_corrected_html",
    }

    assert item["alignment"]["kind"] == "rule_id_exact_with_pdf_text_omission"
    assert item["alignment"]["defect"] == expected_defect
    assert audited_anomaly(source, "P-25.4.3.4.2")["defect"] == expected_defect
    gap_index = item["pdf"]["source_line_ids"].index("p0247:l007")
    assert item["pdf"]["source_line_ids"][gap_index + 1] == "p0247:l008"


@pytest.mark.parametrize("mutation", ["altered", "missing"])
def test_validator_rejects_changed_p254342_source_gap_provenance(
    artifacts: dict[str, Any], mutation: str
) -> None:
    source = copy.deepcopy(artifacts["source"])
    alignment = record(source, "P-25.4.3.4.2")["alignment"]
    declaration = audited_anomaly(source, "P-25.4.3.4.2")
    if mutation == "missing":
        alignment.pop("defect")
        declaration.pop("defect")
    else:
        alignment["defect"]["after_line"] = "p0247:l006"
        declaration["defect"]["after_line"] = "p0247:l006"

    result = validate(artifacts, source)

    codes = error_codes(result)
    assert "source.pdf_text_omission_provenance" in codes
    assert "source.anomaly_provenance" not in codes


@pytest.mark.parametrize("duplicate_source", ["pdf", "html"])
def test_extractor_rejects_duplicate_rule_ids(
    duplicate_source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extractor, "KNOWN_STRUCTURAL_ALIGNMENTS", {})
    monkeypatch.setattr(extractor, "KNOWN_HTML_ANCHOR_COLLISIONS", {})
    pdf = [pdf_record("P-1.1", "p0048:l001")]
    html = [html_record("P-1.1", "0101")]
    if duplicate_source == "pdf":
        pdf.append(copy.deepcopy(pdf[0]))
    else:
        html.append(copy.deepcopy(html[0]))

    with pytest.raises(ValueError, match=f"{duplicate_source.upper()} rule ids are not unique"):
        extractor.merge_sources(pdf, html, {})


def test_validator_rejects_duplicate_record_and_rule_ids(artifacts: dict[str, Any]) -> None:
    source = copy.deepcopy(artifacts["source"])
    source["records"][1]["record_id"] = source["records"][0]["record_id"]
    source["records"][1]["source_rule_id"] = source["records"][0]["source_rule_id"]

    result = validate(artifacts, source)

    assert {"source.record_ids", "source.rule_ids"} <= error_codes(result)


def test_validator_rejects_forged_counters(artifacts: dict[str, Any]) -> None:
    source = copy.deepcopy(artifacts["source"])
    for field in (
        "rule_record_count",
        "non_rule_block_count",
        "source_line_count",
        "owned_rule_source_line_count",
        "owned_non_rule_source_line_count",
        *sorted(SOURCE_IMAGE_COUNTERS),
    ):
        source[field] += 1
    source["reconciliation"]["pdf_rule_count"] += 1
    source["reconciliation"]["html_rule_count"] += 1
    source["reconciliation"]["merged_rule_count"] += 1

    result = validate(artifacts, source)

    codes = error_codes(result)
    assert {
        "source.pdf_heading_count",
        "source.html_anchor_count",
        "source.merged_count",
        "source.counter.rules",
        "source.counter.blocks",
        "source.counter.lines",
        "source.counter.rule_lines",
        "source.counter.block_lines",
        "source.counter.images",
    } <= codes
    image_counter_errors = [
        error
        for error in result["errors"]
        if error["code"] == "source.counter.images"
    ]
    assert {error["context"]["field"] for error in image_counter_errors} == (
        SOURCE_IMAGE_COUNTERS
    )


def test_character_counters_match_exact_source_ownership(
    artifacts: dict[str, Any],
) -> None:
    source = artifacts["source"]
    lines = artifacts["lines"]
    observed = {
        "source_character_count": sum(len(line["text"]) for line in lines.values()),
        "owned_rule_source_character_count": sum(
            span["text_end"] - span["text_start"]
            for item in source["records"]
            for span in item["pdf"]["source_spans"]
        ),
        "owned_non_rule_source_character_count": sum(
            len(lines[line_id]["text"])
            for item in source["non_rule_blocks"]
            for line_id in item["source_line_ids"]
        ),
    }

    assert {field: source[field] for field in SOURCE_CHARACTER_COUNTERS} == observed
    assert (
        observed["owned_rule_source_character_count"]
        + observed["owned_non_rule_source_character_count"]
        == observed["source_character_count"]
    )


def test_validator_rejects_all_forged_character_counters(
    artifacts: dict[str, Any],
) -> None:
    source = copy.deepcopy(artifacts["source"])
    for field in SOURCE_CHARACTER_COUNTERS:
        source[field] += 1

    result = validate(artifacts, source)

    character_counter_errors = [
        error
        for error in result["errors"]
        if error["code"] == "source.counter.characters"
    ]
    assert {error["context"]["field"] for error in character_counter_errors} == (
        SOURCE_CHARACTER_COUNTERS
    )


def test_validator_rejects_an_unowned_source_line(artifacts: dict[str, Any]) -> None:
    source = copy.deepcopy(artifacts["source"])
    target = next(
        item
        for item in source["records"]
        if len(item["pdf"]["source_line_ids"]) > 2
        and item["alignment"]["kind"] == "rule_id_exact"
    )
    target["pdf"]["source_spans"].pop()
    rebuild_rule_pdf(target, artifacts["lines"])
    source["owned_rule_source_line_count"] -= 1

    result = validate(artifacts, source)

    assert {"source.unowned_lines", "source.line_coverage"} <= error_codes(result)
    assert "source.character_overlap" not in error_codes(result)


def test_validator_rejects_overlapping_line_ownership(artifacts: dict[str, Any]) -> None:
    source = copy.deepcopy(artifacts["source"])
    target, donor = source["records"][:2]
    target["pdf"]["source_spans"].append(
        copy.deepcopy(donor["pdf"]["source_spans"][0])
    )
    rebuild_rule_pdf(target, artifacts["lines"])

    result = validate(artifacts, source)

    assert "source.character_overlap" in error_codes(result)
    assert "source.unowned_lines" not in error_codes(result)


def test_validator_rejects_terminal_rule_spill_into_references(artifacts: dict[str, Any]) -> None:
    source = copy.deepcopy(artifacts["source"])
    terminal = record(source, "P-107.4.3.3")
    references = next(
        block for block in source["non_rule_blocks"] if 1067 in block["pages"]
    )
    spilled_line = next(
        line_id
        for line_id in references["source_line_ids"]
        if artifacts["lines"][line_id]["page"] == 1067
    )
    references["source_line_ids"].remove(spilled_line)
    terminal["pdf"]["source_spans"].append(
        {
            "line_id": spilled_line,
            "text_start": 0,
            "text_end": len(artifacts["lines"][spilled_line]["text"]),
            "role": "body",
        }
    )
    rebuild_rule_pdf(terminal, artifacts["lines"])
    rebuild_block(references, artifacts["lines"])
    source["owned_rule_source_line_count"] += 1
    source["owned_non_rule_source_line_count"] -= 1

    result = validate(artifacts, source)

    assert {"source.rule_region", "source.terminal_boundary"} <= error_codes(result)
    assert "source.character_overlap" not in error_codes(result)
    assert "source.unowned_lines" not in error_codes(result)


def test_all_chapter_masthead_reassignments_have_exact_owners(
    artifacts: dict[str, Any],
) -> None:
    source = artifacts["source"]
    declarations = source["reconciliation"]["chapter_masthead_reassignments"]

    assert len(declarations) == len(EXPECTED_CHAPTER_MASTHEADS) == 10
    for declaration, expected in zip(declarations, EXPECTED_CHAPTER_MASTHEADS):
        previous_rule_id, previous_end_line, page, chapter = expected
        expected_lines = [f"p{page:04d}:l{line:03d}" for line in range(1, 7)]
        assert {
            key: declaration[key]
            for key in (
                "previous_rule_id",
                "previous_rule_end_line",
                "page",
                "chapter",
                "source_line_ids",
            )
        } == {
            "previous_rule_id": previous_rule_id,
            "previous_rule_end_line": previous_end_line,
            "page": page,
            "chapter": chapter,
            "source_line_ids": expected_lines,
        }
        previous = record(source, previous_rule_id)
        owner = non_rule_block(source, declaration["block_id"])
        assert previous["pdf"]["end_line"] == previous_end_line
        assert not set(expected_lines).intersection(previous["pdf"]["source_line_ids"])
        assert owner["source_kind"] == "chapter_front_matter"
        assert owner["chapter"] == chapter
        assert owner["source_line_ids"][:6] == expected_lines


def test_validator_rejects_all_mastheads_reassigned_to_terminal_rules(
    artifacts: dict[str, Any],
) -> None:
    source = copy.deepcopy(artifacts["source"])
    moved_lines = 0
    moved_characters = 0
    for declaration in source["reconciliation"]["chapter_masthead_reassignments"]:
        line_ids = list(declaration["source_line_ids"])
        terminal = record(source, declaration["previous_rule_id"])
        owner = non_rule_block(source, declaration["block_id"])
        assert owner["source_line_ids"][: len(line_ids)] == line_ids
        owner["source_line_ids"] = owner["source_line_ids"][len(line_ids) :]
        terminal["pdf"]["source_spans"].extend(
            {
                "line_id": line_id,
                "text_start": 0,
                "text_end": len(artifacts["lines"][line_id]["text"]),
                "role": "body",
            }
            for line_id in line_ids
        )
        rebuild_rule_pdf(terminal, artifacts["lines"])
        rebuild_block(owner, artifacts["lines"])
        moved_lines += len(line_ids)
        moved_characters += sum(
            len(artifacts["lines"][line_id]["text"]) for line_id in line_ids
        )
    source["owned_rule_source_line_count"] += moved_lines
    source["owned_non_rule_source_line_count"] -= moved_lines
    source["owned_rule_source_character_count"] += moved_characters
    source["owned_non_rule_source_character_count"] -= moved_characters

    result = validate(artifacts, source)

    expected_rules = {item[0] for item in EXPECTED_CHAPTER_MASTHEADS}
    boundary_errors = [
        error
        for error in result["errors"]
        if error["code"] == "source.chapter_terminal_boundary"
    ]
    owner_errors = [
        error
        for error in result["errors"]
        if error["code"] == "source.chapter_masthead_owner"
    ]
    assert {
        error["context"]["previous_rule_id"] for error in boundary_errors
    } == expected_rules
    assert {
        error["context"]["previous_rule_id"] for error in owner_errors
    } == expected_rules
    assert not {
        "source.unowned_lines",
        "source.character_gaps",
        "source.character_overlap",
        "source.character_counter_sum",
        "source.counter.characters",
    }.intersection(error_codes(result))


def test_validator_rejects_masthead_lines_moved_to_wrong_block(
    artifacts: dict[str, Any],
) -> None:
    source = copy.deepcopy(artifacts["source"])
    first, second = source["reconciliation"]["chapter_masthead_reassignments"][:2]
    line_ids = list(first["source_line_ids"])
    correct_owner = non_rule_block(source, first["block_id"])
    wrong_owner = non_rule_block(source, second["block_id"])
    correct_owner["source_line_ids"] = correct_owner["source_line_ids"][len(line_ids) :]
    wrong_owner["source_line_ids"].extend(line_ids)
    rebuild_block(correct_owner, artifacts["lines"])
    rebuild_block(wrong_owner, artifacts["lines"])

    result = validate(artifacts, source)

    codes = error_codes(result)
    assert "source.chapter_masthead_owner" in codes
    assert "source.chapter_masthead_provenance" not in codes
    assert "source.chapter_terminal_boundary" not in codes


def test_validator_rejects_wrong_masthead_declaration(
    artifacts: dict[str, Any],
) -> None:
    source = copy.deepcopy(artifacts["source"])
    source["reconciliation"]["chapter_masthead_reassignments"][0][
        "chapter"
    ] = "P-3"

    result = validate(artifacts, source)

    codes = error_codes(result)
    assert "source.chapter_masthead_provenance" in codes
    assert "source.chapter_masthead_owner" not in codes
    assert "source.chapter_terminal_boundary" not in codes


def test_validator_rejects_validly_shaped_but_forged_text_hashes(
    artifacts: dict[str, Any],
) -> None:
    source = copy.deepcopy(artifacts["source"])
    source["records"][0]["pdf"]["text_sha256"] = "0" * 64
    source["records"][0]["html"]["text_sha256"] = "F" * 64

    result = validate(artifacts, source)

    assert {"source.pdf_hash", "source.html_hash"} <= error_codes(result)


def test_validator_rejects_malformed_image_object_hashes(
    artifacts: dict[str, Any],
) -> None:
    pages, image_object = with_mutated_image_object(artifacts["pages"], 0)
    image_object["raw_sha256"] = "not-a-sha256"

    result = validate(artifacts, artifacts["source"], pages)

    assert "pages.image_object_hash" in error_codes(result)


def test_validator_rejects_empty_image_object_hash(artifacts: dict[str, Any]) -> None:
    pages, image_object = with_mutated_image_object(artifacts["pages"], 0)
    image_object["raw_sha256"] = validator.sha256_bytes(b"")

    result = validate(artifacts, artifacts["source"], pages)

    assert "pages.empty_image_object_hash" in error_codes(result)


def test_validator_rejects_broken_image_placement_target(
    artifacts: dict[str, Any],
) -> None:
    page_number = next(
        page["page"] for page in artifacts["pages"]["pages"] if page["images"]
    )
    pages, changed_page = with_mutated_page(artifacts["pages"], page_number)
    placement = changed_page["images"][0]
    placement["object_id"] = 99_999_999
    placement["generation"] = 0
    placement["image_object_id"] = "pdf-image-object:99999999:00000"

    result = validate(artifacts, artifacts["source"], pages)

    assert "pages.placement_object_target" in error_codes(result)


def test_validator_rejects_missing_mask_dependency_target(
    artifacts: dict[str, Any],
) -> None:
    object_index = next(
        index
        for index, image_object in enumerate(artifacts["pages"]["image_objects"])
        if image_object["dependencies"]
    )
    pages, image_object = with_mutated_image_object(
        artifacts["pages"], object_index
    )
    image_object["dependencies"][0][
        "target_image_object_id"
    ] = "pdf-image-object:99999999:00000"

    result = validate(artifacts, artifacts["source"], pages)

    assert "pages.image_dependency_target" in error_codes(result)


def test_validator_rejects_mask_dependency_with_wrong_target_role(
    artifacts: dict[str, Any],
) -> None:
    image_objects = artifacts["pages"]["image_objects"]
    object_index = next(
        index for index, image_object in enumerate(image_objects) if image_object["dependencies"]
    )
    relation = image_objects[object_index]["dependencies"][0]["relation"]
    expected_role = "soft_mask" if relation == "soft_mask" else "explicit_mask"
    wrong_target = next(
        image_object["image_object_id"]
        for image_object in image_objects
        if expected_role not in image_object["roles"]
    )
    pages, image_object = with_mutated_image_object(
        artifacts["pages"], object_index
    )
    image_object["dependencies"][0]["target_image_object_id"] = wrong_target

    result = validate(artifacts, artifacts["source"], pages)

    codes = error_codes(result)
    assert "pages.image_dependency_role" in codes
    assert "pages.image_dependency_target" not in codes


def test_validator_rejects_forged_image_metrics(artifacts: dict[str, Any]) -> None:
    pages = dict(artifacts["pages"])
    pages["image_metrics"] = dict(pages["image_metrics"])
    pages["image_metrics"]["placement_count"] += 1

    result = validate(artifacts, artifacts["source"], pages)

    assert "pages.image_metrics" in error_codes(result)


def test_validator_rejects_malformed_restored_alignment(artifacts: dict[str, Any]) -> None:
    source = copy.deepcopy(artifacts["source"])
    restored = record(source, "P-65.1.2.1")
    restored["alignment"] = {
        "kind": "rule_id_exact",
        "pdf_rule_id": "P-65.1.2",
        "html_rule_id": "P-65.1.2.1",
    }

    result = validate(artifacts, source)

    assert "source.restored_heading" in error_codes(result)


def test_restored_rule_uses_current_pdf_line_partition(
    artifacts: dict[str, Any],
) -> None:
    parent = record(artifacts["source"], "P-65.1.2")
    child = record(artifacts["source"], "P-65.1.2.1")

    assert parent["source_kind"] == "rule_section"
    assert parent["pdf"]["source_line_ids"] == [
        "p0572:l023",
        "p0572:l024",
        "p0572:l025",
    ]
    assert parent["pdf"]["source_spans"][-1] == {
        "line_id": "p0572:l025",
        "text_start": 0,
        "text_end": 9,
        "role": "printed_text_splice_parent",
    }
    assert child["pdf"]["source_line_ids"] == [
        f"p0572:l{line:03d}" for line in range(25, 38)
    ]
    assert child["pdf"]["source_spans"][0] == {
        "line_id": "p0572:l025",
        "text_start": 9,
        "text_end": len(artifacts["lines"]["p0572:l025"]["text"]),
        "role": "printed_text_splice_child",
    }


def test_validator_rejects_obsolete_restored_rule_line_partition(
    artifacts: dict[str, Any],
) -> None:
    source = copy.deepcopy(artifacts["source"])
    parent = record(source, "P-65.1.2")
    child = record(source, "P-65.1.2.1")
    parent["pdf"]["source_spans"].pop()
    child["pdf"]["source_spans"][0].update(
        {
            "text_start": 0,
            "role": "body",
        }
    )
    rebuild_rule_pdf(parent, artifacts["lines"])
    rebuild_rule_pdf(child, artifacts["lines"])

    result = validate(artifacts, source)

    assert {"source.restored_parent", "source.restored_child"} <= error_codes(result)


def test_validator_rejects_undeclared_official_anchor_collision(
    artifacts: dict[str, Any],
) -> None:
    source = copy.deepcopy(artifacts["source"])
    source["reconciliation"]["source_html_anchor_collisions"] = []

    result = validate(artifacts, source)

    assert "source.html_anchor_collision_provenance" in error_codes(result)


@pytest.mark.parametrize("mutation", ["remove_known", "add_unknown"])
def test_validator_rejects_changed_official_anchor_collisions(
    artifacts: dict[str, Any], mutation: str
) -> None:
    source = copy.deepcopy(artifacts["source"])
    if mutation == "remove_known":
        record(source, "P-25.4.5.2")["html"]["anchor"] += "-changed"
    else:
        first, second = source["records"][:2]
        second["html"]["url"] = first["html"]["url"]
        second["html"]["anchor"] = first["html"]["anchor"]

    result = validate(artifacts, source)

    assert {
        "source.html_anchor_collisions",
        "source.html_anchor_collision_provenance",
    } <= error_codes(result)


def test_extractor_rejects_unexpected_anchor_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extractor, "KNOWN_STRUCTURAL_ALIGNMENTS", {})
    monkeypatch.setattr(extractor, "KNOWN_HTML_ANCHOR_COLLISIONS", {})
    pdf = [
        pdf_record("P-1.1", "p0048:l001"),
        pdf_record("P-1.2", "p0048:l002"),
        pdf_record("P-65.1.2", "p0048:l003"),
    ]
    html = [
        html_record("P-1.1", "shared"),
        html_record("P-1.2", "shared"),
        html_record("P-65.1.2", "unique"),
    ]

    with pytest.raises(ValueError, match="Official HTML anchor collisions differ"):
        extractor.merge_sources(pdf, html, {})


def test_extractor_preserves_unicode_and_repairs_cp1252_controls() -> None:
    raw = (
        "<html><body>"
        "<a name='0101'>P-1.1 </a>"
        "<p>beta: \u03b2; thin space: \u2009; prime: \u2032; &#145;legacy&#146;</p>"
        "<!-- <a name='0102'>P-1.2 </a><p>deleted</p> -->"
        "</body></html>"
    ).encode("utf-8")

    sections, manifest = extractor.extract_html_rules("P-1", "P1.html", raw)

    assert manifest["active_rule_anchor_count"] == 1
    assert [section.rule_id for section in sections] == ["P-1.1"]
    assert "\u03b2" in sections[0].text
    assert "\u2009" in sections[0].text
    assert "\u2032" in sections[0].text
    assert "\u2018legacy\u2019" in sections[0].text
    assert not validator.has_control_characters(sections[0].text)
    assert not any(marker in sections[0].text for marker in validator.MOJIBAKE_MARKERS)


@pytest.mark.parametrize(
    ("corruption", "expected_code"),
    [("\u0091", "source.html_control"), ("\u00c2", "source.mojibake")],
)
def test_validator_rejects_html_unicode_corruption(
    artifacts: dict[str, Any], corruption: str, expected_code: str
) -> None:
    source = copy.deepcopy(artifacts["source"])
    html = source["records"][0]["html"]
    html["text"] += corruption
    html["text_sha256"] = validator.sha256_text(html["text"])

    result = validate(artifacts, source)

    assert expected_code in error_codes(result)


def test_corpus_is_canonical_json(artifacts: dict[str, Any]) -> None:
    expected = validator.canonical_json_bytes(artifacts["source"])

    assert SOURCE_CORPUS.read_bytes() == expected
    assert extractor.canonical_json_bytes(artifacts["source"]) == expected


def test_cli_rejects_noncanonical_json(
    artifacts: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    noncanonical = tmp_path / "source.json"
    noncanonical.write_text(
        json.dumps(artifacts["source"], ensure_ascii=False), encoding="utf-8"
    )
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(validator, "SOURCE_CORPUS", noncanonical)
    monkeypatch.setattr(validator, "SOURCE_PAGES", SOURCE_PAGES)
    monkeypatch.setattr(validator, "SOURCE_SCHEMA", SOURCE_SCHEMA)
    missing_semantic = ROOT / "data" / "bluebook_v3" / "__pytest_missing_semantic__.json"
    assert not missing_semantic.exists()
    monkeypatch.setattr(validator, "SEMANTIC_CORPUS", missing_semantic)
    monkeypatch.setattr(validator, "REPORT", report_path)
    monkeypatch.setattr(sys, "argv", ["validate_pdf_rebuild.py", "--stage", "source"])

    exit_code = validator.main()
    report = validator.load_json(report_path)

    assert exit_code == 1
    assert "source.canonical_json" in error_codes(report["source"])
