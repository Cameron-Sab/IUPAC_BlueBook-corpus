from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.audit_semantic_delta_progress import audit_progress
    from scripts.build_compact_semantic_tasks import (
        DEFAULT_OUTPUT as DEFAULT_TASK_DIR,
        canonical_json_bytes,
        digest_without_field,
        load_json,
    )
    from scripts.render_semantic_authoring_task import authoring_view_bytes
    from scripts.scaffold_semantic_authoring import authoring_metrics, scaffold_authoring
else:
    from audit_semantic_delta_progress import audit_progress
    from build_compact_semantic_tasks import (
        DEFAULT_OUTPUT as DEFAULT_TASK_DIR,
        canonical_json_bytes,
        digest_without_field,
        load_json,
    )
    from render_semantic_authoring_task import authoring_view_bytes
    from scaffold_semantic_authoring import authoring_metrics, scaffold_authoring


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DELTA_DIR = ROOT / "data" / "bluebook_v3" / "semantic_deltas"


def pack_tasks(
    tasks: Sequence[Mapping[str, Any]],
    *,
    max_view_bytes: int,
    max_unresolved_clauses: int = 100,
    max_tasks: int,
) -> list[list[Mapping[str, Any]]]:
    if max_view_bytes < 1 or max_unresolved_clauses < 1 or max_tasks < 1:
        raise ValueError("Wave limits must be positive")
    ordered = sorted(tasks, key=lambda item: (item["view_bytes"], item["task_id"]))
    batches: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    current_bytes = 0
    current_clauses = 0
    for task in ordered:
        task_bytes = int(task["view_bytes"])
        task_clauses = int(task.get("unresolved_clause_count", 0))
        exceeds_bytes = current and current_bytes + task_bytes > max_view_bytes
        exceeds_clauses = (
            current and current_clauses + task_clauses > max_unresolved_clauses
        )
        if exceeds_bytes or exceeds_clauses or len(current) >= max_tasks:
            batches.append(current)
            current = []
            current_bytes = 0
            current_clauses = 0
        current.append(task)
        current_bytes += task_bytes
        current_clauses += task_clauses
    if current:
        batches.append(current)
    return batches


def build_plan(
    *,
    task_dir: Path = DEFAULT_TASK_DIR,
    delta_dir: Path = DEFAULT_DELTA_DIR,
    max_view_bytes: int = 50_000,
    max_unresolved_clauses: int = 100,
    max_tasks: int = 4,
    lanes: int = 4,
    skip_task_ids: Sequence[str] = (),
) -> dict[str, Any]:
    if lanes < 1:
        raise ValueError("lanes must be positive")
    progress = audit_progress(task_dir, delta_dir)
    if not progress["passed"]:
        raise ValueError("Cannot plan while semantic deltas are invalid")

    manifest = load_json(task_dir / "manifest.json")
    skipped = set(skip_task_ids)
    unknown_skips = skipped.difference(entry["task_id"] for entry in manifest["tasks"])
    if unknown_skips:
        raise ValueError(f"Unknown skipped tasks: {sorted(unknown_skips)}")

    missing = set(progress["missing_task_ids"]) | set(progress["stale_task_ids"])
    records = []
    for entry in manifest["tasks"]:
        task_id = entry["task_id"]
        if task_id not in missing or task_id in skipped:
            continue
        task = load_json(task_dir / f"{task_id}.json")
        metrics = authoring_metrics(task, scaffold_authoring(task))
        records.append(
            {
                "task_id": task_id,
                "task_sha256": task["task_sha256"],
                "view_bytes": len(authoring_view_bytes(task)),
                "clause_count": metrics["clause_count"],
                "unresolved_clause_count": metrics["unresolved_clause_count"],
                "citation_count": metrics["citation_count"],
                "unresolved_citation_count": metrics["unresolved_citation_count"],
            }
        )

    packed = pack_tasks(
        records,
        max_view_bytes=max_view_bytes,
        max_unresolved_clauses=max_unresolved_clauses,
        max_tasks=max_tasks,
    )
    batches = []
    for index, tasks in enumerate(packed, 1):
        batches.append(
            {
                "batch_id": f"batch-{index:03d}",
                "task_ids": [item["task_id"] for item in tasks],
                "task_count": len(tasks),
                "view_bytes": sum(item["view_bytes"] for item in tasks),
                "unresolved_clause_count": sum(
                    item["unresolved_clause_count"] for item in tasks
                ),
                "unresolved_citation_count": sum(
                    item["unresolved_citation_count"] for item in tasks
                ),
                "tasks": list(tasks),
            }
        )

    waves = []
    for offset in range(0, len(batches), lanes):
        members = batches[offset : offset + lanes]
        waves.append(
            {
                "wave_id": f"wave-{len(waves) + 1:03d}",
                "batch_ids": [item["batch_id"] for item in members],
                "view_bytes": sum(item["view_bytes"] for item in members),
                "unresolved_clause_count": sum(
                    item["unresolved_clause_count"] for item in members
                ),
            }
        )

    plan: dict[str, Any] = {
        "format": "iupac-bluebook-semantic-authoring-plan",
        "format_version": "1.0.0",
        "task_manifest_sha256": manifest["manifest_sha256"],
        "limits": {
            "max_view_bytes_per_batch": max_view_bytes,
            "max_unresolved_clauses_per_batch": max_unresolved_clauses,
            "max_tasks_per_batch": max_tasks,
            "lanes_per_wave": lanes,
        },
        "completed_task_count": progress["completed_task_count"],
        "skipped_task_ids": sorted(skipped),
        "planned_task_count": len(records),
        "planned_view_bytes": sum(item["view_bytes"] for item in records),
        "planned_unresolved_clause_count": sum(
            item["unresolved_clause_count"] for item in records
        ),
        "planned_unresolved_citation_count": sum(
            item["unresolved_citation_count"] for item in records
        ),
        "batch_count": len(batches),
        "wave_count": len(waves),
        "waves": waves,
        "batches": batches,
    }
    plan["plan_sha256"] = digest_without_field(plan, "plan_sha256")
    return plan


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan bounded semantic-authoring waves from sparse evidence size"
    )
    parser.add_argument("--task-dir", type=Path, default=DEFAULT_TASK_DIR)
    parser.add_argument("--delta-dir", type=Path, default=DEFAULT_DELTA_DIR)
    parser.add_argument("--max-view-bytes", type=int, default=50_000)
    parser.add_argument("--max-unresolved-clauses", type=int, default=100)
    parser.add_argument("--max-tasks", type=int, default=4)
    parser.add_argument("--lanes", type=int, default=4)
    parser.add_argument("--skip-task", action="append", default=[])
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_plan(
            task_dir=args.task_dir,
            delta_dir=args.delta_dir,
            max_view_bytes=args.max_view_bytes,
            max_unresolved_clauses=args.max_unresolved_clauses,
            max_tasks=args.max_tasks,
            lanes=args.lanes,
            skip_task_ids=args.skip_task,
        )
    except (KeyError, TypeError, ValueError, OSError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1
    rendered = canonical_json_bytes(plan)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(rendered)
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "planned_task_count": plan["planned_task_count"],
                    "batch_count": plan["batch_count"],
                    "wave_count": plan["wave_count"],
                    "plan_sha256": plan["plan_sha256"],
                },
                indent=2,
            )
        )
    else:
        print(rendered.decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
