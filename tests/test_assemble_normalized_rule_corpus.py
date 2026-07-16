from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts import assemble_normalized_rule_corpus as assembler
from scripts import validate_normalized_rule_chunks as chunk_validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "data" / "normalized_rule_language.schema.json"
SOURCE_HASHES = {
    "source_corpus_sha256": "A" * 64,
    "document_nodes_sha256": "B" * 64,
    "correction_overlays_sha256": "C" * 64,
    "clause_inventory_sha256": "D" * 64,
    "reference_occurrences_sha256": "F" * 64,
    "reference_resolutions_sha256": "7" * 64,
}
REFERENCE_HASH_FIELDS = (
    "reference_occurrences_sha256",
    "reference_resolutions_sha256",
)


@dataclass(frozen=True)
class SyntheticCorpus:
    manifest_path: Path
    packet_dir: Path
    chunk_dir: Path
    packets: tuple[dict[str, Any], ...]
    chunks: tuple[dict[str, Any], ...]

    def assemble(self) -> dict[str, Any]:
        return assembler.assemble_rule_corpus(
            manifest_path=self.manifest_path,
            packet_dir=self.packet_dir,
            chunk_dir=self.chunk_dir,
            schema_path=SCHEMA_PATH,
        )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(chunk_validator.canonical_json_bytes(value))


def object_ref(kind: str, object_id: str) -> dict[str, str]:
    return {"kind": kind, "id": object_id}


def expression(expression_id: str, clause_id: str) -> dict[str, Any]:
    return {
        "expression_id": expression_id,
        "clause_ids": [clause_id],
        "op": "literal",
        "value": True,
    }


def symbol(description: str = "A synthetic entity") -> dict[str, Any]:
    return {
        "symbol_id": "entity.synthetic",
        "kind": "entity_type",
        "description": description,
        "arguments": [],
        "returns": "synthetic",
        "grounding": {"kind": "primitive", "refs": [], "primitive": "synthetic"},
    }


def packet_source_unit(rule_id: str, clause_id: str) -> dict[str, Any]:
    return {
        "unit_id": clause_id,
        "ordinal": 1,
        "source_node_id": f"{rule_id}:node:0001",
        "node_kind": "paragraph",
        "unit_kind": "prose_text",
        "ownership": "primary",
        "source_occurrence_id": None,
        "component_path": "/nodes/0/text",
        "field_source_path": "/nodes/0/field_sources/text",
        "field_source_ids": ["field:" + "A" * 24],
        "provenance_path": "/nodes/0/source",
        "provenance_manifest_sha256": "A" * 64,
        "semantic_cue": "unspecified",
        "text_start": 0,
        "text_end": 1,
        "text": "x",
        "text_sha256": "A" * 64,
        "component_text_sha256": "A" * 64,
        "payload": None,
        "payload_sha256": None,
    }


def build_packet(major: int) -> dict[str, Any]:
    packet_id = f"P-{major}-part-001"
    rule_id = f"P-{major}.1"
    record_id = f"bluebook-v3:{rule_id}"
    clause_id = f"{rule_id}:clause:0001"
    packet: dict[str, Any] = {
        "format": "iupac-bluebook-semantic-work-packet",
        "format_version": "1.0.0",
        "packet_id": packet_id,
        **SOURCE_HASHES,
        "output_path": f"data/bluebook_v3/semantic_chunks/{packet_id}.json",
        "assigned_rule_ids": [rule_id],
        "assigned": [
            {
                "source_rule_id": rule_id,
                "source_record": {"record_id": record_id},
                "document_fragment": {"nodes": []},
                "clause_inventory_record": {
                    "record_id": record_id,
                    "source_rule_id": rule_id,
                    "chapter": f"P-{major}",
                    "document_id": f"P-{major}",
                    "fragment_ordinal": 1,
                    "source_reference_rule_ids": [],
                    "correction_overlay_ids": [],
                    "source_units": [packet_source_unit(rule_id, clause_id)],
                    "node_coverage": [
                        {
                            "node_id": f"{rule_id}:node:0001",
                            "node_kind": "paragraph",
                            "component_path": "/nodes/0",
                            "unit_ids": [clause_id],
                        }
                    ],
                    "field_source_coverage": [
                        {
                            "field_source_id": "field:" + "A" * 24,
                            "component_path": "/nodes/0/text",
                            "field_name": "text",
                            "ownership": "primary",
                            "owner_ref": None,
                            "unit_ids": [clause_id],
                        }
                    ],
                    "record_sha256": "A" * 64,
                },
                "immediate_parent": f"chapter:P-{major}",
                "ancestor_chain": [f"chapter:P-{major}"],
                "preceding_rule_ids": [],
                "following_rule_ids": [],
                "outgoing_source_references": [],
                "incoming_source_references": [],
                "reference_occurrences": [],
                "reference_resolutions": [],
                "incoming_reference_occurrence_ids": [],
                "correction_overlay_ids": [],
            }
        ],
        "context_records": [],
        "correction_overlays": [],
        "relation_edges": [
            {
                "source": rule_id,
                "relation": "hierarchy_parent",
                "target": f"chapter:P-{major}",
                "target_kind": "chapter",
            }
        ],
    }
    packet["packet_sha256"] = chunk_validator.digest_without_field(
        packet, "packet_sha256"
    )
    return packet


