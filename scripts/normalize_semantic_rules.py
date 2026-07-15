from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
IN_PATH = ROOT / "data" / "bluebook_semantic_rules.json"
OUT_PATH = ROOT / "data" / "bluebook_semantic_rules.normalized.json"
SCHEMA_OUT = ROOT / "data" / "normalized_semantic_rule_schema.json"

BLUEBOOK_PDF = "https://iupac.qmul.ac.uk/BlueBook/PDF/BlueBookV3.pdf"
BLUEBOOK_HOME = "https://iupac.qmul.ac.uk/BlueBook/"
POST_V3_CORRECTIONS = "https://iupac.qmul.ac.uk/BlueBook/changes2.html"

REF_RE = re.compile(r"\bP-\d+(?:\.\d+)*\b")
RANGE_RE = re.compile(r"\b(P-\d+(?:\.\d+)*)\s+(?:through|to|-)\s+(P-\d+(?:\.\d+)*)\b", re.I)


REQUIREMENT_MAP = (
    (
        ("cross_referenced", "referenced_rules"),
        "dependency_graph_linked",
        "Cross-referenced Blue Book rules are represented as dependency edges.",
    ),
    (
        ("source examples", "regression tests", "examples should"),
        "example_regression_cases_required",
        "Source examples are preserved for conversion into conformance tests.",
    ),
    (
        ("preference_order", "seniority ordering", "seniority order"),
        "global_preference_comparator_required",
        "Preference wording is represented as ordered comparators that must share one global ranking service.",
    ),
    (
        ("graph predicate", "graph feature", "structural match", "candidate enumeration"),
        "molecular_graph_predicate_required",
        "Rule execution requires a molecular graph feature or predicate implementation.",
    ),
    (
        ("stereochemistry", "stereochemical", "CIP", "descriptor"),
        "stereochemistry_perception_required",
        "Rule execution requires stereochemical perception and descriptor assignment.",
    ),
    (
        ("table", "retained-name", "retained_name", "allowlist"),
        "source_table_required",
        "Rule execution requires a structured source table or allowlist.",
    ),
    (
        ("long_source_record", "embedded_subrules", "split"),
        "subrule_partition_recommended",
        "The source section contains multiple clauses; normalized predicates keep the section together and mark split points for later compilation.",
    ),
    (
        ("human_judgment", "human_policy", "formal_threshold"),
        "formal_policy_threshold_required",
        "Human-facing policy wording is represented as an explicit threshold or allowlist requirement.",
    ),
    (
        ("source extraction", "verify boundaries", "extracted logic"),
        "source_boundary_review_recommended",
        "The extracted source boundary is preserved and should be checked before executable compilation.",
    ),
)


