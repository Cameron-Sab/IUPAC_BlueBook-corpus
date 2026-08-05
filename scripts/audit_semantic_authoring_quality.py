from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.build_compact_semantic_tasks import (
        DEFAULT_OUTPUT as DEFAULT_TASK_DIR,
        load_json,
    )
else:
    from build_compact_semantic_tasks import (
        DEFAULT_OUTPUT as DEFAULT_TASK_DIR,
        load_json,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTHORING_DIR = ROOT / "data" / "bluebook_v3" / "semantic_authoring"
SAFE_NONOPERATIVE_REASONS = {
    "citation_only",
    "example_label",
    "heading_or_title",
    "historical_context",
    "rationale",
    "source_navigation",
}
NORMATIVE_TEXT_RE = re.compile(
    r"\b(?:"
    r"must|shall|should|may|recommended|recommendation|preferred\s+IUPAC\s+name|PIN|"
    r"permitted|prohibited|not\s+allowed|not\s+used|no\s+longer|without\s+restriction|"
    r"limited|retained|substitutab(?:le|ility)|is\s+used|are\s+used|is\s+chosen|"
    r"are\s+chosen|priority|preference\s+is\s+given|seniority"
    r")\b",
    re.IGNORECASE,
)


def _ordered_source_units(task: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    return [
        (str(rule["rule_id"]), unit)
        for rule in task["rules"]
        for unit in rule["source_units"]
    ]


def quality_findings(
    authoring: Mapping[str, Any], task: Mapping[str, Any]
) -> list[dict[str, Any]]:
    ordered = _ordered_source_units(task)
    clauses = authoring.get("clauses")
    if not isinstance(clauses, list) or len(clauses) != len(ordered):
        raise ValueError("Authoring clause slots do not match the task")

    candidates: list[dict[str, Any]] = []
    flagged_rules: set[str] = set()
    for index, ((rule_id, unit), decision) in enumerate(zip(ordered, clauses), 1):
        if not isinstance(decision, list) or len(decision) != 4:
            continue
        role, force, disposition, reason = decision
        if disposition != "skip" or reason in SAFE_NONOPERATIVE_REASONS:
            continue
        text = unit.get("text") or ""
        signals = []
        if unit.get("semantic_cue") is not None:
            signals.append("semantic_cue")
        if NORMATIVE_TEXT_RE.search(text):
            signals.append("normative_language")
        finding = {
            "task_id": task["task_id"],
            "rule_id": rule_id,
            "clause_index": index,
            "clause_id": unit["clause_id"],
            "role": role,
            "force": force,
            "reason_code": reason,
            "signals": signals,
            "text": text,
        }
        candidates.append(finding)
        if signals:
            flagged_rules.add(rule_id)

    findings = []
    for finding in candidates:
        if not finding["signals"] and finding["rule_id"] in flagged_rules:
            finding["signals"] = ["same_rule_as_normative_skip"]
        if finding["signals"]:
            findings.append(finding)
    return findings


def audit_paths(
    paths: Sequence[Path], *, task_dir: Path = DEFAULT_TASK_DIR
) -> dict[str, Any]:
    findings = []
    task_ids = []
    for path in paths:
        authoring = load_json(path)
        task_id = authoring["task_id"]
        task = load_json(task_dir / f"{task_id}.json")
        task_ids.append(task_id)
        findings.extend(quality_findings(authoring, task))
    return {
        "format": "iupac-bluebook-semantic-authoring-quality-audit",
        "format_version": "1.0.0",
        "passed": not findings,
        "task_count": len(task_ids),
        "task_ids": task_ids,
        "finding_count": len(findings),
        "findings": findings,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flag suspicious nonoperative semantic-authoring decisions"
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--authoring-dir", type=Path, default=DEFAULT_AUTHORING_DIR)
    parser.add_argument("--task-dir", type=Path, default=DEFAULT_TASK_DIR)
    parser.add_argument("--require-clean", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = args.paths or sorted(args.authoring_dir.glob("*.json"))
    try:
        report = audit_paths(paths, task_dir=args.task_dir)
    except (KeyError, TypeError, ValueError, OSError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if args.require_clean and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
