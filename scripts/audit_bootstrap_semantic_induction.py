from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.bootstrap_semantic_authoring import (
        DEFAULT_TASK_DIR,
        DEFAULT_TRAINING_DIR,
        DecisionClassifier,
        _ordered_units,
        classify_native_kind,
        classify_clause,
    )
    from scripts.build_compact_semantic_tasks import load_json
else:
    from bootstrap_semantic_authoring import (
        DEFAULT_TASK_DIR,
        DEFAULT_TRAINING_DIR,
        DecisionClassifier,
        _ordered_units,
        classify_native_kind,
        classify_clause,
    )
    from build_compact_semantic_tasks import load_json


def _fold(task_id: str, fold_count: int) -> int:
    digest = hashlib.sha256(task_id.encode("ascii")).digest()
    return int.from_bytes(digest[:4], "big") % fold_count


def audit_induction(
    training_dir: Path = DEFAULT_TRAINING_DIR,
    task_dir: Path = DEFAULT_TASK_DIR,
    *,
    fold_count: int = 5,
) -> dict[str, Any]:
    tasks: dict[
        str,
        tuple[list[Mapping[str, Any]], list[Any], dict[int, set[str]]],
    ] = {}
    for authoring_path in sorted(training_dir.glob("P-*-part-*.json")):
        task_path = task_dir / authoring_path.name
        if not task_path.exists():
            continue
        authoring = load_json(authoring_path)
        task = load_json(task_path)
        units = _ordered_units(task)
        decisions = authoring.get("clauses", [])
        if len(units) == len(decisions):
            owner_kinds: dict[int, set[str]] = defaultdict(set)
            for semantic_unit in authoring.get("units", []):
                kind = semantic_unit.get("k")
                if not isinstance(kind, str):
                    continue
                for index in semantic_unit.get("c", []):
                    if isinstance(index, int):
                        owner_kinds[index].add(kind)
            tasks[str(task["task_id"])] = (units, decisions, owner_kinds)

    totals = Counter()
    role_confusion: dict[str, Counter[str]] = defaultdict(Counter)
    owner_kind_confusion: dict[str, Counter[str]] = defaultdict(Counter)
    confidence_buckets: dict[str, Counter[str]] = defaultdict(Counter)
    for fold in range(fold_count):
        classifier = DecisionClassifier()
        for task_id, (units, decisions, _) in tasks.items():
            if _fold(task_id, fold_count) == fold:
                continue
            for unit, decision in zip(units, decisions):
                classifier.add(unit, decision)
            for index, kinds in tasks[task_id][2].items():
                for kind in kinds:
                    classifier.add_kind(units[index - 1], kind)
        for task_id, (units, decisions, owner_kinds) in tasks.items():
            if _fold(task_id, fold_count) != fold:
                continue
            for index, (unit, expected) in enumerate(zip(units, decisions), 1):
                if not isinstance(expected, list) or len(expected) < 3:
                    continue
                predicted, confidence = classify_clause(unit, classifier)
                totals["evaluated"] += 1
                if predicted[2] == "compile":
                    totals["retained"] += 1
                if expected[2] != "compile":
                    totals["expected_noncompiled"] += 1
                    if predicted[2] == "compile":
                        totals["conservative_overcompile"] += 1
                    continue
                totals["expected_compiled"] += 1
                expected_role = str(expected[0])
                predicted_role = str(predicted[0])
                role_confusion[expected_role][predicted_role] += 1
                if expected_role == predicted_role:
                    totals["role_exact"] += 1
                if str(expected[1]) == str(predicted[1]):
                    totals["force_exact"] += 1
                expected_kinds = owner_kinds.get(index, set())
                predicted_family, _ = classify_native_kind(
                    unit, predicted_role, classifier
                )
                predicted_kind = {
                    "definition": "definition",
                    "mapping": "mapping",
                    "rule": "rule",
                    "constraint": "constraint",
                }.get(predicted_family, "procedure")
                if expected_kinds and predicted_family != "example":
                    totals["semantic_owner_evaluated"] += 1
                    for expected_kind in sorted(expected_kinds):
                        owner_kind_confusion[expected_kind][predicted_kind] += 1
                    if predicted_kind in expected_kinds:
                        totals["semantic_owner_exact"] += 1
                    normalized_expected = {
                        "procedure" if kind == "decision" else kind
                        for kind in expected_kinds
                    }
                    if predicted_kind in normalized_expected:
                        totals["semantic_owner_family_exact"] += 1
                bucket = "high" if confidence >= 0.9 else "medium" if confidence >= 0.75 else "low"
                confidence_buckets[bucket]["count"] += 1
                if expected_role == predicted_role:
                    confidence_buckets[bucket]["role_exact"] += 1

    expected_compiled = totals["expected_compiled"]
    output = {
        "format": "iupac-bluebook-bootstrap-induction-audit",
        "format_version": "1.0.0",
        "passed": totals["retained"] == totals["evaluated"] and bool(tasks),
        "fold_count": fold_count,
        "task_count": len(tasks),
        "metrics": {
            **dict(totals),
            "retention_rate": round(totals["retained"] / max(totals["evaluated"], 1), 6),
            "compiled_role_accuracy": round(totals["role_exact"] / max(expected_compiled, 1), 6),
            "compiled_force_accuracy": round(totals["force_exact"] / max(expected_compiled, 1), 6),
            "semantic_owner_kind_accuracy": round(
                totals["semantic_owner_exact"]
                / max(totals["semantic_owner_evaluated"], 1),
                6,
            ),
            "semantic_owner_family_accuracy": round(
                totals["semantic_owner_family_exact"]
                / max(totals["semantic_owner_evaluated"], 1),
                6,
            ),
        },
        "confidence_buckets": {
            bucket: {
                **dict(counts),
                "role_accuracy": round(counts["role_exact"] / max(counts["count"], 1), 6),
            }
            for bucket, counts in sorted(confidence_buckets.items())
        },
        "role_confusion": {
            role: dict(predictions.most_common())
            for role, predictions in sorted(role_confusion.items())
        },
        "semantic_owner_kind_confusion": {
            kind: dict(predictions.most_common())
            for kind, predictions in sorted(owner_kind_confusion.items())
        },
    }
    return output


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-validate deterministic semantic-role induction"
    )
    parser.add_argument("--training-dir", type=Path, default=DEFAULT_TRAINING_DIR)
    parser.add_argument("--task-dir", type=Path, default=DEFAULT_TASK_DIR)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output = audit_induction(
        args.training_dir, args.task_dir, fold_count=args.folds
    )
    rendered = json.dumps(output, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
