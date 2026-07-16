from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "bluebook_v3"
DEFAULT_REFERENCES = BASE / "bluebook_v3_reference_occurrences.json"
DEFAULT_SOURCE = BASE / "bluebook_v3_source_corpus.json"
DEFAULT_CORRECTIONS = BASE / "bluebook_v3_correction_overlays.json"
DEFAULT_SCHEMA = ROOT / "data" / "bluebook_reference_resolutions.schema.json"
DEFAULT_OUTPUT = BASE / "bluebook_v3_reference_resolutions.json"

DECLARED_RESOLUTIONS: tuple[dict[str, Any], ...] = (
    {
        "occurrence_id": "P-16.2.4.1:xref:0005",
        "nominal_rule_id": "P-66.1.2.1",
        "resolution_kind": "source_alias",
        "resolved_rule_id": "P-66.1.2",
        "rationale_code": "nonexistent_subrule_resolved_to_exact_active_parent",
        "correction_overlay_id": None,
    },
    {
        "occurrence_id": "P-65.7:xref:0009",
        "nominal_rule_id": "P-65.7.8",
        "resolution_kind": "historical_deleted_rule",
        "resolved_rule_id": "P-65.7.8",
        "rationale_code": "deleted_by_official_correction",
        "correction_overlay_id": "BBV3-CORR-707A0F8B4E94258D",
    },
    {
        "occurrence_id": "P-66.2.1:xref:0001",
        "nominal_rule_id": "P-66.1.2.1",
        "resolution_kind": "source_alias",
        "resolved_rule_id": "P-66.1.2",
        "rationale_code": "nonexistent_subrule_resolved_to_exact_active_parent",
        "correction_overlay_id": None,
    },
)


class ResolutionError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _correction_targets(record: Mapping[str, Any]) -> set[str]:
    return {
        selector["rule_id"]
        for selector in record.get("target", {}).get("selectors", [])
        if selector.get("kind") == "rule" and isinstance(selector.get("rule_id"), str)
    }


def build_reference_resolutions(
    references: Mapping[str, Any],
    source: Mapping[str, Any],
    corrections: Mapping[str, Any],
    *,
    references_sha256: str,
    source_sha256: str,
    corrections_sha256: str,
) -> dict[str, Any]:
    occurrences = {
        item["occurrence_id"]: item for item in references.get("occurrences", [])
    }
    unresolved_ids = {
        item["occurrence_id"]
        for item in occurrences.values()
        if item.get("target", {}).get("resolution") == "unresolved"
    }
    declared_ids = {item["occurrence_id"] for item in DECLARED_RESOLUTIONS}
    if unresolved_ids != declared_ids:
        raise ResolutionError(
            "Declared reference resolutions do not exactly cover raw unresolved "
            f"occurrences: missing={sorted(unresolved_ids - declared_ids)}, "
            f"extra={sorted(declared_ids - unresolved_ids)}"
        )

    active_rule_ids = {
        record["source_rule_id"] for record in source.get("records", [])
    }
    correction_by_id = {
        record["overlay_id"]: record for record in corrections.get("records", [])
    }
    records: list[dict[str, Any]] = []
    for ordinal, declaration in enumerate(DECLARED_RESOLUTIONS, start=1):
        occurrence = occurrences[declaration["occurrence_id"]]
        nominal_rule_id = declaration["nominal_rule_id"]
        if occurrence.get("cited_rule_id") != nominal_rule_id:
            raise ResolutionError(
                f"{declaration['occurrence_id']} no longer cites {nominal_rule_id}"
            )
        if occurrence.get("target", {}).get("rule_id") != nominal_rule_id:
            raise ResolutionError(
                f"{declaration['occurrence_id']} raw target no longer matches its citation"
            )

        resolution_kind = declaration["resolution_kind"]
        resolved_rule_id = declaration["resolved_rule_id"]
        overlay_id = declaration["correction_overlay_id"]
        if resolution_kind == "source_alias":
            if nominal_rule_id in active_rule_ids or resolved_rule_id not in active_rule_ids:
                raise ResolutionError(
                    f"Invalid explicit alias {nominal_rule_id} -> {resolved_rule_id}"
                )
            if overlay_id is not None:
                raise ResolutionError("Source aliases cannot cite deletion overlays")
        elif resolution_kind == "historical_deleted_rule":
            overlay = correction_by_id.get(overlay_id)
            if (
                overlay is None
                or overlay.get("status") != "deleted"
                or resolved_rule_id not in _correction_targets(overlay)
            ):
                raise ResolutionError(
                    f"Historical target {resolved_rule_id} lacks its declared deletion overlay"
                )
            if resolved_rule_id in active_rule_ids:
                raise ResolutionError(
                    f"Historical deleted target {resolved_rule_id} is still active"
                )
        else:
            raise ResolutionError(f"Unknown resolution kind: {resolution_kind}")

        record = {
            "resolution_id": f"BBV3-XREF-RES-{ordinal:04d}",
            "occurrence_id": declaration["occurrence_id"],
            "nominal_rule_id": nominal_rule_id,
            "resolution_kind": resolution_kind,
            "resolved_rule_id": resolved_rule_id,
            "rationale_code": declaration["rationale_code"],
            "correction_overlay_id": overlay_id,
            "reference_occurrence_sha256": sha256_bytes(
                canonical_json_bytes(occurrence)
            ),
        }
        record["record_sha256"] = sha256_bytes(canonical_json_bytes(record))
        records.append(record)

    kind_counts = Counter(record["resolution_kind"] for record in records)
    counters = {
        "raw_unresolved_occurrence_count": len(unresolved_ids),
        "resolution_record_count": len(records),
        "resolution_kind_counts": {
            "source_alias": kind_counts["source_alias"],
            "historical_deleted_rule": kind_counts["historical_deleted_rule"],
        },
        "remaining_unresolved_occurrence_count": len(unresolved_ids - declared_ids),
    }
    payload = {
        "format": "iupac-bluebook-reference-resolutions",
        "format_version": "1.0.0",
        "reference_occurrences_sha256": references_sha256,
        "source_corpus_sha256": source_sha256,
        "correction_overlays_sha256": corrections_sha256,
        "policy": "exact_occurrence_only_no_generic_parent_fallback",
        "counters": counters,
        "records": records,
    }
    payload["corpus_sha256"] = sha256_bytes(canonical_json_bytes(payload))
    return payload


def validate_schema(corpus: Mapping[str, Any], schema_path: Path = DEFAULT_SCHEMA) -> None:
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(corpus), key=lambda error: list(error.absolute_path)
    )
    if errors:
        details = "\n".join(
            f"- /{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in errors[:25]
        )
        raise ResolutionError(f"Reference resolution schema failed:\n{details}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build explicit overlays for every unresolved raw Blue Book reference"
    )
    parser.add_argument("--references", type=Path, default=DEFAULT_REFERENCES)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--corrections", type=Path, default=DEFAULT_CORRECTIONS)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    reference_bytes = args.references.read_bytes()
    source_bytes = args.source.read_bytes()
    correction_bytes = args.corrections.read_bytes()
    corpus = build_reference_resolutions(
        json.loads(reference_bytes),
        json.loads(source_bytes),
        json.loads(correction_bytes),
        references_sha256=sha256_bytes(reference_bytes),
        source_sha256=sha256_bytes(source_bytes),
        corrections_sha256=sha256_bytes(correction_bytes),
    )
    validate_schema(corpus, args.schema)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(canonical_json_bytes(corpus))
    print(json.dumps(corpus["counters"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
