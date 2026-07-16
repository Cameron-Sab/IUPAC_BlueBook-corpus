from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

try:
    from scripts.document_node_store import (
        DEFAULT_STORE as DEFAULT_DOCUMENT_NODE_STORE,
        hash_document_nodes,
        load_document_nodes,
    )
except ModuleNotFoundError:  # Support `python scripts/build_clause_inventory.py`.
    from document_node_store import (  # type: ignore[no-redef]
        DEFAULT_STORE as DEFAULT_DOCUMENT_NODE_STORE,
        hash_document_nodes,
        load_document_nodes,
    )

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "bluebook_v3"
DEFAULT_SOURCE = BASE / "bluebook_v3_source_corpus.json"
DEFAULT_DOCUMENT_NODES = DEFAULT_DOCUMENT_NODE_STORE
DEFAULT_CORRECTIONS = BASE / "bluebook_v3_correction_overlays.json"
DEFAULT_SCHEMA = ROOT / "data" / "bluebook_clause_inventory.schema.json"
DEFAULT_OUTPUT = BASE / "bluebook_v3_clause_inventory.json"

UNIT_KINDS = (
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
TEXT_UNIT_KINDS = {
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
}
ABBREVIATIONS = {
    "ca.",
    "cf.",
    "ed.",
    "eds.",
    "fig.",
    "figs.",
    "i.e.",
    "e.g.",
    "no.",
    "nos.",
    "p.",
    "pp.",
    "ref.",
    "refs.",
    "st.",
    "vs.",
}
CLOSING_PUNCTUATION = "\"'\u2019\u201d)]}"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def preceding_token(text: str, period_index: int) -> str:
    start = period_index
    while start > 0 and (text[start - 1].isalpha() or text[start - 1] == "."):
        start -= 1
    return text[start : period_index + 1].lower()


def is_period_boundary(text: str, index: int, next_nonspace: int) -> bool:
    if index > 0 and index + 1 < len(text):
        if text[index - 1].isdigit() and text[index + 1].isdigit():
            return False
    token = preceding_token(text, index)
    if token in ABBREVIATIONS or token.endswith(("e.g.", "i.e.")):
        return False
    if len(token) == 2 and token[0].isalpha():
        return False
    if next_nonspace < len(text) and text[next_nonspace].islower():
        return False
    return True


def sentence_spans(text: str) -> list[tuple[int, int]]:
    boundaries: list[int] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char not in ".!?;":
            index += 1
            continue
        punctuation_end = index + 1
        while punctuation_end < len(text) and text[punctuation_end] in ".!?":
            punctuation_end += 1
        boundary_end = punctuation_end
        while boundary_end < len(text) and text[boundary_end] in CLOSING_PUNCTUATION:
            boundary_end += 1
        next_nonspace = boundary_end
        while next_nonspace < len(text) and text[next_nonspace].isspace():
            next_nonspace += 1
        if char == "." and not is_period_boundary(text, index, next_nonspace):
            index = punctuation_end
            continue
        if char != ";" and next_nonspace < len(text):
            following = text[next_nonspace]
            if following.islower() or following in ",;:":
                index = punctuation_end
                continue
        boundaries.append(boundary_end)
        index = boundary_end

    spans: list[tuple[int, int]] = []
    cursor = 0
    for boundary in [*boundaries, len(text)]:
        start = cursor
        end = boundary
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append((start, end))
        cursor = boundary
    if not spans and text.strip():
        start = len(text) - len(text.lstrip())
        end = len(text.rstrip())
        spans.append((start, end))
    return spans


def correction_rule_targets(record: dict[str, Any]) -> set[str]:
    targets = {
        selector["rule_id"]
        for selector in record["target"]["selectors"]
        if selector["kind"] == "rule" and selector.get("relation") == "target"
    }
    targets.update(
        reference["target"]
        for reference in record.get("references", [])
        if reference.get("target_type") == "rule"
        and reference.get("relation") in {"target", "conflicts_with", "renamed_from"}
    )
    return targets


def image_payload(image: dict[str, Any]) -> dict[str, Any]:
    return {
        key: image.get(key)
        for key in (
            "source_src",
            "url",
            "alt",
            "title",
            "width",
            "height",
            "link_href",
            "link_url",
        )
    }


def event_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_kind": event.get("event_kind"),
        "target_url": event.get("target_url"),
        "description": event.get("description"),
        "icon": image_payload(event["icon"]),
    }


