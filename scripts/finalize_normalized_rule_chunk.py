from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from scripts import validate_normalized_rule_chunks as chunk_validator


def finalize_chunk(chunk: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    chunk["format"] = "iupac-bluebook-normalized-rule-chunk"
    chunk["format_version"] = "1.0.0"
    chunk["packet_id"] = packet["packet_id"]
    chunk["packet_sha256"] = packet["packet_sha256"]
    chunk["schema_sha256"] = chunk_validator.language_schema_sha256()
    for field in chunk_validator.SOURCE_HASH_FIELDS:
        chunk[field] = packet[field]
    chunk["assigned_rule_ids"] = packet["assigned_rule_ids"]
    chunk["chunk_metrics"] = chunk_validator._expected_metrics(chunk)
    chunk["chunk_sha256"] = chunk_validator.digest_without_field(
        chunk, "chunk_sha256"
    )
    return chunk


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finalize and validate one normalized semantic conversion chunk"
    )
    parser.add_argument("chunk", type=Path)
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate the finalized in-memory form without rewriting the chunk",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    chunk = chunk_validator.load_json(args.chunk)
    packet = chunk_validator.load_json(args.packet)
    finalized = finalize_chunk(chunk, packet)
    finalized_bytes = chunk_validator.canonical_json_bytes(finalized)
    result = chunk_validator.validate_chunk(
        finalized,
        packet,
        chunk_bytes=finalized_bytes,
        packet_bytes=args.packet.read_bytes(),
    )
    if result["passed"] and not args.check_only:
        args.chunk.write_bytes(finalized_bytes)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
