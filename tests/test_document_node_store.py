from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from scripts import document_node_store as store


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "bluebook_v3" / "bluebook_v3_document_nodes.json"
SCHEMA = ROOT / "data" / "bluebook_document_node_store.schema.json"
EXPECTED_SOURCE_BYTES = 240_001_951
EXPECTED_SOURCE_SHA256 = "12ACBC54AB468C7CF7C2B13D9BA0C4E6E3FAFFA976E0FF4CA2D2ECEC10376CBF"


@pytest.fixture(scope="session")
def shard_store(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    path = tmp_path_factory.mktemp("document-node-store")
    manifest = store.generate_store(SOURCE, path)
    return path, manifest


@contextmanager
def replaced_bytes(path: Path, replacement: bytes) -> Iterator[None]:
    original = path.read_bytes()
    path.write_bytes(replacement)
    try:
        yield
    finally:
        path.write_bytes(original)


def read_manifest(path: Path) -> dict:
    return json.loads((path / store.MANIFEST_NAME).read_text(encoding="utf-8"))


def test_schema_is_strict_draft_2020_12() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert all(
        definition.get("additionalProperties") is False
        for definition in schema["$defs"].values()
        if definition.get("type") == "object"
    )


def test_generation_is_canonical_deterministic_and_github_safe(
    shard_store: tuple[Path, dict],
) -> None:
    path, manifest = shard_store
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert (path / store.MANIFEST_NAME).read_bytes() == store.canonical_json_bytes(manifest)
    assert manifest["manifest_sha256"] == store.manifest_sha256(manifest)
    assert manifest["document_count"] == 11
    assert tuple(item["document_id"] for item in manifest["shards"]) == (
        store.OFFICIAL_DOCUMENT_IDS
    )
    assert manifest["reconstructed_byte_count"] == EXPECTED_SOURCE_BYTES
    assert manifest["reconstructed_sha256"] == EXPECTED_SOURCE_SHA256

    first_files = {
        item["path"]: (path / item["path"]).read_bytes()
        for item in manifest["shards"]
    }
    assert all(0 < len(raw) < store.MAX_SHARD_BYTES for raw in first_files.values())
    assert all(
        raw == store.canonical_json_bytes(json.loads(raw)) for raw in first_files.values()
    )

    regenerated = store.generate_store(SOURCE, path)
    assert store.canonical_json_bytes(regenerated) == store.canonical_json_bytes(manifest)
    assert {
        item["path"]: (path / item["path"]).read_bytes()
        for item in regenerated["shards"]
    } == first_files


def test_round_trip_reconstructs_current_monolith_byte_for_byte(
    shard_store: tuple[Path, dict], tmp_path: Path
) -> None:
    path, manifest = shard_store
    output = tmp_path / "bluebook_v3_document_nodes.json"
    reconstructed = store.reconstruct_store(path, output)

    assert reconstructed == SOURCE.read_bytes()
    assert output.read_bytes() == reconstructed
    assert store.sha256_bytes(reconstructed) == EXPECTED_SOURCE_SHA256
    assert store.sha256_bytes(reconstructed) == manifest["reconstructed_sha256"]
    assert store.canonical_json_bytes(store.load_document_nodes(path)) == reconstructed
    assert store.hash_document_nodes(path) == EXPECTED_SOURCE_SHA256


def test_manifest_rejects_noncanonical_bytes(shard_store: tuple[Path, dict]) -> None:
    path, _ = shard_store
    manifest_path = path / store.MANIFEST_NAME
    with replaced_bytes(manifest_path, b" " + manifest_path.read_bytes()):
        with pytest.raises(store.DocumentNodeStoreError, match="not canonical JSON"):
            store.load_manifest(path)


def test_manifest_rejects_hash_mutation(shard_store: tuple[Path, dict]) -> None:
    path, _ = shard_store
    manifest_path = path / store.MANIFEST_NAME
    mutated = read_manifest(path)
    mutated["reconstructed_sha256"] = "0" * 64
    with replaced_bytes(manifest_path, store.canonical_json_bytes(mutated)):
        with pytest.raises(store.DocumentNodeStoreError, match="manifest_sha256 is invalid"):
            store.load_manifest(path)


def test_store_rejects_noncanonical_shard_bytes(shard_store: tuple[Path, dict]) -> None:
    path, manifest = shard_store
    shard_path = path / manifest["shards"][0]["path"]
    with replaced_bytes(shard_path, shard_path.read_bytes() + b"\n"):
        with pytest.raises(store.DocumentNodeStoreError, match="not canonical JSON"):
            store.load_store(path)


def test_store_rejects_rehashed_document_counter_mutation(
    shard_store: tuple[Path, dict],
) -> None:
    path, _ = shard_store
    manifest_path = path / store.MANIFEST_NAME
    mutated = read_manifest(path)
    mutated["shards"][0]["document_node_count"] += 1
    mutated["manifest_sha256"] = store.manifest_sha256(mutated)

    with replaced_bytes(manifest_path, store.canonical_json_bytes(mutated)):
        with pytest.raises(store.DocumentNodeStoreError, match="document counters"):
            store.load_store(path)


def test_schema_and_loader_reject_unknown_manifest_fields(
    shard_store: tuple[Path, dict],
) -> None:
    path, manifest = shard_store
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(manifest)
    mutated["unexpected"] = True
    errors = list(Draft202012Validator(schema).iter_errors(mutated))
    assert any("Additional properties" in error.message for error in errors)

    mutated["manifest_sha256"] = store.manifest_sha256(mutated)
    manifest_path = path / store.MANIFEST_NAME
    with replaced_bytes(manifest_path, store.canonical_json_bytes(mutated)):
        with pytest.raises(store.DocumentNodeStoreError, match="Draft 2020-12"):
            store.load_manifest(path)
