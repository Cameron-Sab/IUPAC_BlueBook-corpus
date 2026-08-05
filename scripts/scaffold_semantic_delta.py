from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.build_compact_semantic_tasks import canonical_json_bytes, load_json
    from scripts.compile_semantic_delta import finalize_delta, validate_delta_schema
    from scripts.render_compact_semantic_task import validate_task
else:
    from build_compact_semantic_tasks import canonical_json_bytes, load_json
    from compile_semantic_delta import finalize_delta, validate_delta_schema
    from render_compact_semantic_task import validate_task


NAVIGATION_LINE_RE = re.compile(
    r"^P-[0-9]+(?:\.[0-9]+)*(?:\([a-z0-9]+\))?(?:\s+.+)?$"
)
HEADING_LINE_RE = re.compile(
    r"^P-[0-9]+(?:\.[0-9]+)*(?:\([a-z0-9]+\))?(?:\s+(?P<label>.*))?$"
)
FORBIDDEN_OUTPUT_RE = re.compile(
    r"\b(?:todo|unresolved|placeholder|not_started|manual_review)\b",
    re.IGNORECASE,
)


def _is_mechanical_heading(text: str | None) -> bool:
    """Return true only when heading text is provably nonoperative."""
    match = HEADING_LINE_RE.fullmatch((text or "").strip())
    if match is None:
        return False
    label = (match.group("label") or "").strip()
    return not label or re.search(r"[a-z]", label) is None


def _nonoperative_disposition(unit: Mapping[str, Any]) -> dict[str, Any] | None:
    if unit["semantic_cue"] is not None or unit["ancestor_node_kinds"]:
        return None

    unit_kind = unit["unit_kind"]
    node_kind = unit["node_kind"]
    if (
        unit_kind == "heading_text"
        and node_kind == "heading"
        and _is_mechanical_heading(unit.get("text"))
    ):
        return {
            "clause_id": unit["clause_id"],
            "role": "heading",
            "force": "informative",
            "disposition": {
                "kind": "nonoperative",
                "reason_code": "heading_or_title",
            },
        }
    if unit_kind == "example_label" and node_kind == "example_block":
        return {
            "clause_id": unit["clause_id"],
            "role": "example",
            "force": "illustrative",
            "disposition": {
                "kind": "nonoperative",
                "reason_code": "example_label",
            },
        }
    if unit_kind == "prose_text" and node_kind == "prose":
        lines = [line.strip() for line in (unit["text"] or "").splitlines() if line.strip()]
        if lines and all(NAVIGATION_LINE_RE.fullmatch(line) for line in lines):
            return {
                "clause_id": unit["clause_id"],
                "role": "source_metadata",
                "force": "source_metadata",
                "disposition": {
                    "kind": "nonoperative",
                    "reason_code": "source_navigation",
                },
            }
    return None


def _literal_rule_reference_pattern(reference_text: str) -> re.Pattern[str] | None:
    value = reference_text.strip()
    if not re.fullmatch(r"P-[0-9]+(?:\.[0-9]+)*(?:\([a-z0-9]+\))?", value):
        return None
    return re.compile(rf"(?<![A-Za-z0-9.]){re.escape(value)}(?![A-Za-z0-9.])")


def _citation_clause(
    rule: Mapping[str, Any], reference: Mapping[str, Any]
) -> str | None:
    pattern = _literal_rule_reference_pattern(reference["reference_text"])
    if pattern is None:
        return None
    matching_clauses = [
        unit["clause_id"]
        for unit in rule["source_units"]
        if isinstance(unit["text"], str) and pattern.search(unit["text"])
    ]
    if len(matching_clauses) != 1:
        return None
    return matching_clauses[0]


def _citation_bindings(task: Mapping[str, Any]) -> list[dict[str, Any]]:
    bindings: list[dict[str, Any]] = []
    for rule in task["rules"]:
        for reference in rule["references"]:
            if reference["effective_target_kind"] == "external_or_historical":
                continue
            clause_id = _citation_clause(rule, reference)
            if clause_id is None:
                continue
            occurrence_id = reference["occurrence_id"]
            bindings.append(
                {
                    "reference_id": "reference."
                    + re.sub(r"[^A-Za-z0-9_.:-]+", "_", occurrence_id),
                    "clause_ids": [clause_id],
                    "relation": "cites",
                    "occurrence_ids": [occurrence_id],
                    "resolution": "exact",
                    "ordered_member_refs": [],
                }
            )
    return bindings


def scaffold_delta(task: dict[str, Any]) -> dict[str, Any]:
    validate_task(task)
    dispositions = [
        disposition
        for rule in task["rules"]
        for unit in rule["source_units"]
        if (disposition := _nonoperative_disposition(unit)) is not None
    ]
    delta: dict[str, Any] = {
        "symbol_declarations": [],
        "clause_dispositions": dispositions,
        "semantic_units": [],
        "exceptions": [],
        "tables": [],
        "figures": [],
        "examples": [],
        "correction_applications": [],
        "citation_bindings": _citation_bindings(task),
        "additional_references": [],
    }
    finalize_delta(delta, task)
    validate_delta_schema(delta)
    rendered = canonical_json_bytes(delta)
    if FORBIDDEN_OUTPUT_RE.search(rendered.decode("utf-8")):
        raise ValueError("Scaffold contains a forbidden authoring marker")
    return delta


def scaffold_metrics(task: Mapping[str, Any], delta: Mapping[str, Any]) -> dict[str, int]:
    clause_count = sum(len(rule["source_units"]) for rule in task["rules"])
    citation_count = sum(len(rule["references"]) for rule in task["rules"])
    avoided_bytes = len(canonical_json_bytes(delta))
    return {
        "clause_count": clause_count,
        "prefilled_clause_count": len(delta["clause_dispositions"]),
        "remaining_clause_count": clause_count - len(delta["clause_dispositions"]),
        "citation_count": citation_count,
        "prefilled_citation_count": len(delta["citation_bindings"]),
        "remaining_citation_count": citation_count - len(delta["citation_bindings"]),
        "mechanical_output_bytes_avoided": avoided_bytes,
        "mechanical_output_tokens_approx": (avoided_bytes + 3) // 4,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a source-bound mechanical semantic-delta draft"
    )
    parser.add_argument("task", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--metrics", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    task = load_json(args.task)
    delta = scaffold_delta(task)
    rendered = canonical_json_bytes(delta)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(rendered)
    else:
        sys.stdout.buffer.write(rendered)
    if args.metrics:
        stream = sys.stdout if args.output else sys.stderr
        print(json.dumps(scaffold_metrics(task, delta), sort_keys=True), file=stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