def main() -> int:
    payload = json.loads(IN_PATH.read_text(encoding="utf-8-sig"))
    records = payload.get("records", [])
    seen_ids: Counter[str] = Counter()
    normalized_records: list[dict[str, Any]] = []
    requirement_counts: Counter[str] = Counter()

    for index, record in enumerate(records, start=1):
        rule_id = str(record["rule_id"])
        seen_ids[rule_id] += 1
        normalized = normalize_record(record, index, seen_ids[rule_id])
        for requirement in normalized["implementation_requirements"]:
            requirement_counts[requirement["kind"]] += 1
        normalized_records.append(normalized)

    result = {
        "source": BLUEBOOK_PDF,
        "source_homepage": BLUEBOOK_HOME,
        "post_v3_corrections": POST_V3_CORRECTIONS,
        "source_version": "IUPAC Blue Book Version 3 PDF plus post-V3 web corrections",
        "conversion_status": "normalized_semantic",
        "record_count": len(normalized_records),
        "duplicate_rule_id_count": sum(1 for count in seen_ids.values() if count > 1),
        "implementation_requirement_counts": dict(sorted(requirement_counts.items())),
        "normalization_notes": [
            "Draft implementation notes were converted into explicit implementation_requirements.",
            "Rule strings were normalized into stable predicate/action tokens while preserving source_quote.",
            "Dependency context was expanded for explicit references, hierarchy, chapter scope, exceptions, examples, and source tables.",
        ],
        "records": normalized_records,
    }
    OUT_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    SCHEMA_OUT.write_text(json.dumps(normalized_schema(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(normalized_records)} normalized semantic records to {OUT_PATH}")
    print(f"Wrote normalized schema to {SCHEMA_OUT}")
    return 0


def normalize_record(record: dict[str, Any], record_index: int, duplicate_index: int) -> dict[str, Any]:
    rule_id = str(record["rule_id"])
    text_fields = collect_text_fields(record)
    explicit_refs = sorted({ref for text in text_fields for ref in REF_RE.findall(text) if ref != rule_id})
    range_refs = sorted({match.group(0) for text in text_fields for match in RANGE_RE.finditer(text)})
    hierarchical_refs = hierarchy_for(rule_id)
    source_table_refs = sorted(item for item in record.get("tables_or_terms", []) if "table" in item.lower())
    exception_refs = sorted(
        {
            ref
            for exception in record.get("exceptions", [])
            for text in (str(exception.get("condition", "")), str(exception.get("effect", "")))
            for ref in REF_RE.findall(text)
            if ref != rule_id
        }
    )
    example_refs = sorted({ref for item in record.get("examples", []) for ref in REF_RE.findall(str(item)) if ref != rule_id})

    implementation_requirements = requirements_from(record.get("implementation_requirements", []))
    implementation_status = "specified"
    if any(req["kind"].endswith("_required") for req in implementation_requirements):
        implementation_status = "specified_with_external_requirements"
    elif implementation_requirements:
        implementation_status = "specified_with_review_recommendations"

    return {
        "normalized_id": f"{rule_id}#{duplicate_index}" if duplicate_index > 1 else rule_id,
        "record_index": record_index,
        "rule_id": rule_id,
        "rule_instance": duplicate_index,
        "title": clean_text(record.get("title", "")),
        "source_chapter": record.get("source_chapter", ""),
        "source_url": record.get("source_url", ""),
        "source_pdf_url": BLUEBOOK_PDF,
        "rule_type": record.get("rule_type", "other"),
        "implementation_status": implementation_status,
        "logic": {
            "applies_if": normalize_list(record.get("applies_if", [])),
            "unless": normalize_list(record.get("unless", [])),
            "then": normalize_list(record.get("then", [])),
            "prefer": normalize_list(record.get("prefer", [])),
            "reject": normalize_list(record.get("reject", [])),
        },
        "compare_by": normalize_compare_by(record.get("compare_by", [])),
        "exceptions": normalize_exceptions(record.get("exceptions", [])),
        "dependency_context": {
            "declared": sorted(set(record.get("depends_on", []))),
            "explicit_references": explicit_refs,
            "range_references": range_refs,
            "hierarchy": hierarchical_refs,
            "exception_references": exception_refs,
            "example_references": example_refs,
            "source_table_references": source_table_refs,
        },
        "implementation_requirements": implementation_requirements,
        "examples": [clean_text(item) for item in record.get("examples", [])],
        "tables_or_terms": [clean_text(item) for item in record.get("tables_or_terms", [])],
        "source_quote": clean_text(record.get("source_quote", "")),
    }


def collect_text_fields(record: dict[str, Any]) -> list[str]:
    values: list[str] = [str(record.get("title", "")), str(record.get("source_quote", ""))]
    for field in ("applies_if", "unless", "then", "prefer", "reject", "depends_on", "examples", "tables_or_terms"):
        values.extend(str(item) for item in record.get(field, []))
    for exception in record.get("exceptions", []):
        values.append(str(exception.get("condition", "")))
        values.append(str(exception.get("effect", "")))
    return values


def requirements_from(notes: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    details: dict[str, str] = {}
    for note in notes:
        if isinstance(note, dict):
            kind = str(note.get("kind", "semantic_compilation_requirement"))
            details[kind] = str(note.get("resolution", "Explicit implementation requirement."))
            for source_note in note.get("source_notes", []):
                grouped[kind].add(clean_text(source_note))
            if not grouped[kind]:
                grouped[kind].add(kind)
            continue
        kind, resolution = classify_requirement(str(note))
        grouped[kind].add(clean_text(note))
        details[kind] = resolution
    return [
        {
            "kind": kind,
            "resolution": details[kind],
            "source_notes": sorted(source_notes),
        }
        for kind, source_notes in sorted(grouped.items())
    ]


def classify_requirement(note: str) -> tuple[str, str]:
    lowered = note.lower()
    for needles, kind, resolution in REQUIREMENT_MAP:
        if any(needle.lower() in lowered for needle in needles):
            return kind, resolution
    return (
        "semantic_compilation_requirement",
        "The draft note is represented as an explicit compilation requirement.",
    )


def normalize_list(values: list[Any]) -> list[str]:
    return [normalize_token(str(value)) for value in values if normalize_token(str(value))]


def normalize_compare_by(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for index, item in enumerate(values, start=1):
        normalized.append(
            {
                "priority": int(item.get("priority") or index),
                "criterion": normalize_token(str(item.get("criterion", ""))),
                "direction": normalize_token(str(item.get("direction", "not_applicable"))),
            }
        )
    return normalized


def normalize_exceptions(values: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "condition": normalize_token(str(item.get("condition", ""))),
            "effect": normalize_token(str(item.get("effect", ""))),
        }
        for item in values
        if normalize_token(str(item.get("condition", ""))) or normalize_token(str(item.get("effect", "")))
    ]


def hierarchy_for(rule_id: str) -> list[str]:
    parts = rule_id.split(".")
    if len(parts) == 1:
        return []
    return [".".join(parts[:idx]) for idx in range(1, len(parts))]


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_token(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r"^(?:source_(?:condition|requirement|preference|prohibition)_)", "", value)
    value = re.sub(r"^(?:predicate|action|apply_rule_text|apply_rule_text_as_semantic_constraint):?", "", value)
    value = value.replace("P-", "P_")
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_").lower()


def normalized_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Normalized semantic Blue Book rule records",
        "type": "object",
        "required": ["source", "source_version", "conversion_status", "records"],
        "properties": {
            "source": {"type": "string"},
            "source_homepage": {"type": "string"},
            "post_v3_corrections": {"type": "string"},
            "source_version": {"type": "string"},
            "conversion_status": {"const": "normalized_semantic"},
            "records": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "normalized_id",
                        "record_index",
                        "rule_id",
                        "rule_instance",
                        "title",
                        "source_chapter",
                        "source_pdf_url",
                        "rule_type",
                        "implementation_status",
                        "logic",
                        "compare_by",
                        "exceptions",
                        "dependency_context",
                        "implementation_requirements",
                        "examples",
                        "tables_or_terms",
                        "source_quote",
                    ],
                },
            },
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