def build_chunk(packet: dict[str, Any], *, specificity: int) -> dict[str, Any]:
    packet_id = packet["packet_id"]
    rule_id = packet["assigned_rule_ids"][0]
    major = int(packet_id.split("-")[1])
    record_id = f"bluebook-v3:{rule_id}"
    clause_id = f"{rule_id}:clause:0001"
    exception_id = f"exception.p{major}"
    expression_id = f"expr.exception.p{major}"
    references = [
        {
            "reference_id": f"reference.p{major}.a",
            "clause_ids": [clause_id],
            "relation": "cites",
            "source": object_ref("record", record_id),
            "target": object_ref("external", "external.shared"),
            "resolution": "external",
            "ordered_member_refs": [],
        }
    ]
    if major == 1:
        references.append(
            {
                **deepcopy(references[0]),
                "reference_id": "reference.p1.b",
            }
        )
    reference_ids = [reference["reference_id"] for reference in references]
    compiled_targets = [
        object_ref("exception", exception_id),
        object_ref("expression", expression_id),
        *(object_ref("reference", reference_id) for reference_id in reference_ids),
    ]
    chunk: dict[str, Any] = {
        "format": "iupac-bluebook-normalized-rule-chunk",
        "format_version": "1.0.0",
        "packet_id": packet_id,
        "packet_sha256": packet["packet_sha256"],
        "schema_sha256": chunk_validator.language_schema_sha256(),
        **SOURCE_HASHES,
        "assigned_rule_ids": [rule_id],
        "symbol_declarations": [symbol()],
        "clause_dispositions": [
            {
                "clause_id": clause_id,
                "role": "exception",
                "force": "normative",
                "disposition": {"kind": "compiled", "targets": compiled_targets},
            }
        ],
        "records": [
            {
                "record_id": record_id,
                "source_rule_id": rule_id,
                "chapter": f"P-{major}",
                "clause_ids": [clause_id],
                "operative": True,
                "semantic_unit_ids": [],
                "exception_ids": [exception_id],
                "table_ids": [],
                "figure_ids": [],
                "example_ids": [],
                "correction_application_ids": [],
                "reference_ids": reference_ids,
            }
        ],
        "semantic_units": [],
        "exceptions": [
            {
                "exception_id": exception_id,
                "clause_ids": [clause_id],
                "when": expression(expression_id, clause_id),
                "target": object_ref("record", record_id),
                "effect": {
                    "mode": "suppress",
                    "replacement": None,
                    "guard": None,
                    "redirect": None,
                },
                "precedence": {"specificity": specificity, "source_order": major},
            }
        ],
        "tables": [],
        "figures": [],
        "examples": [],
        "correction_applications": [],
        "references": references,
        "chunk_metrics": {},
        "chunk_sha256": "0" * 64,
    }
    stamp_chunk(chunk)
    return chunk


