from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

if __package__:
    from scripts.assemble_normalized_rule_corpus import (
        AssemblyError as SemanticAssemblyError,
        validate_rule_corpus,
    )
    from scripts.audit_html_physical_occurrences import (
        AuditError as PhysicalAuditError,
        build_census as build_html_physical_census,
        reconcile_artifact as reconcile_html_physical_artifact,
    )
    from scripts.build_reference_dependency_graph import (
        build_reference_dependency_graph,
        validate_graph as validate_reference_dependency_graph,
    )
    from scripts.build_reference_resolution_overlays import (
        ResolutionError as ReferenceResolutionError,
        build_reference_resolutions,
    )
    from scripts.document_node_store import (
        DEFAULT_STORE as DEFAULT_DOCUMENT_NODE_STORE,
        DocumentNodeStoreError,
        hash_document_nodes,
        load_document_nodes,
    )
    from scripts.extract_html_document_nodes import validate_fragment_field_sources
    from scripts.extract_reference_occurrences import extract_reference_occurrences
else:
    from assemble_normalized_rule_corpus import (
        AssemblyError as SemanticAssemblyError,
        validate_rule_corpus,
    )
    from audit_html_physical_occurrences import (
        AuditError as PhysicalAuditError,
        build_census as build_html_physical_census,
        reconcile_artifact as reconcile_html_physical_artifact,
    )
    from build_reference_dependency_graph import (
        build_reference_dependency_graph,
        validate_graph as validate_reference_dependency_graph,
    )
    from build_reference_resolution_overlays import (
        ResolutionError as ReferenceResolutionError,
        build_reference_resolutions,
    )
    from document_node_store import (
        DEFAULT_STORE as DEFAULT_DOCUMENT_NODE_STORE,
        DocumentNodeStoreError,
        hash_document_nodes,
        load_document_nodes,
    )
    from extract_html_document_nodes import validate_fragment_field_sources
    from extract_reference_occurrences import extract_reference_occurrences

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "bluebook_v3"
SOURCE_CORPUS = BASE / "bluebook_v3_source_corpus.json"
SOURCE_PAGES = BASE / "bluebook_v3_source_pages.json"
SOURCE_SCHEMA = ROOT / "data" / "bluebook_source_corpus.schema.json"
SOURCE_PAGES_SCHEMA = ROOT / "data" / "bluebook_source_pages.schema.json"
CORRECTION_OVERLAYS = BASE / "bluebook_v3_correction_overlays.json"
CORRECTION_SCHEMA = ROOT / "data" / "bluebook_correction_overlay.schema.json"
DOCUMENT_NODES = DEFAULT_DOCUMENT_NODE_STORE
DOCUMENT_NODE_SCHEMA = ROOT / "data" / "bluebook_document_nodes.schema.json"
CLAUSE_INVENTORY = BASE / "bluebook_v3_clause_inventory.json"
CLAUSE_INVENTORY_SCHEMA = ROOT / "data" / "bluebook_clause_inventory.schema.json"
REFERENCE_OCCURRENCES = BASE / "bluebook_v3_reference_occurrences.json"
REFERENCE_OCCURRENCES_SCHEMA = (
    ROOT / "data" / "bluebook_reference_occurrences.schema.json"
)
REFERENCE_RESOLUTIONS = BASE / "bluebook_v3_reference_resolutions.json"
REFERENCE_RESOLUTIONS_SCHEMA = (
    ROOT / "data" / "bluebook_reference_resolutions.schema.json"
)
REFERENCE_DEPENDENCY_GRAPH = BASE / "bluebook_v3_reference_dependency_graph.json"
REFERENCE_DEPENDENCY_GRAPH_SCHEMA = (
    ROOT / "data" / "bluebook_reference_dependency_graph.schema.json"
)
SEMANTIC_CORPUS = BASE / "bluebook_v3_rule_ir.json"
SEMANTIC_SCHEMA = ROOT / "data" / "normalized_rule_language.schema.json"
REPORT = BASE / "bluebook_v3_validation_report.json"

RULE_ID_RE = re.compile(r"^P-\d+(?:\.\d+)*(?:\([a-z0-9]+\))?$")
HTML_COMMENT_RE = re.compile(rb"<!--.*?-->", re.DOTALL)
MOJIBAKE_MARKERS = ("\u00c2", "\u00c3", "\u00e2\u20ac", "\ufffd")
EXPECTED_CHAPTER_COUNTS = {
    "P-1": 285,
    "P-2": 433,
    "P-3": 109,
    "P-4": 165,
    "P-5": 173,
    "P-6": 703,
    "P-7": 169,
    "P-8": 63,
    "P-9": 169,
    "P-10": 285,
}
EXPECTED_HTML_ANCHOR_COLLISIONS = [
    {
        "href": "https://iupac.qmul.ac.uk/BlueBook/P2.html#25040501",
        "rule_ids": ["P-25.4.5.1", "P-25.4.5.2"],
    },
    {
        "href": "https://iupac.qmul.ac.uk/BlueBook/P6a.html#6801010303",
        "rule_ids": ["P-68.1.1.3.3", "P-68.1.1.3.4"],
    },
]
EXPECTED_SOURCE_ANOMALIES = [
    {
        "rule_id": "P-25.4.3.4.2",
        "kind": "rule_id_exact_with_pdf_text_omission",
    },
    {
        "rule_id": "P-33.1",
        "kind": "rule_id_exact_with_pdf_layout_reordering",
    },
    {
        "rule_id": "P-33.2",
        "kind": "rule_id_exact_with_pdf_layout_reordering",
    },
    {
        "rule_id": "P-65.1.2",
        "kind": "rule_id_exact_with_pdf_text_omission",
    },
    {
        "rule_id": "P-65.1.2.1",
        "kind": "structural_heading_restored",
    },
]
EXPECTED_CHAPTER_MASTHEAD_REASSIGNMENTS = [
    {
        "previous_rule_id": previous_rule_id,
        "previous_rule_end_line": previous_rule_end_line,
        "page": page,
        "chapter": chapter,
        "source_line_ids": [f"p{page:04d}:l{line:03d}" for line in range(1, 7)],
    }
    for previous_rule_id, previous_rule_end_line, page, chapter in [
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
    ]
]
EXPECTED_DOCUMENT_IDS = [
    "P-1",
    "P-2",
    "P-3",
    "P-4",
    "P-5",
    "P-6a",
    "P-6b",
    "P-7",
    "P-8",
    "P-9",
    "P-10",
]
EXPECTED_DOCUMENT_NODE_KIND_COUNTS = {
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
}
CLAUSE_UNIT_KINDS = (
    "heading_text",
    "prose_text",
    "list_item_text",
    "example_label",
    "note_text",
    "table_caption",
    "table_cell_text",
    "table_footnote_marker",
    "table_footnote_text",
    "figure_caption",
    "caption_text",
    "footnote_marker",
    "footnote_text",
    "image_asset",
    "correction_event",
    "empty_table_cell",
    "empty_table",
)
TEXT_CLAUSE_UNIT_KINDS = set(CLAUSE_UNIT_KINDS[:13])
EXPECTED_CLAUSE_UNIT_KIND_COUNTS = {
    "heading_text": 3466,
    "prose_text": 7331,
    "list_item_text": 1111,
    "example_label": 1745,
    "note_text": 179,
    "table_caption": 38,
    "table_cell_text": 6217,
    "table_footnote_marker": 4,
    "table_footnote_text": 6,
    "figure_caption": 4573,
    "caption_text": 14,
    "footnote_marker": 3,
    "footnote_text": 3,
    "image_asset": 5181,
    "correction_event": 190,
    "empty_table_cell": 2346,
    "empty_table": 1,
}
EXPECTED_REGIONS = [
    ("contents", "front_matter", 1, 36),
    ("membership", "front_matter", 37, 38),
    ("preface_changes_acknowledgements", "front_matter", 39, 44),
    ("glossary", "glossary", 45, 47),
    ("chapters", "normative_chapters", 48, 1066),
    ("references", "references", 1067, 1069),
    ("appendix_1", "table", 1070, 1072),
    ("appendix_2", "table", 1073, 1120),
    ("appendix_3", "structure_registry", 1121, 1149),
]


class Audit:
    def __init__(self) -> None:
        self.errors: list[dict[str, Any]] = []
        self.metrics: dict[str, Any] = {}

    def fail(self, code: str, message: str, **context: Any) -> None:
        self.errors.append({"code": code, "message": message, "context": context})

    def require(self, condition: bool, code: str, message: str, **context: Any) -> None:
        if not condition:
            self.fail(code, message, **context)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def schema_errors(instance: Any, schema: Any) -> list[str]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path))
    ]


def has_control_characters(value: str) -> bool:
    return any((ord(char) < 32 and char not in "\n\t") or 127 <= ord(char) <= 159 for char in value)


def stripped_rule_text(
    rule_id: str,
    lines_by_id: dict[str, dict[str, Any]],
    spans: list[dict[str, Any]],
    restored: bool,
) -> str:
    values = [
        str(lines_by_id[span["line_id"]]["text"])[
            span["text_start"] : span["text_end"]
        ]
        for span in spans
    ]
    if values and not restored:
        values[0] = re.sub(rf"^{re.escape(rule_id)}[.;:]?\s*", "", values[0], count=1)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(values)).strip()


def validate_regions(source: dict[str, Any], audit: Audit) -> None:
    regions = source["source_document"]["document_regions"]
    observed = [
        (item["region_id"], item["source_kind"], item["page_start"], item["page_end"])
        for item in regions
    ]
    audit.require(observed == EXPECTED_REGIONS, "source.regions", "Document regions differ", observed=observed)
    covered = [page for _, _, start, end in observed for page in range(start, end + 1)]
    audit.require(
        covered == list(range(1, 1150)),
        "source.region_coverage",
        "Document regions must cover pages 1-1149 exactly once",
    )