def primary_field_source_ids(value: Any) -> list[str]:
    field_ids: list[str] = []

    def walk(current: Any) -> None:
        if isinstance(current, dict):
            for field_source in current.get("field_sources", {}).values():
                if field_source["ownership"]["kind"] == "primary":
                    field_ids.append(field_source["field_source_id"])
            for key, child in current.items():
                if key != "field_sources":
                    walk(child)
        elif isinstance(current, list):
            for child in current:
                walk(child)

    walk(value)
    if len(field_ids) != len(set(field_ids)):
        raise ValueError("A component repeats a primary field-source id")
    return field_ids


def iter_field_sources(
    value: Any, path: str = ""
) -> Iterable[tuple[str, str, dict[str, Any]]]:
    if isinstance(value, dict):
        for field_name, field_source in sorted(value.get("field_sources", {}).items()):
            yield f"{path}/{field_name}", field_name, field_source
        for key, child in value.items():
            if key != "field_sources":
                yield from iter_field_sources(child, f"{path}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_field_sources(child, f"{path}/{index}")


class RecordEmitter:
    def __init__(self, rule_id: str) -> None:
        self.rule_id = rule_id
        self.units: list[dict[str, Any]] = []
        self.node_coverage: list[dict[str, Any]] = []
        self.field_units: dict[str, list[str]] = defaultdict(list)

    def next_id(self) -> str:
        return f"{self.rule_id}:clause:{len(self.units) + 1:04d}"

    def add_text(
        self,
        *,
        node: dict[str, Any],
        node_path: str,
        field_path: str,
        field_source_path: str,
        field_source: dict[str, Any],
        provenance_path: str,
        provenance: dict[str, Any],
        unit_kind: str,
        value: str,
        semantic_cue: str | None = None,
    ) -> list[str]:
        if field_source["ownership"]["kind"] != "primary":
            raise ValueError(f"Nonprimary text field cannot emit clauses: {field_path}")
        field_source_id = field_source["field_source_id"]
        unit_ids: list[str] = []
        for start, end in sentence_spans(value):
            text = value[start:end]
            unit_id = self.next_id()
            self.units.append(
                {
                    "unit_id": unit_id,
                    "ordinal": len(self.units) + 1,
                    "source_node_id": node["node_id"],
                    "node_kind": node["kind"],
                    "unit_kind": unit_kind,
                    "ownership": "primary",
                    "source_occurrence_id": None,
                    "component_path": field_path,
                    "field_source_path": field_source_path,
                    "field_source_ids": [field_source_id],
                    "provenance_path": provenance_path,
                    "provenance_manifest_sha256": provenance["manifest_sha256"],
                    "semantic_cue": semantic_cue,
                    "text_start": start,
                    "text_end": end,
                    "text": text,
                    "text_sha256": sha256_text(text),
                    "component_text_sha256": sha256_text(value),
                    "payload": None,
                    "payload_sha256": None,
                }
            )
            unit_ids.append(unit_id)
            self.field_units[field_source_id].append(unit_id)
        if not unit_ids:
            raise ValueError(f"Text component produced no source units: {field_path}")
        return unit_ids

    def add_payload(
        self,
        *,
        node: dict[str, Any],
        component_path: str,
        provenance_path: str,
        provenance: dict[str, Any],
        unit_kind: str,
        payload: dict[str, Any],
        source_occurrence_id: str | None,
        field_source_ids: list[str] | None = None,
    ) -> str:
        field_source_ids = field_source_ids or []
        unit_id = self.next_id()
        self.units.append(
            {
                "unit_id": unit_id,
                "ordinal": len(self.units) + 1,
                "source_node_id": node["node_id"],
                "node_kind": node["kind"],
                "unit_kind": unit_kind,
                "ownership": "primary",
                "source_occurrence_id": source_occurrence_id,
                "component_path": component_path,
                "field_source_path": None,
                "field_source_ids": field_source_ids,
                "provenance_path": provenance_path,
                "provenance_manifest_sha256": provenance["manifest_sha256"],
                "semantic_cue": None,
                "text_start": None,
                "text_end": None,
                "text": None,
                "text_sha256": None,
                "component_text_sha256": None,
                "payload": payload,
                "payload_sha256": sha256_bytes(canonical_json_bytes(payload)),
            }
        )
        for field_source_id in field_source_ids:
            self.field_units[field_source_id].append(unit_id)
        return unit_id

    def add_primary_text_field(
        self,
        *,
        node: dict[str, Any],
        component: dict[str, Any],
        component_path: str,
        field_name: str,
        provenance_path: str,
        provenance: dict[str, Any],
        unit_kind: str,
        semantic_cue: str | None = None,
    ) -> list[str]:
        field_source = component.get("field_sources", {}).get(field_name)
        if (
            field_source is None
            or field_source["ownership"]["kind"] != "primary"
        ):
            return []
        return self.add_text(
            node=node,
            node_path=component_path,
            field_path=f"{component_path}/{field_name}",
            field_source_path=f"{component_path}/field_sources/{field_name}",
            field_source=field_source,
            provenance_path=provenance_path,
            provenance=provenance,
            unit_kind=unit_kind,
            value=component[field_name],
            semantic_cue=semantic_cue,
        )

    def emit_image(
        self,
        node: dict[str, Any],
        image: dict[str, Any],
        image_path: str,
    ) -> str:
        return self.add_payload(
            node=node,
            component_path=image_path,
            provenance_path=f"{image_path}/source",
            provenance=image["source"],
            unit_kind="image_asset",
            payload=image_payload(image),
            source_occurrence_id=image["occurrence_id"],
            field_source_ids=primary_field_source_ids(image),
        )

    def emit_event(
        self,
        node: dict[str, Any],
        event: dict[str, Any],
        event_path: str,
    ) -> str:
        return self.add_payload(
            node=node,
            component_path=event_path,
            provenance_path=f"{event_path}/icon/source",
            provenance=event["icon"]["source"],
            unit_kind="correction_event",
            payload=event_payload(event),
            source_occurrence_id=event["occurrence_id"],
            field_source_ids=primary_field_source_ids(event),
        )

    def emit_footnote(
        self,
        node: dict[str, Any],
        footnote: dict[str, Any],
        footnote_path: str,
        *,
        table: bool,
    ) -> list[str]:
        units: list[str] = []
        units.extend(
            self.add_primary_text_field(
                node=node,
                component=footnote,
                component_path=footnote_path,
                field_name="marker",
                provenance_path=f"{footnote_path}/source",
                provenance=footnote["source"],
                unit_kind=(
                    "table_footnote_marker" if table else "footnote_marker"
                ),
            )
        )
        units.extend(
            self.add_primary_text_field(
                node=node,
                component=footnote,
                component_path=footnote_path,
                field_name="text",
                provenance_path=f"{footnote_path}/source",
                provenance=footnote["source"],
                unit_kind="table_footnote_text" if table else "footnote_text",
            )
        )
        return units

    def emit_cell(
        self,
        node: dict[str, Any],
        cell: dict[str, Any],
        cell_path: str,
        *,
        row: int | None,
        column: int,
    ) -> list[str]:
        units = self.add_primary_text_field(
            node=node,
            component=cell,
            component_path=cell_path,
            field_name="text",
            provenance_path=f"{cell_path}/source",
            provenance=cell["source"],
            unit_kind="table_cell_text",
        )
        for image_index, image in enumerate(cell["images"]):
            image_path = f"{cell_path}/images/{image_index}"
            units.append(self.emit_image(node, image, image_path))
        for event_index, event in enumerate(cell["source_events"]):
            event_path = f"{cell_path}/source_events/{event_index}"
            units.append(self.emit_event(node, event, event_path))
        if not units:
            units.append(
                self.add_payload(
                    node=node,
                    component_path=cell_path,
                    provenance_path=f"{cell_path}/source",
                    provenance=cell["source"],
                    unit_kind="empty_table_cell",
                    payload={
                        "row": row,
                        "column": column,
                        "cell_kind": cell["cell_kind"],
                        "rowspan": cell["rowspan"],
                        "colspan": cell["colspan"],
                    },
                    source_occurrence_id=cell["occurrence_id"],
                )
            )
        return units

    def walk_node(self, node: dict[str, Any], node_path: str) -> None:
        kind = node["kind"]
        own_unit_ids: list[str] = []
        cue = (node.get("semantics_cue") or {}).get("kind")
        text_kinds = {
            "heading": ("text", "heading_text"),
            "paragraph": ("text", "prose_text"),
            "prose": ("text", "prose_text"),
            "list_item": ("text", "list_item_text"),
            "example_block": ("label", "example_label"),
            "note": ("text", "note_text"),
            "caption": ("text", "caption_text"),
        }
        if kind in text_kinds:
            field_name, unit_kind = text_kinds[kind]
            own_unit_ids.extend(
                self.add_primary_text_field(
                    node=node,
                    component=node,
                    component_path=node_path,
                    field_name=field_name,
                    provenance_path=f"{node_path}/source",
                    provenance=node["source"],
                    unit_kind=unit_kind,
                    semantic_cue=cue if kind == "list_item" else None,
                )
            )
        elif kind == "footnote":
            own_unit_ids.extend(
                self.emit_footnote(node, node, node_path, table=False)
            )
        elif kind == "source_event":
            own_unit_ids.append(self.emit_event(node, node, node_path))
        elif kind == "figure":
            if node.get("caption"):
                provenance_name = (
                    "caption_source" if node.get("caption_source") else "source"
                )
                own_unit_ids.extend(
                    self.add_primary_text_field(
                        node=node,
                        component=node,
                        component_path=node_path,
                        field_name="caption",
                        provenance_path=f"{node_path}/{provenance_name}",
                        provenance=node.get("caption_source") or node["source"],
                        unit_kind="figure_caption",
                    )
                )
            for image_index, image in enumerate(node["images"]):
                image_path = f"{node_path}/images/{image_index}"
                own_unit_ids.append(self.emit_image(node, image, image_path))
        elif kind == "orphan_cell":
            own_unit_ids.extend(
                self.emit_cell(node, node, node_path, row=None, column=1)
            )
        elif kind == "table":
            if node.get("caption"):
                provenance_name = (
                    "caption_source" if node.get("caption_source") else "source"
                )
                own_unit_ids.extend(
                    self.add_primary_text_field(
                        node=node,
                        component=node,
                        component_path=node_path,
                        field_name="caption",
                        provenance_path=f"{node_path}/{provenance_name}",
                        provenance=node.get("caption_source") or node["source"],
                        unit_kind="table_caption",
                    )
                )
            for image_index, image in enumerate(node.get("images", [])):
                image_path = f"{node_path}/images/{image_index}"
                own_unit_ids.append(self.emit_image(node, image, image_path))
            for event_index, event in enumerate(node.get("source_events", [])):
                event_path = f"{node_path}/source_events/{event_index}"
                own_unit_ids.append(self.emit_event(node, event, event_path))
            for row_index, row in enumerate(node["rows"]):
                row_path = f"{node_path}/rows/{row_index}"
                for image_index, image in enumerate(row.get("images", [])):
                    image_path = f"{row_path}/images/{image_index}"
                    own_unit_ids.append(self.emit_image(node, image, image_path))
                for event_index, event in enumerate(row.get("source_events", [])):
                    event_path = f"{row_path}/source_events/{event_index}"
                    own_unit_ids.append(self.emit_event(node, event, event_path))
                for cell_index, cell in enumerate(row["cells"]):
                    cell_path = f"{row_path}/cells/{cell_index}"
                    own_unit_ids.extend(
                        self.emit_cell(
                            node,
                            cell,
                            cell_path,
                            row=row_index + 1,
                            column=cell_index + 1,
                        )
                    )
            for cell_index, cell in enumerate(node.get("orphan_cells", [])):
                cell_path = f"{node_path}/orphan_cells/{cell_index}"
                own_unit_ids.extend(
                    self.emit_cell(
                        node,
                        cell,
                        cell_path,
                        row=None,
                        column=cell_index + 1,
                    )
                )
            for footnote_index, footnote in enumerate(node["footnotes"]):
                footnote_path = f"{node_path}/footnotes/{footnote_index}"
                own_unit_ids.extend(
                    self.emit_footnote(
                        node, footnote, footnote_path, table=True
                    )
                )
            if not own_unit_ids:
                own_unit_ids.append(
                    self.add_payload(
                        node=node,
                        component_path=node_path,
                        provenance_path=f"{node_path}/source",
                        provenance=node["source"],
                        unit_kind="empty_table",
                        payload={"empty_table": True},
                        source_occurrence_id=node["occurrence_id"],
                    )
                )
        else:
            raise ValueError(f"Unsupported document node kind: {kind}")

        if not own_unit_ids:
            raise ValueError(f"Document node emitted no primary units: {node_path}")
        self.node_coverage.append(
            {
                "node_id": node["node_id"],
                "node_kind": kind,
                "component_path": node_path,
                "unit_ids": own_unit_ids,
            }
        )
        for child_index, child in enumerate(node.get("children", [])):
            self.walk_node(child, f"{node_path}/children/{child_index}")

    def build_field_source_coverage(
        self, fragment: dict[str, Any]
    ) -> list[dict[str, Any]]:
        coverage: list[dict[str, Any]] = []
        primary_ids: set[str] = set()
        for component_path, field_name, field_source in iter_field_sources(
            fragment["nodes"], "/nodes"
        ):
            field_source_id = field_source["field_source_id"]
            ownership = field_source["ownership"]
            if ownership["kind"] == "primary":
                if field_source_id in primary_ids:
                    raise ValueError(
                        f"Primary field-source id is serialized more than once in "
                        f"{self.rule_id}: {field_source_id}"
                    )
                primary_ids.add(field_source_id)
            unit_ids = self.field_units.get(field_source_id, [])
            if ownership["kind"] == "primary" and not unit_ids:
                raise ValueError(
                    f"Primary field source has no clauses: {component_path}"
                )
            if ownership["kind"] != "primary" and unit_ids:
                raise ValueError(
                    f"Nonprimary field source emitted clauses: {component_path}"
                )
            coverage.append(
                {
                    "field_source_id": field_source_id,
                    "component_path": component_path,
                    "field_name": field_name,
                    "ownership": ownership["kind"],
                    "owner_ref": ownership.get("owner_ref"),
                    "unit_ids": unit_ids,
                }
            )
        return coverage


def build_inventory(
    source: dict[str, Any],
    document_nodes: dict[str, Any],
    corrections: dict[str, Any],
    *,
    source_sha256: str,
    document_nodes_sha256: str,
    corrections_sha256: str,
) -> dict[str, Any]:
    fragments: dict[str, tuple[str, dict[str, Any]]] = {}
    for document in document_nodes["documents"]:
        for fragment in document["fragments"]:
            rule_id = fragment["rule_id"]
            if rule_id in fragments:
                raise ValueError(f"Duplicate document fragment: {rule_id}")
            fragments[rule_id] = (document["document_id"], fragment)

    corrections_by_rule: dict[str, list[str]] = defaultdict(list)
    for correction in corrections["records"]:
        for rule_id in correction_rule_targets(correction):
            corrections_by_rule[rule_id].append(correction["overlay_id"])

    source_ids = [record["source_rule_id"] for record in source["records"]]
    if set(source_ids) != set(fragments):
        raise ValueError("Document-node and source-corpus rule coverage differ")

    records: list[dict[str, Any]] = []
    all_units: list[dict[str, Any]] = []
    document_node_count = 0
    for source_record in source["records"]:
        rule_id = source_record["source_rule_id"]
        document_id, fragment = fragments[rule_id]
        emitter = RecordEmitter(rule_id)
        for node_index, node in enumerate(fragment["nodes"]):
            emitter.walk_node(node, f"/nodes/{node_index}")
        field_source_coverage = emitter.build_field_source_coverage(fragment)
        document_node_count += len(emitter.node_coverage)
        record: dict[str, Any] = {
            "record_id": source_record["record_id"],
            "source_rule_id": rule_id,
            "chapter": source_record["chapter"],
            "document_id": document_id,
            "fragment_ordinal": fragment["ordinal"],
            "source_reference_rule_ids": source_record["html"]["references"],
            "correction_overlay_ids": sorted(set(corrections_by_rule.get(rule_id, []))),
            "source_units": emitter.units,
            "node_coverage": emitter.node_coverage,
            "field_source_coverage": field_source_coverage,
        }
        record["record_sha256"] = sha256_bytes(canonical_json_bytes(record))
        records.append(record)
        all_units.extend(emitter.units)

    unit_kind_counts = Counter(unit["unit_kind"] for unit in all_units)
    counters = {
        "record_count": len(records),
        "document_node_count": document_node_count,
        "source_unit_count": len(all_units),
        "text_unit_count": sum(unit["unit_kind"] in TEXT_UNIT_KINDS for unit in all_units),
        "asset_unit_count": unit_kind_counts["image_asset"],
        "event_unit_count": unit_kind_counts["correction_event"],
        "empty_structural_unit_count": (
            unit_kind_counts["empty_table_cell"] + unit_kind_counts["empty_table"]
        ),
        "field_source_count": sum(
            len(record["field_source_coverage"]) for record in records
        ),
        "primary_field_source_count": sum(
            item["ownership"] == "primary"
            for record in records
            for item in record["field_source_coverage"]
        ),
        "unit_kind_counts": {kind: unit_kind_counts[kind] for kind in UNIT_KINDS},
    }
    inventory: dict[str, Any] = {
        "format": "iupac-bluebook-clause-inventory",
        "format_version": "2.0.0",
        "conversion_stage": "normalized_source_clauses",
        "source_corpus_sha256": source_sha256,
        "document_nodes_sha256": document_nodes_sha256,
        "correction_overlays_sha256": corrections_sha256,
        "segmentation_policy": {
            "text_basis": "normalized document-node field text",
            "boundary_policy": (
                "deterministic sentence and semicolon boundaries with abbreviation and rule-id guards"
            ),
            "whitespace_policy": (
                "non-whitespace characters are covered exactly once; inter-segment gaps must be whitespace"
            ),
            "coverage_policy": (
                "every document node has one node-coverage entry; every primary field source maps to units and nonprimary fields map to none"
            ),
            "asset_policy": (
                "each physical image occurrence is exactly one image or correction-event unit"
            ),
        },
        "counters": counters,
        "records": records,
    }
    inventory["corpus_sha256"] = sha256_bytes(canonical_json_bytes(inventory))
    return inventory


def validate_schema(instance: dict[str, Any], schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    if errors:
        details = "\n".join(
            f"- /{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in errors[:25]
        )
        raise ValueError(f"Clause inventory failed schema validation:\n{details}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the exhaustive normalized source-clause inventory"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--document-nodes", type=Path, default=DEFAULT_DOCUMENT_NODES)
    parser.add_argument("--corrections", type=Path, default=DEFAULT_CORRECTIONS)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_bytes = args.source.read_bytes()
    correction_bytes = args.corrections.read_bytes()
    inventory = build_inventory(
        json.loads(source_bytes.decode("utf-8-sig")),
        load_document_nodes(args.document_nodes),
        json.loads(correction_bytes.decode("utf-8-sig")),
        source_sha256=sha256_bytes(source_bytes),
        document_nodes_sha256=hash_document_nodes(args.document_nodes),
        corrections_sha256=sha256_bytes(correction_bytes),
    )
    validate_schema(inventory, load_json(args.schema))
    write_json(args.out, inventory)
    print(json.dumps(inventory["counters"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