def stamp_chunk(chunk: dict[str, Any]) -> None:
    chunk["chunk_metrics"] = chunk_validator._expected_metrics(chunk)
    chunk["chunk_sha256"] = chunk_validator.digest_without_field(
        chunk, "chunk_sha256"
    )


def build_synthetic_corpus(tmp_path: Path) -> SyntheticCorpus:
    packet_dir = tmp_path / "packets"
    chunk_dir = tmp_path / "chunks"
    packets = (build_packet(1), build_packet(2))
    chunks = (
        build_chunk(packets[0], specificity=1),
        build_chunk(packets[1], specificity=3),
    )
    entries = [
        {
            "packet_id": packet["packet_id"],
            "packet_sha256": packet["packet_sha256"],
            "output_path": packet["output_path"],
            "assigned_rule_ids": packet["assigned_rule_ids"],
        }
        for packet in packets
    ]
    manifest: dict[str, Any] = {
        "format": "iupac-bluebook-semantic-work-packet-manifest",
        "format_version": "1.0.0",
        **SOURCE_HASHES,
        "source_pages_sha256": "E" * 64,
        "effective_through": "2026-07-15",
        "packet_count": len(packets),
        "assigned_rule_count": sum(len(entry["assigned_rule_ids"]) for entry in entries),
        "packets": entries,
    }
    manifest["manifest_sha256"] = chunk_validator.digest_without_field(
        manifest, "manifest_sha256"
    )
    manifest_path = packet_dir / "manifest.json"
    write_json(manifest_path, manifest)
    for packet, chunk in zip(packets, chunks):
        write_json(packet_dir / f"{packet['packet_id']}.json", packet)
        write_json(chunk_dir / f"{packet['packet_id']}.json", chunk)
    return SyntheticCorpus(manifest_path, packet_dir, chunk_dir, packets, chunks)


@pytest.fixture
def synthetic(tmp_path: Path) -> SyntheticCorpus:
    return build_synthetic_corpus(tmp_path)


def test_valid_multi_chunk_assembly_merges_and_projects_all_typed_objects(
    synthetic: SyntheticCorpus,
) -> None:
    corpus = synthetic.assemble()

    assert [record["source_rule_id"] for record in corpus["records"]] == [
        "P-1.1",
        "P-2.1",
    ]
    assert [item["clause_id"] for item in corpus["clause_dispositions"]] == [
        "P-1.1:clause:0001",
        "P-2.1:clause:0001",
    ]
    assert corpus["symbol_registry"]["symbols"] == [symbol()]
    assert {
        field: corpus["source_snapshot"][field] for field in REFERENCE_HASH_FIELDS
    } == {field: SOURCE_HASHES[field] for field in REFERENCE_HASH_FIELDS}
    assert [item["exception_id"] for item in corpus["exceptions"]] == [
        "exception.p2",
        "exception.p1",
    ]
    shared = next(
        edge
        for edge in corpus["dependency_edges"]
        if edge["from"]["id"] == "bluebook-v3:P-1.1"
        and edge["to"]["id"] == "external.shared"
    )
    assert shared["derived_from_object_ids"] == ["reference.p1.a", "reference.p1.b"]
    assert corpus["metrics"]["dependency_edge_count"] == 4
    assert corpus["corpus_sha256"] == chunk_validator.digest_without_field(
        corpus, "corpus_sha256"
    )


def test_assembly_hash_and_bytes_are_deterministic(synthetic: SyntheticCorpus) -> None:
    first = synthetic.assemble()
    second = synthetic.assemble()

    assert first["corpus_sha256"] == second["corpus_sha256"]
    assert chunk_validator.canonical_json_bytes(first) == chunk_validator.canonical_json_bytes(
        second
    )


def test_missing_chunk_is_rejected(synthetic: SyntheticCorpus) -> None:
    (synthetic.chunk_dir / "P-2-part-001.json").unlink()

    with pytest.raises(assembler.AssemblyError, match="requires exactly one semantic chunk; found 0"):
        synthetic.assemble()