def validate_pages(pages: dict[str, Any], audit: Audit) -> tuple[dict[str, dict[str, Any]], set[str]]:
    page_rows = pages.get("pages", [])
    audit.require(pages.get("page_count") == 1149, "pages.count_field", "Page count must be 1149")
    audit.require(len(page_rows) == 1149, "pages.count", "Page array must contain 1149 pages")
    audit.require(
        [item.get("page") for item in page_rows] == list(range(1, 1150)),
        "pages.sequence",
        "Page numbers must be contiguous and ordered",
    )
    lines_by_id: dict[str, dict[str, Any]] = {}
    image_objects = pages.get("image_objects", [])
    image_objects_by_id: dict[str, dict[str, Any]] = {}
    decoded_payload_hashes: set[str] = set()
    role_ids: dict[str, set[str]] = {
        "primary": set(),
        "soft_mask": set(),
        "explicit_mask": set(),
    }
    empty_sha256 = sha256_bytes(b"")
    for image_object in image_objects:
        image_object_id = image_object.get("image_object_id")
        audit.require(
            image_object_id not in image_objects_by_id,
            "pages.duplicate_image_object",
            "Duplicate PDF image object id",
            image_object_id=image_object_id,
        )
        expected_id = (
            f'pdf-image-object:{int(image_object.get("object_id", 0)):08d}:'
            f'{int(image_object.get("generation", 0)):05d}'
        )
        audit.require(
            image_object_id == expected_id,
            "pages.image_object_identity",
            "PDF image object id does not match its object reference",
            image_object_id=image_object_id,
        )
        for field in ("raw_sha256", "decoded_sha256"):
            value = str(image_object.get(field, ""))
            audit.require(
                bool(re.fullmatch(r"[A-F0-9]{64}", value)),
                "pages.image_object_hash",
                "Invalid PDF image object hash",
                image_object_id=image_object_id,
                field=field,
            )
            audit.require(
                value != empty_sha256,
                "pages.empty_image_object_hash",
                "PDF image object hashes an empty payload",
                image_object_id=image_object_id,
                field=field,
            )
        decoded_payload_hashes.add(str(image_object.get("decoded_sha256")))
        for role in image_object.get("roles", []):
            if role in role_ids:
                role_ids[role].add(str(image_object_id))
        image_objects_by_id[str(image_object_id)] = image_object

    for image_object in image_objects:
        for dependency in image_object.get("dependencies", []):
            target = dependency.get("target_image_object_id")
            relation = dependency.get("relation")
            audit.require(
                target in image_objects_by_id,
                "pages.image_dependency_target",
                "Image mask dependency target is absent",
                source=image_object.get("image_object_id"),
                target=target,
            )
            expected_role = "soft_mask" if relation == "soft_mask" else "explicit_mask"
            audit.require(
                target in role_ids[expected_role],
                "pages.image_dependency_role",
                "Image mask dependency target has the wrong role",
                source=image_object.get("image_object_id"),
                target=target,
                relation=relation,
            )

    placement_ids: set[str] = set()
    placement_object_ids: set[str] = set()
    page_object_pairs: set[tuple[int, str]] = set()
    image_count = 0
    for page in page_rows:
        page_number = page["page"]
        region = next(item for item in EXPECTED_REGIONS if item[2] <= page_number <= item[3])
        audit.require(page.get("region_id") == region[0], "pages.region", "Wrong page region", page=page_number)
        audit.require(page.get("source_kind") == region[1], "pages.kind", "Wrong page kind", page=page_number)
        page_lines = page.get("lines", [])
        reconstructed = "\n".join(str(line.get("text", "")) for line in page_lines).strip()
        audit.require(
            reconstructed == page.get("text"),
            "pages.text_reconstruction",
            "Page text does not reconstruct from lines",
            page=page_number,
        )
        for line in page_lines:
            uid = line.get("uid")
            audit.require(uid not in lines_by_id, "pages.duplicate_line", "Duplicate line id", line_id=uid)
            audit.require(line.get("page") == page_number, "pages.line_page", "Line page mismatch", line_id=uid)
            audit.require(not has_control_characters(str(line.get("text", ""))), "pages.control", "Control character in PDF line", line_id=uid)
            audit.require(bool(line.get("runs")), "pages.runs", "Line has no glyph-style runs", line_id=uid)
            lines_by_id[uid] = line
        for image in page.get("images", []):
            placement_id = image.get("placement_id")
            image_object_id = str(image.get("image_object_id"))
            audit.require(placement_id not in placement_ids, "pages.duplicate_image", "Duplicate image placement id", placement_id=placement_id)
            audit.require(image.get("page") == page_number, "pages.image_page", "Image page mismatch", placement_id=placement_id)
            expected_object_id = (
                f'pdf-image-object:{int(image.get("object_id", 0)):08d}:'
                f'{int(image.get("generation", 0)):05d}'
            )
            audit.require(
                image_object_id == expected_object_id,
                "pages.placement_object_identity",
                "Image placement object id does not match its object reference",
                placement_id=placement_id,
            )
            audit.require(
                image_object_id in image_objects_by_id,
                "pages.placement_object_target",
                "Image placement references an absent image object",
                placement_id=placement_id,
                image_object_id=image_object_id,
            )
            placement_ids.add(str(placement_id))
            placement_object_ids.add(image_object_id)
            page_object_pairs.add((page_number, image_object_id))
            image_count += 1
    audit.require(
        placement_object_ids == role_ids["primary"],
        "pages.primary_image_closure",
        "Primary image-object roles do not equal placement targets",
    )
    computed_image_metrics = {
        "placement_count": image_count,
        "page_primary_object_count": len(page_object_pairs),
        "primary_object_count": len(role_ids["primary"]),
        "soft_mask_object_count": len(role_ids["soft_mask"]),
        "explicit_mask_object_count": len(role_ids["explicit_mask"]),
        "image_object_count": len(image_objects_by_id),
        "unique_decoded_payload_count": len(decoded_payload_hashes),
    }
    audit.require(
        pages.get("image_metrics") == computed_image_metrics,
        "pages.image_metrics",
        "Declared PDF image metrics do not match image objects and placements",
        computed=computed_image_metrics,
    )
    expected_image_metrics = {
        "placement_count": 5413,
        "page_primary_object_count": 5342,
        "primary_object_count": 5294,
        "soft_mask_object_count": 5226,
        "explicit_mask_object_count": 6,
        "image_object_count": 10526,
    }
    audit.require(
        all(computed_image_metrics[key] == value for key, value in expected_image_metrics.items()),
        "pages.image_source_invariants",
        "PDF image-object invariants differ from the pinned source",
        computed=computed_image_metrics,
    )
    audit.metrics["page_count"] = len(page_rows)
    audit.metrics["source_line_count"] = len(lines_by_id)
    audit.metrics.update(
        {f"pdf_{key}": value for key, value in computed_image_metrics.items()}
    )
    return lines_by_id, placement_ids


