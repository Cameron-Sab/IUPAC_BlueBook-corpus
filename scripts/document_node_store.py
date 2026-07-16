from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
BLUEBOOK_V3 = ROOT / "data" / "bluebook_v3"
DEFAULT_SOURCE = BLUEBOOK_V3 / "bluebook_v3_document_nodes.json"
DEFAULT_STORE = BLUEBOOK_V3 / "bluebook_v3_document_nodes"
DEFAULT_STORE_SCHEMA = ROOT / "data" / "bluebook_document_node_store.schema.json"
DEFAULT_DOCUMENT_SCHEMA = ROOT / "data" / "bluebook_document_nodes.schema.json"

MANIFEST_NAME = "manifest.json"
STORE_FORMAT = "iupac-bluebook-document-node-store"
STORE_VERSION = "1.0.0"
MAX_SHARD_BYTES = 100_000_000
OFFICIAL_DOCUMENT_IDS = (
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
)
NODE_KINDS = (
    "heading",
    "paragraph",
    "prose",
    "list_item",
    "table",
    "figure",
    "example_block",
    "note",
    "caption",
    "source_event",
    "footnote",
    "orphan_cell",
)
CORPUS_KEYS = {
    "corpus_sha256",
    "counters",
    "documents",
    "format",
    "metrics",
    "source_scope",
    "version",
}
DOCUMENT_KEYS = {
    "active_rule_fragment_count",
    "cache_path",
    "document_id",
    "document_node_count",
    "fragments",
    "node_kind_counts",
    "source_byte_count",
    "source_encoding",
    "source_metrics",
    "source_sha256",
    "source_url",
}