def test_duplicate_chunk_is_rejected(synthetic: SyntheticCorpus) -> None:
    write_json(synthetic.chunk_dir / "duplicate.json", synthetic.chunks[0])

    with pytest.raises(assembler.AssemblyError, match="requires exactly one semantic chunk; found 2"):
        synthetic.assemble()


def test_invalid_chunk_is_rejected_by_the_chunk_validator(
    synthetic: SyntheticCorpus,
) -> None:
    invalid = deepcopy(synthetic.chunks[0])
    invalid["format"] = "not-a-normalized-chunk"
    stamp_chunk(invalid)
    write_json(synthetic.chunk_dir / "P-1-part-001.json", invalid)

    with pytest.raises(assembler.AssemblyError, match="chunk.schema"):
        synthetic.assemble()


@pytest.mark.parametrize("field", REFERENCE_HASH_FIELDS)
@pytest.mark.parametrize("layer", ["manifest", "packet", "chunk"])
def test_reference_source_hash_tampering_is_rejected(
    synthetic: SyntheticCorpus, field: str, layer: str
) -> None:
    tampered_hash = "9" * 64
    manifest = dict(chunk_validator.load_json(synthetic.manifest_path))

    if layer == "manifest":
        manifest[field] = tampered_hash
    elif layer == "packet":
        packet = deepcopy(synthetic.packets[0])
        packet[field] = tampered_hash
        packet["packet_sha256"] = chunk_validator.digest_without_field(
            packet, "packet_sha256"
        )
        manifest["packets"][0]["packet_sha256"] = packet["packet_sha256"]
        write_json(synthetic.packet_dir / f"{packet['packet_id']}.json", packet)
    else:
        chunk = deepcopy(synthetic.chunks[0])
        chunk[field] = tampered_hash
        stamp_chunk(chunk)
        write_json(synthetic.chunk_dir / f"{chunk['packet_id']}.json", chunk)

    manifest["manifest_sha256"] = chunk_validator.digest_without_field(
        manifest, "manifest_sha256"
    )
    write_json(synthetic.manifest_path, manifest)

    with pytest.raises(assembler.AssemblyError, match=field):
        synthetic.assemble()


def test_conflicting_symbol_declarations_are_rejected(
    synthetic: SyntheticCorpus,
) -> None:
    conflicting = deepcopy(synthetic.chunks[1])
    conflicting["symbol_declarations"] = [symbol("A conflicting declaration")]
    stamp_chunk(conflicting)
    write_json(synthetic.chunk_dir / "P-2-part-001.json", conflicting)

    with pytest.raises(assembler.AssemblyError, match="Conflicting declarations for symbol"):
        synthetic.assemble()


def test_dangling_projected_edge_endpoint_is_rejected_independently(
    synthetic: SyntheticCorpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    dangling = deepcopy(synthetic.chunks[0])
    dangling["references"][0]["target"] = object_ref(
        "record", "bluebook-v3:P-9.9"
    )
    dangling["references"][0]["resolution"] = "exact"
    stamp_chunk(dangling)
    write_json(synthetic.chunk_dir / "P-1-part-001.json", dangling)
    monkeypatch.setattr(
        assembler.chunk_validator,
        "validate_chunk",
        lambda *args, **kwargs: {"passed": True, "errors": []},
    )

    with pytest.raises(assembler.AssemblyError, match="Dangling typed object reference"):
        synthetic.assemble()


def test_cross_chunk_object_id_collision_is_rejected(
    synthetic: SyntheticCorpus,
) -> None:
    colliding = deepcopy(synthetic.chunks[1])
    old_id = colliding["exceptions"][0]["exception_id"]
    new_id = "bluebook-v3:P-1.1"
    colliding["exceptions"][0]["exception_id"] = new_id
    colliding["records"][0]["exception_ids"] = [new_id]
    for target in colliding["clause_dispositions"][0]["disposition"]["targets"]:
        if target == object_ref("exception", old_id):
            target["id"] = new_id
    stamp_chunk(colliding)
    write_json(synthetic.chunk_dir / "P-2-part-001.json", colliding)

    with pytest.raises(assembler.AssemblyError, match="Addressable object ID collision"):
        synthetic.assemble()