def validate_source_corpus(
    source: dict[str, Any],
    pages: dict[str, Any],
    source_schema: dict[str, Any],
    pages_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    audit = Audit()
    for error in schema_errors(source, source_schema):
        audit.fail("source.schema", error)
    if pages_schema is not None:
        for error in schema_errors(pages, pages_schema):
            audit.fail("pages.schema", error)
    validate_regions(source, audit)
    lines_by_id, _ = validate_pages(pages, audit)

    records = source.get("records", [])
    blocks = source.get("non_rule_blocks", [])
    record_ids = [record.get("record_id") for record in records]
    rule_ids = [record.get("source_rule_id") for record in records]
    html_anchors = [(record.get("html") or {}).get("url", "") + "#" + (record.get("html") or {}).get("anchor", "") for record in records]
    audit.require(len(records) == 2554, "source.rule_count", "Expected 2554 reconciled rules", observed=len(records))
    audit.require(len(set(record_ids)) == len(record_ids), "source.record_ids", "Duplicate record ids")
    audit.require(len(set(rule_ids)) == len(rule_ids), "source.rule_ids", "Duplicate rule ids")
    anchor_groups: dict[str, list[str]] = {}
    for href, rule_id in zip(html_anchors, rule_ids):
        anchor_groups.setdefault(href, []).append(rule_id)
    observed_anchor_collisions = [
        {"href": href, "rule_ids": sorted(ids)}
        for href, ids in sorted(anchor_groups.items())
        if len(ids) > 1
    ]
    declared_anchor_collisions = source.get("reconciliation", {}).get(
        "source_html_anchor_collisions", []
    )
    audit.require(
        observed_anchor_collisions == EXPECTED_HTML_ANCHOR_COLLISIONS,
        "source.html_anchor_collisions",
        "Official HTML anchor collisions differ from the audited source",
        observed=observed_anchor_collisions,
    )
    audit.require(
        declared_anchor_collisions == observed_anchor_collisions,
        "source.html_anchor_collision_provenance",
        "Declared HTML anchor collisions do not match source records",
    )
    audit.require(all(RULE_ID_RE.fullmatch(str(value)) for value in rule_ids), "source.rule_format", "Invalid rule id")
    chapters = Counter(record.get("chapter") for record in records)
    audit.require(dict(chapters) == EXPECTED_CHAPTER_COUNTS, "source.chapter_counts", "Chapter counts differ", observed=dict(chapters))

    owned_rule_lines: set[str] = set()
    character_ownership: dict[str, list[tuple[int, int, str]]] = {
        line_id: [] for line_id in lines_by_id
    }
    owned_rule_characters = 0
    for record in records:
        rule_id = record["source_rule_id"]
        audit.require(record["record_id"] == f"bluebook-v3:{rule_id}", "source.record_identity", "Record id mismatch", rule_id=rule_id)
        pdf = record["pdf"]
        html = record["html"]
        alignment = record["alignment"]
        audit.require(
            alignment["html_rule_id"] == rule_id,
            "source.alignment_html_id",
            "Alignment HTML id differs from record id",
            rule_id=rule_id,
        )
        audit.require(
            alignment["pdf_rule_id"] == pdf["printed_rule_id"],
            "source.alignment_pdf_id",
            "Alignment PDF id differs from printed rule id",
            rule_id=rule_id,
        )
        line_ids = pdf["source_line_ids"]
        spans = pdf["source_spans"]
        missing = [line_id for line_id in line_ids if line_id not in lines_by_id]
        audit.require(not missing, "source.missing_lines", "Rule references absent source lines", rule_id=rule_id, missing=missing)
        if missing:
            continue
        source_lines = [lines_by_id[line_id] for line_id in line_ids]
        span_line_ids = list(dict.fromkeys(span["line_id"] for span in spans))
        audit.require(
            span_line_ids == line_ids,
            "source.span_line_ids",
            "PDF source-line ids do not project from ordered source spans",
            rule_id=rule_id,
        )
        for span in spans:
            line_id = span["line_id"]
            if line_id not in lines_by_id:
                continue
            text_length = len(str(lines_by_id[line_id]["text"]))
            start = int(span["text_start"])
            end = int(span["text_end"])
            audit.require(
                0 <= start < end <= text_length,
                "source.span_bounds",
                "PDF text span falls outside its source line",
                rule_id=rule_id,
                line_id=line_id,
                start=start,
                end=end,
                text_length=text_length,
            )
            if 0 <= start < end <= text_length:
                character_ownership[line_id].append((start, end, rule_id))
                owned_rule_characters += end - start
        audit.require(pdf["start_line"] == line_ids[0], "source.start_line", "Wrong start line", rule_id=rule_id)
        audit.require(pdf["end_line"] == line_ids[-1], "source.end_line", "Wrong end line", rule_id=rule_id)
        audit.require(pdf["pages"] == sorted({line["page"] for line in source_lines}), "source.pages", "Wrong page provenance", rule_id=rule_id)
        restored = record["alignment"]["kind"] == "structural_heading_restored"
        expected_text = stripped_rule_text(
            pdf["printed_rule_id"], lines_by_id, spans, restored
        )
        audit.require(pdf["text"] == expected_text, "source.pdf_text", "PDF rule text mismatch", rule_id=rule_id)
        audit.require(pdf["text_sha256"] == sha256_text(pdf["text"]), "source.pdf_hash", "PDF text hash mismatch", rule_id=rule_id)
        audit.require(html["text_sha256"] == sha256_text(html["text"]), "source.html_hash", "HTML text hash mismatch", rule_id=rule_id)
        audit.require(not has_control_characters(html["text"]), "source.html_control", "Control character in HTML text", rule_id=rule_id)
        for marker in MOJIBAKE_MARKERS:
            audit.require(marker not in html["text"], "source.mojibake", "Mojibake marker in HTML text", rule_id=rule_id, marker=marker)
        audit.require(all(48 <= page <= 1066 for page in pdf["pages"]), "source.rule_region", "Rule outside normative pages", rule_id=rule_id)
        owned_rule_lines.update(line_ids)

    restored = [record for record in records if record["alignment"]["kind"] == "structural_heading_restored"]
    audit.require([record["source_rule_id"] for record in restored] == ["P-65.1.2.1"], "source.restored_heading", "Unexpected restored headings")
    heading = next((record for record in records if record["source_rule_id"] == "P-65.1.2"), None)
    child = next((record for record in records if record["source_rule_id"] == "P-65.1.2.1"), None)
    audit.require(
        bool(
            heading
            and heading["source_kind"] == "rule_section"
            and heading["pdf"]["source_line_ids"]
            == ["p0572:l023", "p0572:l024", "p0572:l025"]
            and heading["pdf"]["source_spans"][-1]
            == {
                "line_id": "p0572:l025",
                "text_start": 0,
                "text_end": 9,
                "role": "printed_text_splice_parent",
            }
        ),
        "source.restored_parent",
        "P-65.1.2 must retain the heading and its partial PDF introduction",
    )
    audit.require(
        bool(
            child
            and child["pdf"]["source_line_ids"]
            == [f"p0572:l{line:03d}" for line in range(25, 38)]
            and child["pdf"]["source_spans"][0]["line_id"] == "p0572:l025"
            and child["pdf"]["source_spans"][0]["text_start"] == 9
            and child["pdf"]["source_spans"][0]["role"]
            == "printed_text_splice_child"
        ),
        "source.restored_child",
        "P-65.1.2.1 must own the post-omission PDF lines",
    )
    audit.require(
        bool(
            heading
            and heading["alignment"].get("defect")
            == {
                "kind": "printed_text_splice",
                "line_id": "p0572:l025",
                "split_index": 9,
            }
        ),
        "source.printed_text_splice_provenance",
        "P-65.1.2 must declare its exact intra-line PDF splice",
    )
    p331 = next((record for record in records if record["source_rule_id"] == "P-33.1"), None)
    p332 = next((record for record in records if record["source_rule_id"] == "P-33.2"), None)
    audit.require(
        bool(
            p331
            and p331["pdf"]["source_line_ids"]
            == [
                *[f"p0341:l{line:03d}" for line in range(3, 32)],
                *[f"p0341:l{line:03d}" for line in range(33, 51)],
            ]
        ),
        "source.layout_reordering_p331",
        "P-33.1 example lines are not assigned around the floating P-33.2 label",
    )
    audit.require(
        bool(
            p332
            and p332["source_kind"] == "section_heading"
            and p332["pdf"]["source_line_ids"] == ["p0341:l032", "p0341:l051"]
        ),
        "source.layout_reordering_p332",
        "P-33.2 must join its noncontiguous printed label and title",
    )
    p254342 = next(
        (record for record in records if record["source_rule_id"] == "P-25.4.3.4.2"),
        None,
    )
    audit.require(
        bool(
            p254342
            and p254342["alignment"].get("defect")
            == {
                "kind": "source_gap_restoration",
                "after_line": "p0247:l007",
                "before_line": "p0247:l008",
                "authoritative_source": "official_corrected_html",
            }
            and any(
                left == "p0247:l007" and right == "p0247:l008"
                for left, right in zip(
                    p254342["pdf"]["source_line_ids"],
                    p254342["pdf"]["source_line_ids"][1:],
                )
            )
        ),
        "source.pdf_text_omission_provenance",
        "P-25.4.3.4.2 must preserve the audited PDF gap and HTML restoration source",
    )
    observed_anomalies = [
        {"rule_id": record["source_rule_id"], "kind": record["alignment"]["kind"]}
        for record in records
        if record["alignment"]["kind"] != "rule_id_exact"
    ]
    audit.require(
        observed_anomalies == EXPECTED_SOURCE_ANOMALIES,
        "source.audited_anomalies",
        "Audited source anomaly set differs",
        observed=observed_anomalies,
    )
    declared_anomalies = source["reconciliation"]["audited_source_anomalies"]
    expected_declared_anomalies = [
        {
            "rule_id": record["source_rule_id"],
            "kind": record["alignment"]["kind"],
            "reason": record["alignment"]["reason"],
            **(
                {"defect": record["alignment"]["defect"]}
                if "defect" in record["alignment"]
                else {}
            ),
        }
        for record in records
        if record["alignment"]["kind"] != "rule_id_exact"
    ]
    audit.require(
        declared_anomalies == expected_declared_anomalies,
        "source.anomaly_provenance",
        "Declared source anomalies differ from record alignments",
    )
    last = next((record for record in records if record["source_rule_id"] == "P-107.4.3.3"), None)
    audit.require(bool(last and last["pdf"]["pages"] == [1066]), "source.terminal_boundary", "Terminal rule must end on page 1066")

    owned_block_lines: set[str] = set()
    owned_block_characters = 0
    for block in blocks:
        line_ids = block["source_line_ids"]
        missing = [line_id for line_id in line_ids if line_id not in lines_by_id]
        audit.require(not missing, "source.block_missing_lines", "Block references absent source lines", block_id=block["block_id"], missing=missing)
        if missing:
            continue
        block_lines = [lines_by_id[line_id] for line_id in line_ids]
        expected_text = "\n".join(line["text"] for line in block_lines).strip()
        audit.require(block["text"] == expected_text, "source.block_text", "Block text mismatch", block_id=block["block_id"])
        audit.require(block["text_sha256"] == sha256_text(block["text"]), "source.block_hash", "Block hash mismatch", block_id=block["block_id"])
        audit.require(block["pages"] == sorted({line["page"] for line in block_lines}), "source.block_pages", "Block page mismatch", block_id=block["block_id"])
        owned_block_lines.update(line_ids)
        for line_id in line_ids:
            owned_block_characters += len(str(lines_by_id[line_id]["text"]))
            character_ownership[line_id].append(
                (0, len(str(lines_by_id[line_id]["text"])), block["block_id"])
            )

    declared_mastheads = source.get("reconciliation", {}).get(
        "chapter_masthead_reassignments", []
    )
    normalized_declared_mastheads = [
        {
            key: item.get(key)
            for key in (
                "previous_rule_id",
                "previous_rule_end_line",
                "page",
                "chapter",
                "source_line_ids",
            )
        }
        for item in declared_mastheads
    ]
    audit.require(
        normalized_declared_mastheads == EXPECTED_CHAPTER_MASTHEAD_REASSIGNMENTS,
        "source.chapter_masthead_provenance",
        "Declared chapter-masthead transfers differ from the audited source",
        observed=normalized_declared_mastheads,
    )
    records_by_id = {record["source_rule_id"]: record for record in records}
    blocks_by_id = {block["block_id"]: block for block in blocks}
    declarations_by_rule = {
        item.get("previous_rule_id"): item for item in declared_mastheads
    }
    for expected in EXPECTED_CHAPTER_MASTHEAD_REASSIGNMENTS:
        previous = records_by_id.get(expected["previous_rule_id"])
        declaration = declarations_by_rule.get(expected["previous_rule_id"], {})
        block = blocks_by_id.get(declaration.get("block_id"))
        audit.require(
            bool(
                previous
                and previous["pdf"]["end_line"] == expected["previous_rule_end_line"]
                and not set(expected["source_line_ids"]).intersection(
                    previous["pdf"]["source_line_ids"]
                )
            ),
            "source.chapter_terminal_boundary",
            "A terminal rule still owns text from the following chapter title page",
            previous_rule_id=expected["previous_rule_id"],
        )
        audit.require(
            bool(
                block
                and block["source_kind"] == "chapter_front_matter"
                and block["chapter"] == expected["chapter"]
                and block["source_line_ids"][:6] == expected["source_line_ids"]
            ),
            "source.chapter_masthead_owner",
            "Chapter masthead lines are not owned by the declared chapter-front-matter block",
            previous_rule_id=expected["previous_rule_id"],
            block_id=declaration.get("block_id"),
        )

    unowned_lines: list[str] = []
    character_gaps: list[dict[str, Any]] = []
    character_overlaps: list[dict[str, Any]] = []
    for line_id, line in lines_by_id.items():
        text_length = len(str(line["text"]))
        intervals = sorted(character_ownership[line_id])
        if not intervals:
            unowned_lines.append(line_id)
            continue
        cursor = 0
        for start, end, owner in intervals:
            if start > cursor:
                character_gaps.append(
                    {"line_id": line_id, "start": cursor, "end": start}
                )
            if start < cursor:
                character_overlaps.append(
                    {"line_id": line_id, "start": start, "end": end, "owner": owner}
                )
            cursor = max(cursor, end)
        if cursor < text_length:
            character_gaps.append(
                {"line_id": line_id, "start": cursor, "end": text_length}
            )
    audit.require(
        not unowned_lines,
        "source.unowned_lines",
        "Source lines have no owner",
        sample=unowned_lines[:20],
    )
    audit.require(
        not character_gaps,
        "source.character_gaps",
        "PDF source characters have no owner",
        sample=character_gaps[:20],
    )
    audit.require(
        not character_overlaps,
        "source.character_overlap",
        "PDF source characters have multiple owners",
        sample=character_overlaps[:20],
    )
    audit.require(
        owned_rule_lines.union(owned_block_lines) == set(lines_by_id),
        "source.line_coverage",
        "Line ownership is incomplete",
    )
    source_characters = sum(len(str(line["text"])) for line in lines_by_id.values())
    audit.require(
        owned_rule_characters + owned_block_characters == source_characters,
        "source.character_counter_sum",
        "Owned rule and non-rule character counts do not cover the source exactly",
    )
    for field, observed in {
        "source_character_count": source_characters,
        "owned_rule_source_character_count": owned_rule_characters,
        "owned_non_rule_source_character_count": owned_block_characters,
    }.items():
        audit.require(
            source.get(field) == observed,
            "source.counter.characters",
            "PDF source character counter mismatch",
            field=field,
            observed=observed,
        )

    document = source["source_document"]
    reconciliation = source["reconciliation"]
    audit.require(document["page_count"] == 1149, "source.document_pages", "Wrong source document page count")
    audit.require(document["local_pdf_sha256"] == "F577437DA72309EE28B49D290A441BDD340E709225FD07794D7EFCFE5B593F74", "source.pdf_digest", "Unexpected PDF digest")
    audit.require(reconciliation["pdf_rule_count"] == 2553, "source.pdf_heading_count", "Expected 2553 PDF headings")
    audit.require(reconciliation["html_rule_count"] == 2554, "source.html_anchor_count", "Expected 2554 active HTML anchors")
    audit.require(reconciliation["merged_rule_count"] == 2554, "source.merged_count", "Expected 2554 merged records")
    audit.require(reconciliation["pdf_only_rule_ids"] == [], "source.pdf_only", "PDF-only rule ids remain")
    audit.require(reconciliation["html_only_structurally_aligned_rule_ids"] == ["P-65.1.2.1"], "source.html_only", "Unexpected HTML-only alignment")

    audit.require(source["rule_record_count"] == len(records), "source.counter.rules", "Rule counter mismatch")
    audit.require(source["non_rule_block_count"] == len(blocks), "source.counter.blocks", "Block counter mismatch")
    audit.require(source["source_line_count"] == len(lines_by_id), "source.counter.lines", "Line counter mismatch")
    audit.require(source["owned_rule_source_line_count"] == len(owned_rule_lines), "source.counter.rule_lines", "Rule line counter mismatch")
    audit.require(source["owned_non_rule_source_line_count"] == len(owned_block_lines), "source.counter.block_lines", "Block line counter mismatch")
    source_image_counter_map = {
        "pdf_image_placement_count": "pdf_placement_count",
        "pdf_page_primary_image_object_count": "pdf_page_primary_object_count",
        "pdf_primary_image_object_count": "pdf_primary_object_count",
        "pdf_soft_mask_image_object_count": "pdf_soft_mask_object_count",
        "pdf_explicit_mask_image_object_count": "pdf_explicit_mask_object_count",
        "pdf_image_object_count": "pdf_image_object_count",
        "pdf_unique_decoded_image_payload_count": "pdf_unique_decoded_payload_count",
    }
    for source_field, metric_field in source_image_counter_map.items():
        audit.require(
            source[source_field] == audit.metrics[metric_field],
            "source.counter.images",
            "PDF image counter mismatch",
            field=source_field,
        )

    audit.metrics.update(
        {
            "rule_record_count": len(records),
            "non_rule_block_count": len(blocks),
            "owned_rule_source_line_count": len(owned_rule_lines),
            "owned_non_rule_source_line_count": len(owned_block_lines),
            "source_character_count": source_characters,
            "owned_rule_source_character_count": owned_rule_characters,
            "owned_non_rule_source_character_count": owned_block_characters,
            "chapter_counts": dict(chapters),
            "html_rule_image_reference_count": sum(len(record["html"]["image_urls"]) for record in records),
            "html_table_element_count": sum(record["html"]["table_count"] for record in records),
            "structurally_restored_rule_count": len(restored),
        }
    )
    return {"passed": not audit.errors, "error_count": len(audit.errors), "errors": audit.errors, "metrics": audit.metrics}


def iter_document_nodes(nodes: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for node in nodes:
        yield node
        yield from iter_document_nodes(node.get("children", []))


def iter_provenance_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if set(value) == {"parts", "manifest_sha256", "ownership"}:
            yield value
            return
        for child in value.values():
            yield from iter_provenance_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_provenance_objects(child)


def document_node_source_metrics(nodes: Iterable[dict[str, Any]]) -> dict[str, Any]:
    table_count = 0
    row_count = 0
    cell_count = 0
    image_count = 0
    correction_count = 0
    footnote_count = 0
    table_captions: list[str] = []
    figure_captions: list[str] = []
    for node in nodes:
        kind = node.get("kind")
        if kind == "figure":
            image_count += len(node.get("images", []))
        elif kind == "source_event":
            image_count += 1
            correction_count += int(node.get("event_kind") == "correction")
        elif kind == "footnote":
            footnote_count += 1
        elif kind == "orphan_cell":
            cell_count += 1
            image_count += len(node.get("images", []))
            image_count += len(node.get("source_events", []))
            correction_count += sum(
                event.get("event_kind") == "correction"
                for event in node.get("source_events", [])
            )
        elif kind == "table":
            table_count += 1
            footnote_count += len(node.get("footnotes", []))
            image_count += len(node.get("images", []))
            image_count += len(node.get("source_events", []))
            correction_count += sum(
                event.get("event_kind") == "correction"
                for event in node.get("source_events", [])
            )
            for row in node.get("rows", []):
                row_count += 1
                image_count += len(row.get("images", []))
                image_count += len(row.get("source_events", []))
                correction_count += sum(
                    event.get("event_kind") == "correction"
                    for event in row.get("source_events", [])
                )
                for cell in row.get("cells", []):
                    cell_count += 1
                    image_count += len(cell.get("images", []))
                    image_count += len(cell.get("source_events", []))
                    correction_count += sum(
                        event.get("event_kind") == "correction"
                        for event in cell.get("source_events", [])
                    )
            for cell in node.get("orphan_cells", []):
                cell_count += 1
                image_count += len(cell.get("images", []))
                image_count += len(cell.get("source_events", []))
                correction_count += sum(
                    event.get("event_kind") == "correction"
                    for event in cell.get("source_events", [])
                )
        if kind in {"table", "figure", "caption"} and node.get("caption_label"):
            if node.get("caption_kind") == "table":
                table_captions.append(node["caption_label"])
            elif node.get("caption_kind") == "figure":
                figure_captions.append(node["caption_label"])
    return {
        "physical_table_occurrence_count": table_count,
        "physical_row_occurrence_count": row_count,
        "physical_cell_occurrence_count": cell_count,
        "physical_image_occurrence_count": image_count,
        "correction_event_count": correction_count,
        "footnote_block_count": footnote_count,
        "visible_table_captions": table_captions,
        "visible_figure_captions": figure_captions,
    }


def validate_document_node_corpus(
    document_nodes: dict[str, Any],
    document_schema: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    audit = Audit()
    for error in schema_errors(document_nodes, document_schema):
        audit.fail("document_nodes.schema", error)

    documents = document_nodes.get("documents", [])
    audit.require(
        [document.get("document_id") for document in documents] == EXPECTED_DOCUMENT_IDS,
        "document_nodes.documents",
        "Document-node chapter sources differ from the complete official chapter set",
    )
    source_manifests = {
        item["chapter"]: item for item in source["source_document"]["html_sources"]
    }
    source_records = {
        record["source_rule_id"]: record for record in source["records"]
    }
    fragment_rule_ids: list[str] = []
    global_kind_counts: Counter[str] = Counter()
    global_nodes: list[dict[str, Any]] = []
    global_node_ids: list[str] = []
    provenance_count = 0
    multipart_provenance_count = 0
    field_source_metrics: Counter[str] = Counter()

    for document in documents:
        document_id = document.get("document_id")
        manifest = source_manifests.get(document_id)
        cache_path = ROOT / ".cache" / "bluebook_html" / str(document.get("cache_path", ""))
        audit.require(
            cache_path.is_file(),
            "document_nodes.source_file",
            "Cached official chapter HTML is absent",
            document_id=document_id,
            path=str(cache_path),
        )
        raw = cache_path.read_bytes() if cache_path.is_file() else b""
        audit.require(
            bool(
                manifest
                and manifest["url"] == document.get("source_url")
                and manifest["sha256"] == document.get("source_sha256")
                and manifest["byte_count"] == document.get("source_byte_count")
                and document.get("source_sha256") == sha256_bytes(raw)
                and document.get("source_byte_count") == len(raw)
            ),
            "document_nodes.source_manifest",
            "Document-node source differs from the lossless source manifest",
            document_id=document_id,
        )
        comment_ranges = [match.span() for match in HTML_COMMENT_RE.finditer(raw)]
        fragments = document.get("fragments", [])
        audit.require(
            [fragment.get("ordinal") for fragment in fragments]
            == list(range(1, len(fragments) + 1)),
            "document_nodes.fragment_ordinals",
            "Rule-fragment ordinals are not contiguous",
            document_id=document_id,
        )
        audit.require(
            all(
                left.get("source", {}).get("end_byte")
                == right.get("source", {}).get("start_byte")
                for left, right in zip(fragments, fragments[1:])
            ),
            "document_nodes.fragment_contiguity",
            "Adjacent active rule fragments do not share an exact source boundary",
            document_id=document_id,
        )
        document_nodes_flat: list[dict[str, Any]] = []
        for fragment in fragments:
            rule_id = fragment.get("rule_id")
            fragment_rule_ids.append(rule_id)
            source_record = source_records.get(rule_id)
            audit.require(
                bool(
                    source_record
                    and source_record["html"]["url"] == document.get("source_url")
                    and source_record["html"]["anchor"] == fragment.get("anchor")
                ),
                "document_nodes.fragment_source",
                "Document-node fragment does not map to its source-corpus HTML anchor",
                rule_id=rule_id,
            )
            fragment_source = fragment.get("source", {})
            start = int(fragment_source.get("start_byte", -1))
            end = int(fragment_source.get("end_byte", -1))
            anchor_start = int(fragment_source.get("anchor_start_byte", -1))
            in_bounds = 0 <= start <= anchor_start < end <= len(raw)
            audit.require(
                in_bounds,
                "document_nodes.fragment_range",
                "Rule-fragment byte range is outside its cached source",
                rule_id=rule_id,
            )
            if in_bounds:
                raw_fragment = raw[start:end]
                audit.require(
                    fragment_source.get("raw_sha256") == sha256_bytes(raw_fragment),
                    "document_nodes.fragment_hash",
                    "Raw rule-fragment digest mismatch",
                    rule_id=rule_id,
                )
                audit.require(
                    fragment_source.get("active_sha256")
                    == sha256_bytes(HTML_COMMENT_RE.sub(b"", raw_fragment)),
                    "document_nodes.active_fragment_hash",
                    "Comment-free active rule-fragment digest mismatch",
                    rule_id=rule_id,
                )
                try:
                    replay_metrics = validate_fragment_field_sources(
                        fragment, raw_fragment
                    )
                except (KeyError, TypeError, ValueError) as error:
                    audit.fail(
                        "document_nodes.field_replay",
                        str(error),
                        rule_id=rule_id,
                    )
                else:
                    audit.require(
                        fragment.get("field_source_metrics") == replay_metrics,
                        "document_nodes.field_metrics",
                        "Field-source metrics do not reconstruct",
                        rule_id=rule_id,
                    )
                    field_source_metrics.update(replay_metrics)
            fragment_nodes = list(iter_document_nodes(fragment.get("nodes", [])))
            document_nodes_flat.extend(fragment_nodes)
            audit.require(
                fragment.get("node_count") == len(fragment_nodes),
                "document_nodes.fragment_counter",
                "Rule-fragment node counter mismatch",
                rule_id=rule_id,
            )
            audit.require(
                [node.get("ordinal") for node in fragment_nodes]
                == list(range(1, len(fragment_nodes) + 1)),
                "document_nodes.node_ordinals",
                "Node ordinals are not contiguous within a rule fragment",
                rule_id=rule_id,
            )
            expected_node_ids = [
                f"{rule_id}:node:{ordinal:04d}"
                for ordinal in range(1, len(fragment_nodes) + 1)
            ]
            observed_node_ids = [node.get("node_id") for node in fragment_nodes]
            audit.require(
                observed_node_ids == expected_node_ids,
                "document_nodes.node_ids",
                "Node ids do not reconstruct from their rule and ordinal",
                rule_id=rule_id,
            )
            global_node_ids.extend(observed_node_ids)
            for provenance in iter_provenance_objects(fragment.get("nodes", [])):
                provenance_count += 1
                parts = provenance.get("parts", [])
                multipart_provenance_count += len(parts) > 1
                audit.require(
                    provenance.get("manifest_sha256") == sha256_bytes(canonical_json_bytes(parts)),
                    "document_nodes.provenance_manifest",
                    "Node provenance manifest digest mismatch",
                    rule_id=rule_id,
                )
                audit.require(
                    parts
                    == sorted(
                        parts,
                        key=lambda part: (
                            part.get("document_start_byte", -1),
                            part.get("document_end_byte", -1),
                            part.get("dom_path", ""),
                        ),
                    ),
                    "document_nodes.provenance_order",
                    "Node provenance parts are not in source order",
                    rule_id=rule_id,
                )
                for part in parts:
                    fragment_start = int(part.get("fragment_start_byte", -1))
                    fragment_end = int(part.get("fragment_end_byte", -1))
                    document_start = int(part.get("document_start_byte", -1))
                    document_end = int(part.get("document_end_byte", -1))
                    part_in_bounds = (
                        in_bounds
                        and 0 <= fragment_start < fragment_end <= end - start
                        and start <= document_start < document_end <= end
                        and document_start == start + fragment_start
                        and document_end == start + fragment_end
                    )
                    audit.require(
                        part_in_bounds,
                        "document_nodes.provenance_range",
                        "Node provenance part falls outside its rule fragment",
                        rule_id=rule_id,
                    )
                    if part_in_bounds:
                        exact = raw[document_start:document_end]
                        audit.require(
                            part.get("raw_sha256") == sha256_bytes(exact),
                            "document_nodes.provenance_hash",
                            "Node provenance bytes do not reproduce their digest",
                            rule_id=rule_id,
                        )
                        audit.require(
                            not any(
                                comment_start < document_end
                                and document_start < comment_end
                                for comment_start, comment_end in comment_ranges
                            ),
                            "document_nodes.comment_leak",
                            "Deleted HTML comment bytes appear in an active node",
                            rule_id=rule_id,
                        )

        document_kind_counts = Counter(node.get("kind") for node in document_nodes_flat)
        document_metrics = document_node_source_metrics(document_nodes_flat)
        expected_document_metrics = {
            "physical_table_occurrence_count": document_metrics[
                "physical_table_occurrence_count"
            ],
            "physical_row_occurrence_count": document_metrics[
                "physical_row_occurrence_count"
            ],
            "physical_cell_occurrence_count": document_metrics[
                "physical_cell_occurrence_count"
            ],
            "physical_image_occurrence_count": document_metrics[
                "physical_image_occurrence_count"
            ],
            "correction_event_count": document_metrics["correction_event_count"],
            "footnote_block_count": document_metrics["footnote_block_count"],
            "visible_table_caption_count": len(
                document_metrics["visible_table_captions"]
            ),
            "visible_figure_caption_count": len(
                document_metrics["visible_figure_captions"]
            ),
        }
        audit.require(
            document.get("active_rule_fragment_count") == len(fragments)
            and (manifest is None or manifest["active_rule_anchor_count"] == len(fragments)),
            "document_nodes.document_fragment_counter",
            "Document active-fragment counter mismatch",
            document_id=document_id,
        )
        audit.require(
            document.get("document_node_count") == len(document_nodes_flat),
            "document_nodes.document_node_counter",
            "Document node counter mismatch",
            document_id=document_id,
        )
        audit.require(
            document.get("node_kind_counts")
            == {kind: document_kind_counts[kind] for kind in EXPECTED_DOCUMENT_NODE_KIND_COUNTS},
            "document_nodes.document_kind_counters",
            "Document node-kind counters do not reconstruct",
            document_id=document_id,
        )
        audit.require(
            document.get("source_metrics") == expected_document_metrics,
            "document_nodes.document_metrics",
            "Document source metrics do not reconstruct",
            document_id=document_id,
        )
        global_nodes.extend(document_nodes_flat)
        global_kind_counts.update(document_kind_counts)

    audit.require(
        len(set(fragment_rule_ids)) == len(fragment_rule_ids)
        and set(fragment_rule_ids) == set(source_records),
        "document_nodes.rule_coverage",
        "Document-node rule coverage differs from the lossless source corpus",
    )
    audit.require(
        len(set(global_node_ids)) == len(global_node_ids),
        "document_nodes.global_node_ids",
        "Document node ids are not globally unique",
    )
    global_metrics = document_node_source_metrics(global_nodes)
    expected_counters = {
        "document_count": len(documents),
        "active_rule_fragment_count": len(fragment_rule_ids),
        "document_node_count": len(global_nodes),
        "node_kind_counts": {
            kind: global_kind_counts[kind] for kind in EXPECTED_DOCUMENT_NODE_KIND_COUNTS
        },
    }
    expected_metrics = {
        "physical_table_occurrence_count": global_metrics[
            "physical_table_occurrence_count"
        ],
        "physical_row_occurrence_count": global_metrics[
            "physical_row_occurrence_count"
        ],
        "physical_cell_occurrence_count": global_metrics[
            "physical_cell_occurrence_count"
        ],
        "physical_image_occurrence_count": global_metrics[
            "physical_image_occurrence_count"
        ],
        "correction_event_count": global_metrics["correction_event_count"],
        "footnote_block_count": global_metrics["footnote_block_count"],
        "visible_table_caption_count": len(global_metrics["visible_table_captions"]),
        "distinct_visible_table_caption_count": len(
            {label.rstrip(".") for label in global_metrics["visible_table_captions"]}
        ),
        "visible_figure_caption_count": len(global_metrics["visible_figure_captions"]),
        "distinct_visible_figure_caption_count": len(
            {label.rstrip(".") for label in global_metrics["visible_figure_captions"]}
        ),
    }
    audit.require(
        document_nodes.get("counters") == expected_counters,
        "document_nodes.counters",
        "Corpus document-node counters do not reconstruct",
    )
    audit.require(
        document_nodes.get("metrics") == expected_metrics,
        "document_nodes.metrics",
        "Corpus document-node metrics do not reconstruct",
    )
    audit.require(
        expected_counters["document_count"] == 11
        and expected_counters["active_rule_fragment_count"] == 2554
        and expected_counters["document_node_count"] == 14453
        and expected_counters["node_kind_counts"] == EXPECTED_DOCUMENT_NODE_KIND_COUNTS
        and expected_metrics["physical_table_occurrence_count"] == 567
        and expected_metrics["physical_row_occurrence_count"] == 3782
        and expected_metrics["physical_cell_occurrence_count"] == 9100
        and expected_metrics["physical_image_occurrence_count"] == 5371
        and expected_metrics["correction_event_count"] == 190
        and expected_metrics["footnote_block_count"] == 7
        and expected_metrics["distinct_visible_table_caption_count"] == 40
        and expected_metrics["distinct_visible_figure_caption_count"] == 8,
        "document_nodes.source_invariants",
        "Document-node source invariants differ from the audited official HTML",
        counters=expected_counters,
        metrics=expected_metrics,
    )
    digest_payload = {
        "documents": documents,
        "counters": document_nodes.get("counters"),
        "metrics": document_nodes.get("metrics"),
    }
    audit.require(
        document_nodes.get("corpus_sha256")
        == sha256_bytes(canonical_json_bytes(digest_payload)),
        "document_nodes.corpus_hash",
        "Document-node corpus digest mismatch",
    )
    physical_report: dict[str, Any] = {}
    try:
        physical_report = reconcile_html_physical_artifact(
            build_html_physical_census(), document_nodes
        ).as_dict()
    except PhysicalAuditError as error:
        audit.fail(
            "document_nodes.physical_occurrences",
            str(error),
        )
    audit.metrics = {
        **expected_counters,
        **expected_metrics,
        "provenance_manifest_count": provenance_count,
        "multipart_provenance_manifest_count": multipart_provenance_count,
        **dict(field_source_metrics),
        "physical_audit": physical_report,
    }
    return {
        "passed": not audit.errors,
        "error_count": len(audit.errors),
        "errors": audit.errors,
        "metrics": audit.metrics,
    }


def validate_reference_occurrence_corpus(
    references: dict[str, Any],
    reference_schema: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    audit = Audit()
    for error in schema_errors(references, reference_schema):
        audit.fail("references.schema", error)

    occurrence_ids = [
        occurrence.get("occurrence_id")
        for occurrence in references.get("occurrences", [])
    ]
    audit.require(
        len(occurrence_ids) == len(set(occurrence_ids)),
        "references.occurrence_ids",
        "Reference occurrence ids are not globally unique",
    )

    source_rule_ids = {
        record.get("source_rule_id") for record in source.get("records", [])
    }
    referenced_source_rule_ids = {
        occurrence.get("source_rule_id")
        for occurrence in references.get("occurrences", [])
    }
    audit.require(
        referenced_source_rule_ids <= source_rule_ids,
        "references.source_rules",
        "Reference occurrences cite source fragments absent from the source corpus",
        unexpected=sorted(referenced_source_rule_ids - source_rule_ids)[:20],
    )

    source_artifacts = references.get("source_artifacts", [])
    audit.require(
        references.get("source_artifact_manifest_sha256")
        == sha256_bytes(canonical_json_bytes(source_artifacts)),
        "references.artifact_manifest_hash",
        "Reference source-artifact manifest digest mismatch",
    )
    digest_payload = {
        "context_characters": references.get("context_characters"),
        "source_document_ids": references.get("source_document_ids"),
        "source_artifact_manifest_sha256": references.get(
            "source_artifact_manifest_sha256"
        ),
        "source_artifacts": source_artifacts,
        "counters": references.get("counters"),
        "occurrences": references.get("occurrences"),
    }
    audit.require(
        references.get("corpus_sha256")
        == sha256_bytes(canonical_json_bytes(digest_payload)),
        "references.corpus_hash",
        "Reference occurrence corpus digest mismatch",
    )

    expected_counters = {
        "source_artifact_count": 11,
        "source_document_count": 11,
        "indexed_active_rule_fragment_count": 2554,
        "source_active_rule_fragment_count": 2554,
        "reference_occurrence_count": 4023,
        "reference_kind_counts": {"href": 4008, "text": 15},
        "target_resolution_counts": {
            "active_rule": 3896,
            "document": 124,
            "unresolved": 3,
        },
        "distinct_source_rule_count": 1363,
        "distinct_target_rule_count": 1532,
    }
    audit.require(
        references.get("counters") == expected_counters,
        "references.source_invariants",
        "Reference occurrence counts differ from the audited official HTML",
        counters=references.get("counters"),
    )

    try:
        regenerated = extract_reference_occurrences()
    except (OSError, ValueError) as error:
        audit.fail(
            "references.source_replay",
            "Reference occurrences could not be regenerated from official HTML",
            error=str(error),
        )
    else:
        audit.require(
            references == regenerated,
            "references.source_replay",
            "Reference occurrence artifact differs from deterministic source replay",
        )

    audit.metrics = {
        **expected_counters,
        "source_artifact_manifest_sha256": references.get(
            "source_artifact_manifest_sha256"
        ),
        "corpus_sha256": references.get("corpus_sha256"),
    }
    return {
        "passed": not audit.errors,
        "error_count": len(audit.errors),
        "errors": audit.errors,
        "metrics": audit.metrics,
    }


def validate_reference_resolution_corpus(
    resolutions: dict[str, Any],
    resolution_schema: dict[str, Any],
    references: dict[str, Any],
    source: dict[str, Any],
    corrections: dict[str, Any],
    *,
    references_sha256: str,
    source_sha256: str,
    corrections_sha256: str,
) -> dict[str, Any]:
    audit = Audit()
    for error in schema_errors(resolutions, resolution_schema):
        audit.fail("reference_resolutions.schema", error)
    try:
        regenerated = build_reference_resolutions(
            references,
            source,
            corrections,
            references_sha256=references_sha256,
            source_sha256=source_sha256,
            corrections_sha256=corrections_sha256,
        )
    except (KeyError, TypeError, ReferenceResolutionError) as error:
        audit.fail(
            "reference_resolutions.source_replay",
            "Explicit reference resolutions could not be reconstructed",
            error=str(error),
        )
    else:
        audit.require(
            resolutions == regenerated,
            "reference_resolutions.source_replay",
            "Reference resolution overlays differ from exact audited reconstruction",
        )
    expected_counters = {
        "raw_unresolved_occurrence_count": 3,
        "resolution_record_count": 3,
        "resolution_kind_counts": {
            "source_alias": 2,
            "historical_deleted_rule": 1,
        },
        "remaining_unresolved_occurrence_count": 0,
    }
    audit.require(
        resolutions.get("counters") == expected_counters,
        "reference_resolutions.source_invariants",
        "Reference resolution counts differ from the audited source anomalies",
    )
    audit.metrics = expected_counters
    return {
        "passed": not audit.errors,
        "error_count": len(audit.errors),
        "errors": audit.errors,
        "metrics": audit.metrics,
    }


POINTER_MISSING = object()


def resolve_json_pointer(value: Any, pointer: str) -> Any:
    current = value
    try:
        for part in pointer.lstrip("/").split("/"):
            current = current[int(part)] if isinstance(current, list) else current[part]
    except (KeyError, IndexError, TypeError, ValueError):
        return POINTER_MISSING
    return current


def document_nodes_with_paths(
    nodes: list[dict[str, Any]], prefix: str = "/nodes"
) -> Iterable[tuple[str, dict[str, Any]]]:
    for index, node in enumerate(nodes):
        path = f"{prefix}/{index}"
        yield path, node
        yield from document_nodes_with_paths(node.get("children", []), f"{path}/children")


def document_field_sources_with_paths(
    value: Any, path: str = ""
) -> Iterable[tuple[str, str, dict[str, Any]]]:
    if isinstance(value, dict):
        for field_name, field_source in sorted(value.get("field_sources", {}).items()):
            yield f"{path}/{field_name}", field_name, field_source
        for key, child in value.items():
            if key != "field_sources":
                yield from document_field_sources_with_paths(child, f"{path}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from document_field_sources_with_paths(child, f"{path}/{index}")


def primary_document_field_ids(value: Any) -> list[str]:
    return [
        field_source["field_source_id"]
        for _, _, field_source in document_field_sources_with_paths(value)
        if field_source["ownership"]["kind"] == "primary"
    ]


def correction_targets(correction: dict[str, Any]) -> set[str]:
    targets = {
        selector["rule_id"]
        for selector in correction["target"]["selectors"]
        if selector["kind"] == "rule" and selector.get("relation") == "target"
    }
    targets.update(
        reference["target"]
        for reference in correction.get("references", [])
        if reference.get("target_type") == "rule"
        and reference.get("relation") in {"target", "conflicts_with", "renamed_from"}
    )
    return targets


def normalized_clause_payload(unit_kind: str, component: Any) -> Any:
    image_keys = (
        "source_src",
        "url",
        "alt",
        "title",
        "width",
        "height",
        "link_href",
        "link_url",
    )
    if not isinstance(component, dict):
        return POINTER_MISSING
    if unit_kind == "image_asset":
        return {key: component.get(key) for key in image_keys}
    if unit_kind == "correction_event":
        icon = component.get("icon")
        if not isinstance(icon, dict):
            return POINTER_MISSING
        return {
            "event_kind": component.get("event_kind"),
            "target_url": component.get("target_url"),
            "description": component.get("description"),
            "icon": {key: icon.get(key) for key in image_keys},
        }
    if unit_kind == "empty_table_cell":
        if "cell_kind" in component:
            return {
                "row": None,
                "column": None,
                "cell_kind": component.get("cell_kind"),
                "rowspan": component.get("rowspan"),
                "colspan": component.get("colspan"),
            }
        return {"empty_table": True}
    if unit_kind == "empty_table":
        return {"empty_table": True}
    return POINTER_MISSING


def validate_clause_inventory(
    inventory: dict[str, Any],
    inventory_schema: dict[str, Any],
    source: dict[str, Any],
    document_nodes: dict[str, Any],
    corrections: dict[str, Any],
    *,
    source_sha256: str,
    document_nodes_sha256: str,
    corrections_sha256: str,
) -> dict[str, Any]:
    audit = Audit()
    for error in schema_errors(inventory, inventory_schema):
        audit.fail("clauses.schema", error)
    audit.require(
        inventory.get("source_corpus_sha256") == source_sha256
        and inventory.get("document_nodes_sha256") == document_nodes_sha256
        and inventory.get("correction_overlays_sha256") == corrections_sha256,
        "clauses.source_hashes",
        "Clause inventory does not bind to the validated source artifacts",
    )

    fragments: dict[str, tuple[str, dict[str, Any]]] = {}
    for document in document_nodes.get("documents", []):
        for fragment in document.get("fragments", []):
            fragments[fragment.get("rule_id")] = (document.get("document_id"), fragment)
    source_records = {
        record["source_rule_id"]: record for record in source.get("records", [])
    }
    corrections_by_rule: dict[str, list[str]] = defaultdict(list)
    for correction in corrections.get("records", []):
        for rule_id in correction_targets(correction):
            corrections_by_rule[rule_id].append(correction["overlay_id"])

    records = inventory.get("records", [])
    observed_rule_ids = [record.get("source_rule_id") for record in records]
    audit.require(
        observed_rule_ids == list(source_records),
        "clauses.record_coverage",
        "Clause inventory does not preserve exact source-record order and coverage",
    )
    all_units: list[dict[str, Any]] = []
    all_unit_ids: list[str] = []
    observed_document_node_count = 0
    for record in records:
        rule_id = record.get("source_rule_id")
        source_record = source_records.get(rule_id)
        document_id, fragment = fragments.get(rule_id, (None, {}))
        audit.require(
            bool(
                source_record
                and record.get("record_id") == source_record["record_id"]
                and record.get("chapter") == source_record["chapter"]
                and record.get("document_id") == document_id
                and record.get("fragment_ordinal") == fragment.get("ordinal")
                and record.get("source_reference_rule_ids")
                == source_record["html"]["references"]
                and record.get("correction_overlay_ids")
                == sorted(set(corrections_by_rule.get(rule_id, [])))
            ),
            "clauses.record_source",
            "Clause record metadata differs from its source artifacts",
            rule_id=rule_id,
        )
        expected_record_hash = sha256_bytes(
            canonical_json_bytes(
                {key: value for key, value in record.items() if key != "record_sha256"}
            )
        )
        audit.require(
            record.get("record_sha256") == expected_record_hash,
            "clauses.record_hash",
            "Clause record digest mismatch",
            rule_id=rule_id,
        )

        nodes_by_path = dict(document_nodes_with_paths(fragment.get("nodes", [])))
        coverage = record.get("node_coverage", [])
        observed_document_node_count += len(coverage)
        audit.require(
            [item.get("component_path") for item in coverage] == list(nodes_by_path),
            "clauses.node_coverage_order",
            "Node coverage does not preserve complete document-node order",
            rule_id=rule_id,
        )
        units = record.get("source_units", [])
        unit_ids = [unit.get("unit_id") for unit in units]
        expected_unit_ids = [
            f"{rule_id}:clause:{ordinal:04d}"
            for ordinal in range(1, len(units) + 1)
        ]
        audit.require(
            unit_ids == expected_unit_ids
            and [unit.get("ordinal") for unit in units]
            == list(range(1, len(units) + 1)),
            "clauses.unit_identity",
            "Clause unit ids or ordinals are not contiguous",
            rule_id=rule_id,
        )
        units_by_id = {unit.get("unit_id"): unit for unit in units}
        covered_ids = [unit_id for item in coverage for unit_id in item.get("unit_ids", [])]
        audit.require(
            len(covered_ids) == len(set(covered_ids))
            and set(covered_ids) == set(units_by_id),
            "clauses.node_unit_partition",
            "Node coverage does not partition every source unit exactly once",
            rule_id=rule_id,
        )
        for item in coverage:
            node = nodes_by_path.get(item.get("component_path"))
            audit.require(
                bool(
                    node
                    and item.get("node_id") == node.get("node_id")
                    and item.get("node_kind") == node.get("kind")
                    and item.get("unit_ids")
                    and all(
                        units_by_id.get(unit_id, {}).get("source_node_id")
                        == node.get("node_id")
                        for unit_id in item.get("unit_ids", [])
                    )
                ),
                "clauses.node_owner",
                "A document node is missing or owns the wrong source units",
                rule_id=rule_id,
                component_path=item.get("component_path"),
            )

        ranges_by_component: dict[str, list[tuple[int, int]]] = defaultdict(list)
        unit_ids_by_field: dict[str, list[str]] = defaultdict(list)
        for unit in units:
            component_path = unit.get("component_path", "")
            provenance_path = unit.get("provenance_path", "")
            component = resolve_json_pointer(fragment, component_path)
            provenance = resolve_json_pointer(fragment, provenance_path)
            audit.require(
                isinstance(provenance, dict)
                and provenance.get("manifest_sha256")
                == unit.get("provenance_manifest_sha256"),
                "clauses.provenance_pointer",
                "Clause provenance pointer does not resolve to its declared manifest",
                unit_id=unit.get("unit_id"),
            )
            unit_kind = unit.get("unit_kind")
            audit.require(
                unit.get("ownership") == "primary",
                "clauses.unit_ownership",
                "A source unit is not primary-owned",
                unit_id=unit.get("unit_id"),
            )
            for field_source_id in unit.get("field_source_ids", []):
                unit_ids_by_field[field_source_id].append(unit.get("unit_id"))
            if unit_kind in TEXT_CLAUSE_UNIT_KINDS:
                start = unit.get("text_start")
                end = unit.get("text_end")
                valid_text_range = (
                    isinstance(component, str)
                    and isinstance(start, int)
                    and isinstance(end, int)
                    and 0 <= start < end <= len(component)
                )
                audit.require(
                    valid_text_range,
                    "clauses.text_range",
                    "Text clause range does not resolve within its component",
                    unit_id=unit.get("unit_id"),
                )
                if valid_text_range:
                    text = component[start:end]
                    field_source = resolve_json_pointer(
                        fragment, unit.get("field_source_path", "")
                    )
                    audit.require(
                        unit.get("text") == text
                        and unit.get("text_sha256") == sha256_text(text)
                        and unit.get("component_text_sha256") == sha256_text(component)
                        and unit.get("payload") is None
                        and unit.get("payload_sha256") is None,
                        "clauses.text_hash",
                        "Text clause content or digest does not reproduce from its component",
                        unit_id=unit.get("unit_id"),
                    )
                    audit.require(
                        isinstance(field_source, dict)
                        and field_source.get("ownership", {}).get("kind") == "primary"
                        and unit.get("field_source_ids")
                        == [field_source.get("field_source_id")]
                        and unit.get("source_occurrence_id") is None,
                        "clauses.text_field_source",
                        "Text clause is not bound to exactly one primary field source",
                        unit_id=unit.get("unit_id"),
                    )
                    ranges_by_component[component_path].append((start, end))
            else:
                expected_payload = normalized_clause_payload(str(unit_kind), component)
                if unit_kind == "empty_table_cell" and isinstance(component, dict) and "cell_kind" in component:
                    cell_match = re.search(r"/rows/(\d+)/cells/(\d+)$", component_path)
                    if cell_match:
                        expected_payload = {
                            **expected_payload,
                            "row": int(cell_match.group(1)) + 1,
                            "column": int(cell_match.group(2)) + 1,
                        }
                    else:
                        orphan_match = re.search(
                            r"/orphan_cells/(\d+)$", component_path
                        )
                        expected_payload = {
                            **expected_payload,
                            "row": None,
                            "column": (
                                int(orphan_match.group(1)) + 1
                                if orphan_match
                                else 1
                            ),
                        }
                expected_field_ids = (
                    primary_document_field_ids(component)
                    if unit_kind in {"image_asset", "correction_event"}
                    else []
                )
                audit.require(
                    expected_payload is not POINTER_MISSING
                    and unit.get("payload") == expected_payload
                    and unit.get("payload_sha256")
                    == sha256_bytes(canonical_json_bytes(expected_payload))
                    and unit.get("text_start") is None
                    and unit.get("text_end") is None
                    and unit.get("text") is None
                    and unit.get("text_sha256") is None
                    and unit.get("component_text_sha256") is None,
                    "clauses.payload",
                    "Nontext clause payload does not reproduce from its source component",
                    unit_id=unit.get("unit_id"),
                )
                audit.require(
                    unit.get("field_source_path") is None
                    and unit.get("field_source_ids") == expected_field_ids
                    and unit.get("source_occurrence_id")
                    == (
                        component.get("occurrence_id")
                        if isinstance(component, dict)
                        else None
                    ),
                    "clauses.payload_source",
                    "Payload clause ownership or field binding differs from its component",
                    unit_id=unit.get("unit_id"),
                )

        for component_path, ranges in ranges_by_component.items():
            component = resolve_json_pointer(fragment, component_path)
            if not isinstance(component, str):
                continue
            character_counts = [0] * len(component)
            for start, end in ranges:
                for index in range(start, end):
                    character_counts[index] += 1
            audit.require(
                all(
                    count == 1 if not char.isspace() else count in {0, 1}
                    for char, count in zip(component, character_counts)
                ),
                "clauses.text_partition",
                "Text clauses omit or overlap non-whitespace source characters",
                rule_id=rule_id,
                component_path=component_path,
            )

        expected_fields = list(
            document_field_sources_with_paths(fragment.get("nodes", []), "/nodes")
        )
        field_coverage = record.get("field_source_coverage", [])
        audit.require(
            len(field_coverage) == len(expected_fields),
            "clauses.field_coverage_count",
            "Field-source coverage count differs from the document artifact",
            rule_id=rule_id,
        )
        for item, expected_field in zip(field_coverage, expected_fields):
            component_path, field_name, field_source = expected_field
            ownership = field_source.get("ownership", {})
            expected_unit_ids = unit_ids_by_field.get(
                field_source.get("field_source_id"), []
            )
            valid = bool(
                item.get("field_source_id") == field_source.get("field_source_id")
                and item.get("component_path") == component_path
                and item.get("field_name") == field_name
                and item.get("ownership") == ownership.get("kind")
                and item.get("owner_ref") == ownership.get("owner_ref")
                and (
                    item.get("unit_ids") == expected_unit_ids
                    if ownership.get("kind") == "primary"
                    else item.get("unit_ids") == [] and not expected_unit_ids
                )
                and (
                    bool(expected_unit_ids)
                    if ownership.get("kind") == "primary"
                    else True
                )
            )
            audit.require(
                valid,
                "clauses.field_coverage",
                "A field source is omitted, duplicated, or assigned contrary to ownership",
                rule_id=rule_id,
                component_path=component_path,
            )
        all_units.extend(units)
        all_unit_ids.extend(unit_ids)

    audit.require(
        len(all_unit_ids) == len(set(all_unit_ids)),
        "clauses.global_unit_ids",
        "Clause unit ids are not globally unique",
    )
    unit_kind_counts = Counter(unit.get("unit_kind") for unit in all_units)
    expected_counters = {
        "record_count": len(records),
        "document_node_count": observed_document_node_count,
        "source_unit_count": len(all_units),
        "text_unit_count": sum(
            unit.get("unit_kind") in TEXT_CLAUSE_UNIT_KINDS for unit in all_units
        ),
        "asset_unit_count": unit_kind_counts["image_asset"],
        "event_unit_count": unit_kind_counts["correction_event"],
        "empty_structural_unit_count": (
            unit_kind_counts["empty_table_cell"] + unit_kind_counts["empty_table"]
        ),
        "field_source_count": sum(
            len(record.get("field_source_coverage", [])) for record in records
        ),
        "primary_field_source_count": sum(
            item.get("ownership") == "primary"
            for record in records
            for item in record.get("field_source_coverage", [])
        ),
        "unit_kind_counts": {kind: unit_kind_counts[kind] for kind in CLAUSE_UNIT_KINDS},
    }
    audit.require(
        inventory.get("counters") == expected_counters,
        "clauses.counters",
        "Clause inventory counters do not reconstruct",
    )
    audit.require(
        expected_counters["record_count"] == 2554
        and expected_counters["document_node_count"] == 14453
        and expected_counters["source_unit_count"] == 32408
        and expected_counters["text_unit_count"] == 24690
        and expected_counters["asset_unit_count"] == 5181
        and expected_counters["event_unit_count"] == 190
        and expected_counters["empty_structural_unit_count"] == 2347
        and expected_counters["field_source_count"] == 38256
        and expected_counters["primary_field_source_count"] == 25413
        and expected_counters["unit_kind_counts"] == EXPECTED_CLAUSE_UNIT_KIND_COUNTS,
        "clauses.source_invariants",
        "Clause inventory source invariants differ from the pinned normalized corpus",
        counters=expected_counters,
    )
    digest_payload = {
        key: value for key, value in inventory.items() if key != "corpus_sha256"
    }
    audit.require(
        inventory.get("corpus_sha256")
        == sha256_bytes(canonical_json_bytes(digest_payload)),
        "clauses.corpus_hash",
        "Clause inventory corpus digest mismatch",
    )
    audit.metrics = expected_counters
    return {
        "passed": not audit.errors,
        "error_count": len(audit.errors),
        "errors": audit.errors,
        "metrics": audit.metrics,
    }


def validate_correction_overlays(
    corrections: dict[str, Any],
    correction_schema: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    audit = Audit()
    for error in schema_errors(corrections, correction_schema):
        audit.fail("corrections.schema", error)

    source_document = corrections.get("source_document", {})
    source_path = ROOT / str(source_document.get("source_path", ""))
    audit.require(
        source_path.is_file(),
        "corrections.source_file",
        "Cached correction source file is absent",
        path=str(source_path),
    )
    raw = source_path.read_bytes() if source_path.is_file() else b""
    audit.require(
        source_document.get("source_sha256") == sha256_bytes(raw),
        "corrections.source_hash",
        "Correction source digest mismatch",
    )
    audit.require(
        source_document.get("source_byte_count") == len(raw),
        "corrections.source_bytes",
        "Correction source byte count mismatch",
    )
    correction_manifest = next(
        (
            item
            for item in source["source_document"]["auxiliary_html_sources"]
            if item["source_kind"] == "corrections"
        ),
        None,
    )
    audit.require(
        bool(
            correction_manifest
            and correction_manifest["sha256"] == sha256_bytes(raw)
            and correction_manifest["byte_count"] == len(raw)
            and correction_manifest["url"] == source_document.get("source_url")
        ),
        "corrections.source_manifest",
        "Correction overlay source differs from the source-corpus manifest",
    )

    records = corrections.get("records", [])
    audit.require(len(records) == 90, "corrections.record_count", "Expected 90 correction overlays")
    audit.require(
        corrections.get("record_count") == len(records),
        "corrections.counter.records",
        "Correction record counter mismatch",
    )
    overlay_ids = [record.get("overlay_id") for record in records]
    audit.require(
        len(set(overlay_ids)) == len(overlay_ids),
        "corrections.overlay_ids",
        "Duplicate correction overlay ids",
    )

    status_counts: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()
    operation_counts: Counter[str] = Counter()
    selector_counts: Counter[str] = Counter()
    reference_ids: list[str] = []
    source_rule_ids = {record["source_rule_id"] for record in source["records"]}
    intentionally_historical_rule_ids = {"P-65.7.8", "P-68.1.6.1.2"}
    for record in records:
        status_counts[record["status"]] += 1
        provenance = record["provenance"]
        start = int(provenance["source_byte_start"])
        end = int(provenance["source_byte_end"])
        audit.require(
            0 <= start < end <= len(raw),
            "corrections.record_range",
            "Correction record byte range is outside the source",
            overlay_id=record["overlay_id"],
        )
        if 0 <= start < end <= len(raw):
            fragment = raw[start:end]
            audit.require(
                fragment.decode("utf-8") == record["source_html"],
                "corrections.record_html",
                "Correction record HTML does not match its exact source range",
                overlay_id=record["overlay_id"],
            )
            audit.require(
                provenance["fragment_sha256"] == sha256_bytes(fragment),
                "corrections.record_hash",
                "Correction record fragment hash mismatch",
                overlay_id=record["overlay_id"],
            )
        for event in record["events"]:
            event_counts[event["event_type"]] += 1
        for operation in record["operations"]:
            operation_counts[operation["kind"]] += 1
            op_provenance = operation["provenance"]
            op_start = int(op_provenance["source_byte_start"])
            op_end = int(op_provenance["source_byte_end"])
            audit.require(
                start <= op_start < op_end <= end,
                "corrections.operation_range",
                "Correction operation range falls outside its record",
                operation_id=operation["operation_id"],
            )
            if 0 <= op_start < op_end <= len(raw):
                op_fragment = raw[op_start:op_end]
                audit.require(
                    op_fragment.decode("utf-8") == operation["source_html"],
                    "corrections.operation_html",
                    "Correction operation HTML does not match its exact source range",
                    operation_id=operation["operation_id"],
                )
                audit.require(
                    op_provenance["fragment_sha256"] == sha256_bytes(op_fragment),
                    "corrections.operation_hash",
                    "Correction operation fragment hash mismatch",
                    operation_id=operation["operation_id"],
                )
        for selector in record["target"]["selectors"]:
            selector_counts[selector["kind"]] += 1
        for reference in record["references"]:
            reference_ids.append(reference["reference_id"])
            if reference["target_type"] == "rule":
                audit.require(
                    reference["target"] in source_rule_ids
                    or reference["target"] in intentionally_historical_rule_ids,
                    "corrections.rule_target",
                    "Correction reference targets neither an active nor a declared historical rule",
                    reference_id=reference["reference_id"],
                    target=reference["target"],
                )
    audit.require(
        len(set(reference_ids)) == len(reference_ids),
        "corrections.reference_ids",
        "Duplicate correction reference ids",
    )
    expected_counters = {
        "status": dict(sorted(status_counts.items())),
        "event_type": dict(sorted(event_counts.items())),
        "operation_kind": dict(sorted(operation_counts.items())),
        "selector_kind": dict(sorted(selector_counts.items())),
    }
    audit.require(
        corrections.get("counters") == expected_counters,
        "corrections.counters",
        "Correction counters do not reconstruct from records",
        computed=expected_counters,
    )
    audit.require(
        expected_counters["status"] == {"applied": 10, "deleted": 1, "replaced": 79},
        "corrections.status_invariant",
        "Correction status counts differ from the pinned source",
    )
    audit.metrics = {
        "correction_record_count": len(records),
        "correction_operation_count": sum(operation_counts.values()),
        "correction_reference_count": len(reference_ids),
        "correction_asset_count": sum(
            len(operation.get("assets", []))
            for record in records
            for operation in record["operations"]
        ),
        "correction_status_counts": dict(sorted(status_counts.items())),
    }
    return {
        "passed": not audit.errors,
        "error_count": len(audit.errors),
        "errors": audit.errors,
        "metrics": audit.metrics,
    }


def validate_semantic_corpus(
    semantic: dict[str, Any],
    semantic_schema: dict[str, Any],
    source: dict[str, Any],
    clause_inventory: dict[str, Any],
    expected_source_snapshot: dict[str, str],
) -> dict[str, Any]:
    audit = Audit()
    try:
        validate_rule_corpus(semantic, semantic_schema, SEMANTIC_SCHEMA)
    except SemanticAssemblyError as error:
        audit.fail(
            "semantic.invariants",
            "Final semantic corpus failed independent validation",
            error=str(error),
        )
    audit.require(
        semantic.get("source_snapshot") == expected_source_snapshot,
        "semantic.source_snapshot",
        "Semantic corpus source snapshot differs from validated source artifacts",
        expected=expected_source_snapshot,
        actual=semantic.get("source_snapshot"),
    )

    source_ids = [record["source_rule_id"] for record in source["records"]]
    records = semantic.get("records", [])
    record_ids = [record.get("record_id") for record in records]
    semantic_source_ids = [record.get("source_rule_id") for record in records]
    audit.require(
        len(set(record_ids)) == len(record_ids),
        "semantic.record_ids",
        "Duplicate semantic record ids",
    )
    audit.require(
        semantic_source_ids == source_ids,
        "semantic.coverage",
        "Semantic records do not preserve exact source-rule coverage and order",
    )

    expected_clauses_by_rule = {
        record["source_rule_id"]: [
            unit["unit_id"] for unit in record["source_units"]
        ]
        for record in clause_inventory["records"]
    }
    for record in records:
        source_rule_id = record.get("source_rule_id")
        audit.require(
            record.get("clause_ids") == expected_clauses_by_rule.get(source_rule_id),
            "semantic.record_clauses",
            "Semantic record clause membership differs from clause inventory",
            source_rule_id=source_rule_id,
        )
    expected_clause_ids = [
        unit["unit_id"]
        for record in clause_inventory["records"]
        for unit in record["source_units"]
    ]
    actual_clause_ids = [
        item.get("clause_id") for item in semantic.get("clause_dispositions", [])
    ]
    audit.require(
        actual_clause_ids == expected_clause_ids,
        "semantic.clause_coverage",
        "Clause dispositions do not cover the normalized source inventory exactly once in order",
    )
    audit.metrics = dict(semantic.get("metrics", {}))
    return {"passed": not audit.errors, "error_count": len(audit.errors), "errors": audit.errors, "metrics": audit.metrics}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the Blue Book source and semantic conversion")
    parser.add_argument("--stage", choices=("source", "semantic", "all"), default="all")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = load_json(SOURCE_CORPUS)
    pages = load_json(SOURCE_PAGES)
    source_schema = load_json(SOURCE_SCHEMA)
    source_result = validate_source_corpus(
        source,
        pages,
        source_schema,
        load_json(SOURCE_PAGES_SCHEMA),
    )

    source_bytes = SOURCE_CORPUS.read_bytes()
    canonical_source = canonical_json_bytes(source)
    if source_bytes != canonical_source:
        source_result["passed"] = False
        source_result["error_count"] += 1
        source_result["errors"].append(
            {"code": "source.canonical_json", "message": "Source corpus is not canonical JSON", "context": {}}
        )
    if SOURCE_PAGES.read_bytes() != canonical_json_bytes(pages):
        source_result["passed"] = False
        source_result["error_count"] += 1
        source_result["errors"].append(
            {"code": "pages.canonical_json", "message": "Source pages are not canonical JSON", "context": {}}
        )

    document_nodes_present = DOCUMENT_NODES.exists()
    document_nodes: dict[str, Any] | None = None
    document_nodes_sha256: str | None = None
    document_node_result: dict[str, Any] = {
        "passed": False,
        "error_count": 1,
        "errors": [
            {
                "code": "document_nodes.missing",
                "message": "Normalized source document nodes have not been generated",
                "context": {"path": str(DOCUMENT_NODES.relative_to(ROOT))},
            }
        ],
        "metrics": {},
    }
    if document_nodes_present:
        try:
            document_nodes = load_document_nodes(DOCUMENT_NODES)
            document_nodes_sha256 = hash_document_nodes(DOCUMENT_NODES)
        except (DocumentNodeStoreError, OSError) as error:
            document_node_result["errors"] = [
                {
                    "code": "document_nodes.store",
                    "message": "Document-node store failed integrity validation",
                    "context": {"error": str(error)},
                }
            ]
        else:
            document_node_result = validate_document_node_corpus(
                document_nodes,
                load_json(DOCUMENT_NODE_SCHEMA),
                source,
            )

    reference_occurrences_present = REFERENCE_OCCURRENCES.exists()
    references: dict[str, Any] | None = None
    reference_result: dict[str, Any] = {
        "passed": False,
        "error_count": 1,
        "errors": [
            {
                "code": "references.missing",
                "message": "Occurrence-level cross-references have not been generated",
                "context": {"path": str(REFERENCE_OCCURRENCES.relative_to(ROOT))},
            }
        ],
        "metrics": {},
    }
    if reference_occurrences_present:
        references = load_json(REFERENCE_OCCURRENCES)
        reference_result = validate_reference_occurrence_corpus(
            references,
            load_json(REFERENCE_OCCURRENCES_SCHEMA),
            source,
        )
        if REFERENCE_OCCURRENCES.read_bytes() != canonical_json_bytes(references):
            reference_result["passed"] = False
            reference_result["error_count"] += 1
            reference_result["errors"].append(
                {
                    "code": "references.canonical_json",
                    "message": "Reference occurrence corpus is not canonical JSON",
                    "context": {},
                }
            )

    correction_present = CORRECTION_OVERLAYS.exists()
    corrections: dict[str, Any] | None = None
    correction_result: dict[str, Any] = {
        "passed": False,
        "error_count": 1,
        "errors": [
            {
                "code": "corrections.missing",
                "message": "Correction overlays have not been generated",
                "context": {"path": str(CORRECTION_OVERLAYS.relative_to(ROOT))},
            }
        ],
        "metrics": {},
    }
    if correction_present:
        corrections = load_json(CORRECTION_OVERLAYS)
        correction_result = validate_correction_overlays(
            corrections,
            load_json(CORRECTION_SCHEMA),
            source,
        )
        if CORRECTION_OVERLAYS.read_bytes() != canonical_json_bytes(corrections):
            correction_result["passed"] = False
            correction_result["error_count"] += 1
            correction_result["errors"].append(
                {
                    "code": "corrections.canonical_json",
                    "message": "Correction overlays are not canonical JSON",
                    "context": {},
                }
            )

    reference_resolutions_present = REFERENCE_RESOLUTIONS.exists()
    reference_resolutions: dict[str, Any] | None = None
    reference_resolution_result: dict[str, Any] = {
        "passed": False,
        "error_count": 1,
        "errors": [
            {
                "code": "reference_resolutions.missing",
                "message": "Explicit raw-reference resolutions have not been generated",
                "context": {"path": str(REFERENCE_RESOLUTIONS.relative_to(ROOT))},
            }
        ],
        "metrics": {},
    }
    if (
        reference_resolutions_present
        and references is not None
        and corrections is not None
    ):
        reference_resolutions = load_json(REFERENCE_RESOLUTIONS)
        reference_resolution_result = validate_reference_resolution_corpus(
            reference_resolutions,
            load_json(REFERENCE_RESOLUTIONS_SCHEMA),
            references,
            source,
            corrections,
            references_sha256=sha256_bytes(REFERENCE_OCCURRENCES.read_bytes()),
            source_sha256=sha256_bytes(source_bytes),
            corrections_sha256=sha256_bytes(CORRECTION_OVERLAYS.read_bytes()),
        )
        if REFERENCE_RESOLUTIONS.read_bytes() != canonical_json_bytes(
            reference_resolutions
        ):
            reference_resolution_result["passed"] = False
            reference_resolution_result["error_count"] += 1
            reference_resolution_result["errors"].append(
                {
                    "code": "reference_resolutions.canonical_json",
                    "message": "Reference resolution corpus is not canonical JSON",
                    "context": {},
                }
            )
    elif reference_resolutions_present:
        reference_resolution_result["errors"] = [
            {
                "code": "reference_resolutions.dependencies",
                "message": "Reference resolution dependencies are absent",
                "context": {
                    "references_present": references is not None,
                    "corrections_present": corrections is not None,
                },
            }
        ]

    reference_graph_present = REFERENCE_DEPENDENCY_GRAPH.exists()
    reference_graph_result: dict[str, Any] = {
        "passed": False,
        "error_count": 1,
        "errors": [
            {
                "code": "reference_graph.missing",
                "message": "Resolved occurrence dependency graph has not been generated",
                "context": {
                    "path": str(REFERENCE_DEPENDENCY_GRAPH.relative_to(ROOT))
                },
            }
        ],
        "metrics": {},
    }
    if (
        reference_graph_present
        and references is not None
        and reference_resolutions is not None
    ):
        graph_bytes = REFERENCE_DEPENDENCY_GRAPH.read_bytes()
        graph = load_json(REFERENCE_DEPENDENCY_GRAPH)
        references_sha256 = sha256_bytes(REFERENCE_OCCURRENCES.read_bytes())
        resolutions_sha256 = sha256_bytes(REFERENCE_RESOLUTIONS.read_bytes())
        graph_errors: list[dict[str, Any]] = []
        try:
            validate_reference_dependency_graph(
                graph,
                load_json(REFERENCE_DEPENDENCY_GRAPH_SCHEMA),
                input_corpus=references,
                input_artifact_sha256=references_sha256,
                resolution_corpus=reference_resolutions,
                resolution_artifact_sha256=resolutions_sha256,
            )
            rebuilt_graph = build_reference_dependency_graph(
                references,
                reference_resolutions,
                source_artifact_sha256=references_sha256,
                resolution_artifact_sha256=resolutions_sha256,
            )
        except ValueError as error:
            graph_errors.append(
                {
                    "code": "reference_graph.invariants",
                    "message": str(error),
                    "context": {},
                }
            )
        else:
            if graph != rebuilt_graph:
                graph_errors.append(
                    {
                        "code": "reference_graph.rebuild",
                        "message": "Reference graph differs from deterministic reconstruction",
                        "context": {},
                    }
                )
            if graph_bytes != canonical_json_bytes(graph):
                graph_errors.append(
                    {
                        "code": "reference_graph.canonical_json",
                        "message": "Reference graph is not canonical JSON",
                        "context": {},
                    }
                )
        reference_graph_result = {
            "passed": not graph_errors,
            "error_count": len(graph_errors),
            "errors": graph_errors,
            "metrics": graph.get("counters", {}),
        }
    elif reference_graph_present:
        reference_graph_result["errors"] = [
            {
                "code": "reference_graph.dependencies",
                "message": "Reference graph dependencies are absent",
                "context": {
                    "references_present": references is not None,
                    "reference_resolutions_present": reference_resolutions is not None,
                },
            }
        ]

    clause_inventory_present = CLAUSE_INVENTORY.exists()
    clause_inventory: dict[str, Any] | None = None
    clause_result: dict[str, Any] = {
        "passed": False,
        "error_count": 1,
        "errors": [
            {
                "code": "clauses.missing",
                "message": "Normalized source-clause inventory has not been generated",
                "context": {"path": str(CLAUSE_INVENTORY.relative_to(ROOT))},
            }
        ],
        "metrics": {},
    }
    if (
        clause_inventory_present
        and document_nodes is not None
        and document_nodes_sha256 is not None
        and corrections is not None
    ):
        clause_inventory = load_json(CLAUSE_INVENTORY)
        clause_result = validate_clause_inventory(
            clause_inventory,
            load_json(CLAUSE_INVENTORY_SCHEMA),
            source,
            document_nodes,
            corrections,
            source_sha256=sha256_bytes(source_bytes),
            document_nodes_sha256=document_nodes_sha256,
            corrections_sha256=sha256_bytes(CORRECTION_OVERLAYS.read_bytes()),
        )
        if CLAUSE_INVENTORY.read_bytes() != canonical_json_bytes(clause_inventory):
            clause_result["passed"] = False
            clause_result["error_count"] += 1
            clause_result["errors"].append(
                {
                    "code": "clauses.canonical_json",
                    "message": "Clause inventory is not canonical JSON",
                    "context": {},
                }
            )
    elif clause_inventory_present:
        clause_result["errors"] = [
            {
                "code": "clauses.dependencies",
                "message": "Clause inventory dependencies are absent",
                "context": {
                    "document_nodes_present": document_nodes is not None,
                    "corrections_present": corrections is not None,
                },
            }
        ]

    semantic_present = SEMANTIC_CORPUS.exists()
    semantic_result: dict[str, Any] = {
        "passed": False,
        "error_count": 1,
        "errors": [
            {
                "code": "semantic.missing",
                "message": "Semantic IR corpus has not been generated",
                "context": {"path": str(SEMANTIC_CORPUS.relative_to(ROOT))},
            }
        ],
        "metrics": {},
    }
    if (
        semantic_present
        and clause_inventory is not None
        and document_nodes_sha256 is not None
        and corrections is not None
        and references is not None
        and reference_resolutions is not None
    ):
        semantic = load_json(SEMANTIC_CORPUS)
        expected_source_snapshot = {
            "source_corpus_sha256": sha256_bytes(source_bytes),
            "source_pages_sha256": sha256_bytes(SOURCE_PAGES.read_bytes()),
            "document_nodes_sha256": document_nodes_sha256,
            "correction_overlays_sha256": sha256_bytes(
                CORRECTION_OVERLAYS.read_bytes()
            ),
            "clause_inventory_sha256": sha256_bytes(CLAUSE_INVENTORY.read_bytes()),
            "reference_occurrences_sha256": sha256_bytes(
                REFERENCE_OCCURRENCES.read_bytes()
            ),
            "reference_resolutions_sha256": sha256_bytes(
                REFERENCE_RESOLUTIONS.read_bytes()
            ),
            "effective_through": max(
                record["effective_date"] for record in corrections["records"]
            ),
        }
        semantic_result = validate_semantic_corpus(
            semantic,
            load_json(SEMANTIC_SCHEMA),
            source,
            clause_inventory,
            expected_source_snapshot,
        )
        if SEMANTIC_CORPUS.read_bytes() != canonical_json_bytes(semantic):
            semantic_result["passed"] = False
            semantic_result["error_count"] += 1
            semantic_result["errors"].append(
                {"code": "semantic.canonical_json", "message": "Semantic corpus is not canonical JSON", "context": {}}
            )
    elif semantic_present:
        semantic_result["errors"] = [
            {
                "code": "semantic.dependencies",
                "message": "Semantic corpus source dependencies are absent",
                "context": {
                    "clause_inventory_present": clause_inventory is not None,
                    "document_nodes_present": document_nodes_sha256 is not None,
                    "corrections_present": corrections is not None,
                    "references_present": references is not None,
                    "reference_resolutions_present": reference_resolutions is not None,
                },
            }
        ]

    source_stage_complete = (
        source_result["passed"]
        and document_node_result["passed"]
        and reference_result["passed"]
        and correction_result["passed"]
        and reference_resolution_result["passed"]
        and reference_graph_result["passed"]
        and clause_result["passed"]
    )
    conversion_complete = source_stage_complete and semantic_result["passed"]
    report = {
        "source_corpus_sha256": sha256_bytes(source_bytes),
        "source_pages_sha256": sha256_bytes(SOURCE_PAGES.read_bytes()),
        "document_nodes_present": document_nodes_present,
        "document_nodes_sha256": document_nodes_sha256,
        "reference_occurrences_present": reference_occurrences_present,
        "reference_occurrences_sha256": (
            sha256_bytes(REFERENCE_OCCURRENCES.read_bytes())
            if reference_occurrences_present
            else None
        ),
        "reference_resolutions_present": reference_resolutions_present,
        "reference_resolutions_sha256": (
            sha256_bytes(REFERENCE_RESOLUTIONS.read_bytes())
            if reference_resolutions_present
            else None
        ),
        "reference_dependency_graph_present": reference_graph_present,
        "reference_dependency_graph_sha256": (
            sha256_bytes(REFERENCE_DEPENDENCY_GRAPH.read_bytes())
            if reference_graph_present
            else None
        ),
        "clause_inventory_present": clause_inventory_present,
        "clause_inventory_sha256": (
            sha256_bytes(CLAUSE_INVENTORY.read_bytes())
            if clause_inventory_present
            else None
        ),
        "correction_overlays_present": correction_present,
        "correction_overlays_sha256": (
            sha256_bytes(CORRECTION_OVERLAYS.read_bytes()) if correction_present else None
        ),
        "semantic_corpus_present": semantic_present,
        "source": source_result,
        "document_nodes": document_node_result,
        "references": reference_result,
        "reference_resolutions": reference_resolution_result,
        "reference_dependency_graph": reference_graph_result,
        "corrections": correction_result,
        "clauses": clause_result,
        "semantic": semantic_result,
        "source_stage_complete": source_stage_complete,
        "conversion_complete": conversion_complete,
    }
    write_json(REPORT, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if args.stage == "source":
        return 0 if source_stage_complete else 1
    if args.stage == "semantic":
        return 0 if semantic_result["passed"] else 1
    return 0 if conversion_complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
