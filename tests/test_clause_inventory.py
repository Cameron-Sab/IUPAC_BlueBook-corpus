from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator, FormatChecker

from scripts import build_clause_inventory as builder
from scripts import document_node_store


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "bluebook_v3"
SOURCE_PATH = BASE / "bluebook_v3_source_corpus.json"
DOCUMENT_NODES_PATH = document_node_store.DEFAULT_STORE
CORRECTIONS_PATH = BASE / "bluebook_v3_correction_overlays.json"
INVENTORY_PATH = BASE / "bluebook_v3_clause_inventory.json"
SCHEMA_PATH = ROOT / "data" / "bluebook_clause_inventory.schema.json"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolve_pointer(value: Any, pointer: str) -> Any:
    current = value
    for part in pointer.lstrip("/").split("/"):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def walk_nodes(nodes: list[dict[str, Any]], prefix: str = "/nodes") -> Iterator[tuple[str, dict[str, Any]]]:
    for index, node in enumerate(nodes):
        path = f"{prefix}/{index}"
        yield path, node
        yield from walk_nodes(node.get("children", []), f"{path}/children")


def without_digest(value: dict[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def test_clause_inventory_is_schema_valid_canonical_and_deterministic() -> None:
    source_bytes = SOURCE_PATH.read_bytes()
    document_nodes = document_node_store.load_document_nodes(DOCUMENT_NODES_PATH)
    correction_bytes = CORRECTIONS_PATH.read_bytes()
    inventory = load(INVENTORY_PATH)
    schema = load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(inventory)
    assert INVENTORY_PATH.read_bytes() == builder.canonical_json_bytes(inventory)

    rebuilt = builder.build_inventory(
        json.loads(source_bytes.decode("utf-8-sig")),
        document_nodes,
        json.loads(correction_bytes.decode("utf-8-sig")),
        source_sha256=builder.sha256_bytes(source_bytes),
        document_nodes_sha256=document_node_store.hash_document_nodes(
            DOCUMENT_NODES_PATH
        ),
        corrections_sha256=builder.sha256_bytes(correction_bytes),
    )
    assert builder.canonical_json_bytes(rebuilt) == INVENTORY_PATH.read_bytes()


def test_every_document_node_and_source_component_is_covered_exactly() -> None:
    inventory = load(INVENTORY_PATH)
    document_nodes = document_node_store.load_document_nodes(DOCUMENT_NODES_PATH)
    fragments = {
        fragment["rule_id"]: fragment
        for document in document_nodes["documents"]
        for fragment in document["fragments"]
    }
    observed_node_count = 0
    observed_unit_ids: list[str] = []
    for record in inventory["records"]:
        rule_id = record["source_rule_id"]
        fragment = fragments[rule_id]
        nodes_by_path = dict(walk_nodes(fragment["nodes"]))
        coverage = record["node_coverage"]
        assert [item["component_path"] for item in coverage] == list(nodes_by_path)
        assert [item["node_id"] for item in coverage] == [
            node["node_id"] for node in nodes_by_path.values()
        ]
        observed_node_count += len(coverage)

        units = record["source_units"]
        units_by_id = {unit["unit_id"]: unit for unit in units}
        assert len(units_by_id) == len(units)
        assert [unit["ordinal"] for unit in units] == list(range(1, len(units) + 1))
        assert [unit["unit_id"] for unit in units] == [
            f"{rule_id}:clause:{ordinal:04d}" for ordinal in range(1, len(units) + 1)
        ]
        covered_ids = [unit_id for item in coverage for unit_id in item["unit_ids"]]
        assert len(covered_ids) == len(set(covered_ids))
        assert set(covered_ids) == set(units_by_id)
        for item in coverage:
            node = nodes_by_path[item["component_path"]]
            assert item["node_id"] == node["node_id"]
            assert item["node_kind"] == node["kind"]
            assert all(
                units_by_id[unit_id]["source_node_id"] == node["node_id"]
                for unit_id in item["unit_ids"]
            )

        ranges_by_component: dict[str, list[tuple[int, int]]] = defaultdict(list)
        unit_ids_by_field: dict[str, list[str]] = defaultdict(list)
        for unit in units:
            observed_unit_ids.append(unit["unit_id"])
            assert unit["ownership"] == "primary"
            provenance = resolve_pointer(fragment, unit["provenance_path"])
            assert provenance["manifest_sha256"] == unit["provenance_manifest_sha256"]
            component = resolve_pointer(fragment, unit["component_path"])
            for field_source_id in unit["field_source_ids"]:
                unit_ids_by_field[field_source_id].append(unit["unit_id"])
            if unit["unit_kind"] in builder.TEXT_UNIT_KINDS:
                assert isinstance(component, str)
                assert unit["source_occurrence_id"] is None
                field_source = resolve_pointer(fragment, unit["field_source_path"])
                assert field_source["ownership"]["kind"] == "primary"
                assert unit["field_source_ids"] == [field_source["field_source_id"]]
                assert unit["component_text_sha256"] == builder.sha256_text(component)
                start, end = unit["text_start"], unit["text_end"]
                assert component[start:end] == unit["text"]
                assert unit["text_sha256"] == builder.sha256_text(unit["text"])
                assert unit["payload"] is None and unit["payload_sha256"] is None
                ranges_by_component[unit["component_path"]].append((start, end))
            else:
                assert unit["field_source_path"] is None
                assert unit["source_occurrence_id"] is not None
                assert unit["text_start"] is None and unit["text_end"] is None
                assert unit["text"] is None and unit["text_sha256"] is None
                assert unit["component_text_sha256"] is None
                assert unit["payload_sha256"] == builder.sha256_bytes(
                    builder.canonical_json_bytes(unit["payload"])
                )
                if unit["unit_kind"] == "image_asset":
                    assert unit["payload"] == builder.image_payload(component)
                elif unit["unit_kind"] == "correction_event":
                    assert unit["payload"] == builder.event_payload(component)
                elif component.get("cell_kind"):
                    assert unit["payload"]["cell_kind"] == component["cell_kind"]
                else:
                    assert unit["payload"] == {"empty_table": True}

        for component_path, ranges in ranges_by_component.items():
            component = resolve_pointer(fragment, component_path)
            coverage_counts = [0] * len(component)
            for start, end in ranges:
                for index in range(start, end):
                    coverage_counts[index] += 1
            assert all(
                count == 1 if not char.isspace() else count in {0, 1}
                for char, count in zip(component, coverage_counts)
            )

        observed_field_sources = list(
            builder.iter_field_sources(fragment["nodes"], "/nodes")
        )
        field_coverage = record["field_source_coverage"]
        assert len(field_coverage) == len(observed_field_sources)
        for coverage_item, (component_path, field_name, field_source) in zip(
            field_coverage, observed_field_sources
        ):
            assert coverage_item["field_source_id"] == field_source["field_source_id"]
            assert coverage_item["component_path"] == component_path
            assert coverage_item["field_name"] == field_name
            assert coverage_item["ownership"] == field_source["ownership"]["kind"]
            assert coverage_item["owner_ref"] == field_source["ownership"]["owner_ref"]
            expected_unit_ids = unit_ids_by_field.get(
                field_source["field_source_id"], []
            )
            if coverage_item["ownership"] == "primary":
                assert expected_unit_ids
                assert coverage_item["unit_ids"] == expected_unit_ids
            else:
                assert not expected_unit_ids
                assert coverage_item["unit_ids"] == []

        assert record["record_sha256"] == builder.sha256_bytes(
            builder.canonical_json_bytes(without_digest(record, "record_sha256"))
        )

    assert observed_node_count == inventory["counters"]["document_node_count"] == 14453
    assert len(observed_unit_ids) == len(set(observed_unit_ids))
    assert len(observed_unit_ids) == inventory["counters"]["source_unit_count"]


def test_inventory_counters_hashes_references_and_corrections_reconstruct() -> None:
    inventory = load(INVENTORY_PATH)
    source = load(SOURCE_PATH)
    corrections = load(CORRECTIONS_PATH)
    records_by_id = {record["source_rule_id"]: record for record in source["records"]}
    corrections_by_rule: dict[str, list[str]] = defaultdict(list)
    for correction in corrections["records"]:
        for rule_id in builder.correction_rule_targets(correction):
            corrections_by_rule[rule_id].append(correction["overlay_id"])

    all_units = []
    for record in inventory["records"]:
        source_record = records_by_id[record["source_rule_id"]]
        assert record["source_reference_rule_ids"] == source_record["html"]["references"]
        assert record["correction_overlay_ids"] == sorted(
            set(corrections_by_rule.get(record["source_rule_id"], []))
        )
        all_units.extend(record["source_units"])

    kinds = Counter(unit["unit_kind"] for unit in all_units)
    expected_counters = {
        "record_count": len(inventory["records"]),
        "document_node_count": sum(
            len(record["node_coverage"]) for record in inventory["records"]
        ),
        "source_unit_count": len(all_units),
        "text_unit_count": sum(
            unit["unit_kind"] in builder.TEXT_UNIT_KINDS for unit in all_units
        ),
        "asset_unit_count": kinds["image_asset"],
        "event_unit_count": kinds["correction_event"],
        "empty_structural_unit_count": (
            kinds["empty_table_cell"] + kinds["empty_table"]
        ),
        "field_source_count": sum(
            len(record["field_source_coverage"]) for record in inventory["records"]
        ),
        "primary_field_source_count": sum(
            item["ownership"] == "primary"
            for record in inventory["records"]
            for item in record["field_source_coverage"]
        ),
        "unit_kind_counts": {kind: kinds[kind] for kind in builder.UNIT_KINDS},
    }
    assert inventory["counters"] == expected_counters
    assert expected_counters["record_count"] == 2554
    assert expected_counters["document_node_count"] == 14453
    assert expected_counters["source_unit_count"] == 32408
    assert expected_counters["text_unit_count"] == 24690
    assert expected_counters["asset_unit_count"] == 5181
    assert expected_counters["event_unit_count"] == 190
    assert expected_counters["empty_structural_unit_count"] == 2347
    assert expected_counters["field_source_count"] == 38256
    assert expected_counters["primary_field_source_count"] == 25413
    assert inventory["corpus_sha256"] == builder.sha256_bytes(
        builder.canonical_json_bytes(without_digest(inventory, "corpus_sha256"))
    )


def test_sentence_segmentation_preserves_nonwhitespace_and_guards_rule_ids() -> None:
    text = (
        "Apply P-44.3.2 and Fig. 2 first; retain the senior parent. "
        "For example, i.e. in a tie, continue to P-44.3.3."
    )
    spans = builder.sentence_spans(text)
    assert [text[start:end] for start, end in spans] == [
        "Apply P-44.3.2 and Fig. 2 first;",
        "retain the senior parent.",
        "For example, i.e. in a tie, continue to P-44.3.3.",
    ]
    counts = [0] * len(text)
    for start, end in spans:
        for index in range(start, end):
            counts[index] += 1
    assert all(
        count == 1 if not char.isspace() else count in {0, 1}
        for char, count in zip(text, counts)
    )