class DocumentNodeStoreError(ValueError):
    """Raised when a document-node store violates an integrity invariant."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise DocumentNodeStoreError(f"Value is not canonical JSON data: {error}") from error
    return (rendered + "\n").encode("utf-8")


def _reject_json_constant(value: str) -> None:
    raise DocumentNodeStoreError(f"Non-finite JSON number is forbidden: {value}")


def _decode_json(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DocumentNodeStoreError(f"{label} is not UTF-8: {error}") from error
    try:
        return json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, DocumentNodeStoreError) as error:
        raise DocumentNodeStoreError(f"{label} is not valid strict JSON: {error}") from error


def _read_canonical_json(path: Path, label: str) -> tuple[Any, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise DocumentNodeStoreError(f"Cannot read {label} at {path}: {error}") from error
    value = _decode_json(raw, label)
    if raw != canonical_json_bytes(value):
        raise DocumentNodeStoreError(f"{label} is not canonical JSON: {path}")
    return value, raw


def _schema_validator(schema_path: Path, *, document: bool = False) -> Any:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as error:
        raise RuntimeError(
            "jsonschema is required; install the project's conversion extra"
        ) from error

    try:
        schema_raw = schema_path.read_bytes()
    except OSError as error:
        raise DocumentNodeStoreError(
            f"Cannot read JSON Schema at {schema_path}: {error}"
        ) from error
    schema = _decode_json(schema_raw, "JSON Schema")
    Draft202012Validator.check_schema(schema)
    if document:
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": schema["$defs"],
            "$ref": "#/$defs/Document",
        }
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_with_schema(value: Any, validator: Any, label: str) -> None:
    errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    if not errors:
        return
    details = "; ".join(
        f"/{'/'.join(map(str, error.absolute_path))}: {error.message}"
        for error in errors[:8]
    )
    raise DocumentNodeStoreError(f"{label} failed Draft 2020-12 validation: {details}")


def manifest_sha256(manifest: Mapping[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("manifest_sha256", None)
    return sha256_bytes(canonical_json_bytes(payload))


def corpus_semantic_sha256(corpus: Mapping[str, Any]) -> str:
    payload = {
        "documents": corpus["documents"],
        "counters": corpus["counters"],
        "metrics": corpus["metrics"],
    }
    return sha256_bytes(canonical_json_bytes(payload))


def shard_filename(document_id: str) -> str:
    if document_id not in OFFICIAL_DOCUMENT_IDS:
        raise DocumentNodeStoreError(f"Unknown official document id: {document_id}")
    return f"bluebook_v3_document_nodes.{document_id}.json"


def _document_metadata(document: Mapping[str, Any], raw: bytes, ordinal: int) -> dict[str, Any]:
    return {
        "active_rule_fragment_count": document["active_rule_fragment_count"],
        "byte_count": len(raw),
        "document_id": document["document_id"],
        "document_node_count": document["document_node_count"],
        "node_kind_counts": document["node_kind_counts"],
        "ordinal": ordinal,
        "path": shard_filename(document["document_id"]),
        "sha256": sha256_bytes(raw),
    }


def _write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _assert_corpus_shape(corpus: Any) -> None:
    if not isinstance(corpus, dict):
        raise DocumentNodeStoreError("Document-node corpus must be a JSON object")
    if set(corpus) != CORPUS_KEYS:
        raise DocumentNodeStoreError("Document-node corpus has noncanonical top-level fields")
    if (
        corpus["format"] != "iupac-bluebook-document-nodes"
        or corpus["version"] != "2.0.0"
        or corpus["source_scope"]
        != "active normative rule fragments in cached official chapter HTML"
    ):
        raise DocumentNodeStoreError("Document-node corpus identity is invalid")
    documents = corpus.get("documents")
    if not isinstance(documents, list):
        raise DocumentNodeStoreError("Document-node corpus must contain a documents array")
    ids = [
        document.get("document_id")
        for document in documents
        if isinstance(document, dict)
    ]
    if len(ids) != len(documents) or tuple(ids) != OFFICIAL_DOCUMENT_IDS:
        raise DocumentNodeStoreError(
            "Document-node corpus must contain the 11 official documents in canonical order"
        )


def _iter_fragment_nodes(fragment: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    nodes = fragment.get("nodes")
    if not isinstance(nodes, list):
        raise DocumentNodeStoreError("Document fragment nodes must be an array")
    pending = list(reversed(nodes))
    while pending:
        node = pending.pop()
        if not isinstance(node, dict) or node.get("kind") not in NODE_KINDS:
            raise DocumentNodeStoreError("Document fragment contains an invalid node")
        yield node
        children = node.get("children", [])
        if not isinstance(children, list):
            raise DocumentNodeStoreError("Document node children must be an array")
        pending.extend(reversed(children))


def _assert_document_invariants(document: Any, expected_id: str) -> None:
    if not isinstance(document, dict) or set(document) != DOCUMENT_KEYS:
        raise DocumentNodeStoreError(f"Shard {expected_id} has invalid document fields")
    if document["document_id"] != expected_id:
        raise DocumentNodeStoreError(f"Shard {expected_id} contains the wrong document")
    fragments = document["fragments"]
    if not isinstance(fragments, list) or not fragments:
        raise DocumentNodeStoreError(f"Shard {expected_id} has invalid fragments")

    actual_kinds: Counter[str] = Counter()
    actual_node_count = 0
    for fragment in fragments:
        if not isinstance(fragment, dict):
            raise DocumentNodeStoreError(f"Shard {expected_id} has an invalid fragment")
        fragment_nodes = list(_iter_fragment_nodes(fragment))
        if fragment.get("node_count") != len(fragment_nodes):
            raise DocumentNodeStoreError(f"Shard {expected_id} has an invalid fragment counter")
        actual_node_count += len(fragment_nodes)
        actual_kinds.update(node["kind"] for node in fragment_nodes)

    expected_kinds = {kind: actual_kinds[kind] for kind in NODE_KINDS}
    if document["active_rule_fragment_count"] != len(fragments):
        raise DocumentNodeStoreError(f"Shard {expected_id} has an invalid fragment count")
    if document["document_node_count"] != actual_node_count:
        raise DocumentNodeStoreError(f"Shard {expected_id} has an invalid node count")
    if document["node_kind_counts"] != expected_kinds:
        raise DocumentNodeStoreError(f"Shard {expected_id} has invalid node-kind counts")


def _assert_corpus_invariants(corpus: Mapping[str, Any]) -> None:
    documents = corpus["documents"]
    counters = corpus["counters"]
    aggregate_kinds: Counter[str] = Counter()
    for document in documents:
        _assert_document_invariants(document, document["document_id"])
        aggregate_kinds.update(document["node_kind_counts"])
    expected_counters = {
        "active_rule_fragment_count": sum(
            document["active_rule_fragment_count"] for document in documents
        ),
        "document_count": len(documents),
        "document_node_count": sum(
            document["document_node_count"] for document in documents
        ),
        "node_kind_counts": {kind: aggregate_kinds[kind] for kind in NODE_KINDS},
    }
    if counters != expected_counters:
        raise DocumentNodeStoreError("Corpus counters do not equal the shard document counters")
    expected_semantic_hash = corpus_semantic_sha256(corpus)
    if corpus["corpus_sha256"] != expected_semantic_hash:
        raise DocumentNodeStoreError(
            "Corpus corpus_sha256 does not match its canonical documents/counters/metrics payload"
        )


def generate_store(
    source_path: Path = DEFAULT_SOURCE,
    store_dir: Path = DEFAULT_STORE,
    *,
    store_schema_path: Path = DEFAULT_STORE_SCHEMA,
    document_schema_path: Path = DEFAULT_DOCUMENT_SCHEMA,
    validate_document_schema: bool = False,
) -> dict[str, Any]:
    """Generate one canonical shard per official document and return its manifest."""

    source_path = Path(source_path)
    store_dir = Path(store_dir)
    corpus, source_raw = _read_canonical_json(source_path, "document-node corpus")
    _assert_corpus_shape(corpus)
    if validate_document_schema:
        document_validator = _schema_validator(document_schema_path, document=True)
        for document in corpus["documents"]:
            _validate_with_schema(document, document_validator, document["document_id"])
    _assert_corpus_invariants(corpus)

    shard_entries: list[dict[str, Any]] = []
    for ordinal, document in enumerate(corpus["documents"], start=1):
        raw = canonical_json_bytes(document)
        if len(raw) >= MAX_SHARD_BYTES:
            raise DocumentNodeStoreError(
                f"Shard {document['document_id']} is {len(raw)} bytes; "
                f"it must be smaller than {MAX_SHARD_BYTES} bytes"
            )
        entry = _document_metadata(document, raw, ordinal)
        shard_entries.append(entry)
        _write_bytes(store_dir / entry["path"], raw)

    envelope = {key: value for key, value in corpus.items() if key != "documents"}
    manifest: dict[str, Any] = {
        "corpus": envelope,
        "document_count": len(shard_entries),
        "format": STORE_FORMAT,
        "reconstructed_byte_count": len(source_raw),
        "reconstructed_sha256": sha256_bytes(source_raw),
        "shards": shard_entries,
        "version": STORE_VERSION,
    }
    manifest["manifest_sha256"] = manifest_sha256(manifest)
    validator = _schema_validator(store_schema_path)
    _validate_with_schema(manifest, validator, "store manifest")
    _write_bytes(store_dir / MANIFEST_NAME, canonical_json_bytes(manifest))
    return manifest


def _manifest_path(path: Path) -> Path:
    path = Path(path)
    return path / MANIFEST_NAME if path.is_dir() else path


def load_manifest(
    path: Path = DEFAULT_STORE,
    *,
    schema_path: Path = DEFAULT_STORE_SCHEMA,
) -> dict[str, Any]:
    manifest_path = _manifest_path(Path(path))
    manifest, _ = _read_canonical_json(manifest_path, "store manifest")
    validator = _schema_validator(schema_path)
    _validate_with_schema(manifest, validator, "store manifest")
    if manifest["manifest_sha256"] != manifest_sha256(manifest):
        raise DocumentNodeStoreError("Store manifest_sha256 is invalid")

    entries = manifest["shards"]
    ids = tuple(entry["document_id"] for entry in entries)
    ordinals = tuple(entry["ordinal"] for entry in entries)
    paths = tuple(entry["path"] for entry in entries)
    expected_paths = tuple(
        shard_filename(document_id) for document_id in OFFICIAL_DOCUMENT_IDS
    )
    if ids != OFFICIAL_DOCUMENT_IDS or ordinals != tuple(range(1, 12)):
        raise DocumentNodeStoreError("Manifest shards are not in canonical official-document order")
    if paths != expected_paths or len(set(paths)) != len(paths):
        raise DocumentNodeStoreError("Manifest shard paths are not canonical and unique")
    if manifest["document_count"] != len(entries):
        raise DocumentNodeStoreError("Manifest document_count does not equal its shard count")
    return manifest


def _safe_shard_path(store_dir: Path, relative_path: str) -> Path:
    candidate = store_dir / relative_path
    try:
        resolved_store = store_dir.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
    except OSError as error:
        raise DocumentNodeStoreError(f"Cannot resolve shard path {candidate}: {error}") from error
    if resolved_candidate.parent != resolved_store or not resolved_candidate.is_file():
        raise DocumentNodeStoreError(
            f"Shard path escapes the store or is not a file: {relative_path}"
        )
    return resolved_candidate


def load_store(
    path: Path = DEFAULT_STORE,
    *,
    store_schema_path: Path = DEFAULT_STORE_SCHEMA,
    document_schema_path: Path = DEFAULT_DOCUMENT_SCHEMA,
    validate_document_schema: bool = False,
) -> dict[str, Any]:
    """Load, verify, and transparently reconstruct a sharded document-node corpus."""

    manifest_path = _manifest_path(Path(path))
    manifest = load_manifest(manifest_path, schema_path=store_schema_path)
    store_dir = manifest_path.parent
    document_validator = (
        _schema_validator(document_schema_path, document=True)
        if validate_document_schema
        else None
    )
    documents: list[dict[str, Any]] = []

    for entry in manifest["shards"]:
        shard_path = _safe_shard_path(store_dir, entry["path"])
        document, raw = _read_canonical_json(shard_path, f"shard {entry['document_id']}")
        if len(raw) >= MAX_SHARD_BYTES:
            raise DocumentNodeStoreError(f"Shard {entry['document_id']} is not GitHub-safe")
        if len(raw) != entry["byte_count"]:
            raise DocumentNodeStoreError(f"Shard {entry['document_id']} byte_count is invalid")
        if sha256_bytes(raw) != entry["sha256"]:
            raise DocumentNodeStoreError(f"Shard {entry['document_id']} sha256 is invalid")
        _assert_document_invariants(document, entry["document_id"])
        if document_validator is not None:
            _validate_with_schema(
                document, document_validator, f"shard {entry['document_id']}"
            )
        expected_entry = _document_metadata(document, raw, entry["ordinal"])
        if entry != expected_entry:
            raise DocumentNodeStoreError(
                f"Shard {entry['document_id']} document counters or identity are invalid"
            )
        documents.append(document)

    corpus = {**manifest["corpus"], "documents": documents}
    _assert_corpus_shape(corpus)
    _assert_corpus_invariants(corpus)
    reconstructed = canonical_json_bytes(corpus)
    if len(reconstructed) != manifest["reconstructed_byte_count"]:
        raise DocumentNodeStoreError("Canonical reconstruction byte count is invalid")
    if sha256_bytes(reconstructed) != manifest["reconstructed_sha256"]:
        raise DocumentNodeStoreError("Canonical reconstruction SHA-256 is invalid")
    return corpus


def reconstruct_store(
    path: Path = DEFAULT_STORE,
    output_path: Path | None = None,
    *,
    store_schema_path: Path = DEFAULT_STORE_SCHEMA,
    document_schema_path: Path = DEFAULT_DOCUMENT_SCHEMA,
    validate_document_schema: bool = False,
) -> bytes:
    raw = canonical_json_bytes(
        load_store(
            path,
            store_schema_path=store_schema_path,
            document_schema_path=document_schema_path,
            validate_document_schema=validate_document_schema,
        )
    )
    if output_path is not None:
        _write_bytes(Path(output_path), raw)
    return raw


def verify_store(path: Path = DEFAULT_STORE) -> dict[str, Any]:
    load_store(path)
    return load_manifest(path)


def load_document_nodes(path: Path = DEFAULT_SOURCE) -> dict[str, Any]:
    """Load either the canonical monolith or a shard store through one API."""

    path = Path(path)
    if path.is_dir() or path.name == MANIFEST_NAME:
        return load_store(path)
    value, _ = _read_canonical_json(path, "document-node corpus")
    _assert_corpus_shape(value)
    _assert_corpus_invariants(value)
    return value


def hash_document_nodes(path: Path = DEFAULT_SOURCE) -> str:
    return sha256_bytes(canonical_json_bytes(load_document_nodes(path)))


def hash_store(path: Path = DEFAULT_STORE) -> str:
    return hash_document_nodes(path)


# Descriptive aliases for callers that prefer corpus-specific names.
build_document_node_store = generate_store
load_document_node_store = load_store
reconstruct_document_node_corpus = reconstruct_store


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and verify the GitHub-safe Blue Book document-node shard store."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate deterministic shards")
    generate.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    generate.add_argument("--store", type=Path, default=DEFAULT_STORE)
    generate.add_argument("--schema", type=Path, default=DEFAULT_STORE_SCHEMA)
    generate.add_argument("--document-schema", type=Path, default=DEFAULT_DOCUMENT_SCHEMA)
    generate.add_argument(
        "--validate-document-schema",
        action="store_true",
        help="Also run the expensive pre-existing deep document schema",
    )

    verify = subparsers.add_parser("verify", help="Verify every store invariant")
    verify.add_argument("store", nargs="?", type=Path, default=DEFAULT_STORE)

    reconstruct = subparsers.add_parser("reconstruct", help="Reconstruct canonical monolith")
    reconstruct.add_argument("store", nargs="?", type=Path, default=DEFAULT_STORE)
    reconstruct.add_argument("--out", required=True, type=Path)

    digest = subparsers.add_parser("hash", help="Print canonical reconstruction SHA-256")
    digest.add_argument("store", nargs="?", type=Path, default=DEFAULT_STORE)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "generate":
            manifest = generate_store(
                args.source,
                args.store,
                store_schema_path=args.schema,
                document_schema_path=args.document_schema,
                validate_document_schema=args.validate_document_schema,
            )
            print(
                f"Wrote {manifest['document_count']} canonical shards to {args.store}; "
                f"reconstruction SHA-256 {manifest['reconstructed_sha256']}"
            )
        elif args.command == "verify":
            manifest = verify_store(args.store)
            print(
                f"Verified {manifest['document_count']} shards; "
                f"reconstruction SHA-256 {manifest['reconstructed_sha256']}"
            )
        elif args.command == "reconstruct":
            raw = reconstruct_store(args.store, args.out)
            print(f"Wrote {len(raw)} canonical bytes to {args.out}")
        else:
            print(hash_store(args.store))
    except (DocumentNodeStoreError, OSError) as error:
        print(f"document-node store error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
