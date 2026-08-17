from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

if __package__:
    from scripts.bootstrap_semantic_authoring import DEFAULT_TASK_DIR
    from scripts.build_compact_semantic_tasks import load_json
    from scripts.local_semantic_compaction import (
        DEFAULT_REFERENCE_DIR,
        VALIDATOR_VERSION,
        build_candidate_view,
        build_prompt,
        process_task,
    )
else:
    from bootstrap_semantic_authoring import DEFAULT_TASK_DIR  # type: ignore[no-redef]
    from build_compact_semantic_tasks import load_json  # type: ignore[no-redef]
    from local_semantic_compaction import (  # type: ignore[no-redef]
        DEFAULT_REFERENCE_DIR,
        VALIDATOR_VERSION,
        build_candidate_view,
        build_prompt,
        process_task,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTHORING = ROOT / "work" / "bootstrap_semantic_authoring"
DEFAULT_OUTPUT = ROOT / "work" / "local_semantic_compaction_production"
DEFAULT_STATE = ROOT / "work" / "local_semantic_supervisor" / "state.json"


def now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def task_prompt_bytes(task_path: Path, authoring_dir: Path) -> int:
    task = load_json(task_path)
    authoring = load_json(authoring_dir / task_path.name)
    return len(build_prompt(build_candidate_view(task, authoring)).encode("utf-8"))


def discover_tasks(task_dir: Path, authoring_dir: Path) -> list[tuple[int, Path]]:
    tasks = []
    for authoring_path in sorted(authoring_dir.glob("*.json")):
        task_path = task_dir / authoring_path.name
        if task_path.is_file():
            tasks.append((task_prompt_bytes(task_path, authoring_dir), task_path))
    return sorted(tasks, key=lambda item: (item[0], item[1].name))


def load_state(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
    else:
        state = {
            "format": "iupac-local-semantic-supervisor-state",
            "format_version": "1.0.0",
            "started_at": now(),
            "tasks": {},
        }
    state["status"] = "running"
    state["updated_at"] = now()
    state["pid"] = os.getpid()
    state["configuration"] = {
        "model": args.model,
        "backend": args.backend,
        "endpoint": args.endpoint,
        "context_tokens": args.context_tokens,
        "maximum_output_tokens": args.maximum_output_tokens,
        "repair_attempts": args.repair_attempts,
        "maximum_task_runs": args.maximum_task_runs,
        "validator_version": VALIDATOR_VERSION,
    }
    return state


def process_entry(
    args: argparse.Namespace,
    state: dict[str, Any],
    prompt_bytes: int,
    task_path: Path,
) -> None:
    task_id = task_path.stem
    task_state = state["tasks"].setdefault(task_id, {})
    run_count = int(task_state.get("run_count", 0))
    estimated_prompt_tokens = math.ceil(prompt_bytes / 3)
    available_output = args.context_tokens - estimated_prompt_tokens - 1024
    output_tokens = min(args.maximum_output_tokens, available_output)
    task_state.update(
        {
            "status": "running",
            "run_count": run_count + 1,
            "prompt_bytes": prompt_bytes,
            "estimated_prompt_tokens": estimated_prompt_tokens,
            "output_tokens": output_tokens,
            "started_at": now(),
        }
    )
    if output_tokens < args.minimum_output_tokens:
        task_state.update(
            {
                "status": "oversize",
                "error": "Insufficient context for the minimum output budget",
                "finished_at": now(),
            }
        )
        atomic_write_json(args.state, state)
        return
    atomic_write_json(args.state, state)
    try:
        report = process_task(
            task_path,
            authoring_dir=args.authoring_dir,
            reference_dir=args.reference_dir,
            output_dir=args.output_dir,
            model=args.model,
            backend=args.backend,
            endpoint=args.endpoint,
            context_tokens=args.context_tokens,
            output_tokens=output_tokens,
            timeout=args.timeout,
            repair_attempts=args.repair_attempts,
            seed=args.seed + run_count,
            dry_run=False,
            force=False,
        )
        passed = report.get("validation", {}).get("passed") is True
        task_state.update(
            {
                "status": "passed" if passed else "failed",
                "finished_at": now(),
                "report": report,
            }
        )
    except Exception as error:
        task_state.update(
            {
                "status": "infrastructure_error",
                "finished_at": now(),
                "error": f"{type(error).__name__}: {error}",
            }
        )
        time.sleep(args.infrastructure_retry_seconds)
    state["updated_at"] = now()
    atomic_write_json(args.state, state)


def run(args: argparse.Namespace) -> int:
    lock_path = args.state.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RuntimeError(f"Supervisor lock already exists: {lock_path}") from error
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
    finally:
        os.close(descriptor)

    state = load_state(args.state, args)
    stop_path = args.state.with_suffix(".stop")
    try:
        tasks = discover_tasks(args.task_dir, args.authoring_dir)
        state["task_count"] = len(tasks)
        atomic_write_json(args.state, state)
        for round_number in range(1, args.maximum_task_runs + 1):
            state["round"] = round_number
            runnable = [
                (prompt_bytes, task_path)
                for prompt_bytes, task_path in tasks
                if not (
                    state["tasks"].get(task_path.stem, {}).get("status") == "passed"
                    and state["tasks"]
                    .get(task_path.stem, {})
                    .get("report", {})
                    .get("validator_version")
                    == VALIDATOR_VERSION
                )
                and int(
                    state["tasks"].get(task_path.stem, {}).get("run_count", 0)
                )
                < args.maximum_task_runs
            ]
            if not runnable:
                break
            for prompt_bytes, task_path in runnable:
                if stop_path.exists():
                    state["status"] = "stopped"
                    break
                process_entry(args, state, prompt_bytes, task_path)
            if state.get("status") == "stopped":
                break

        for task_state in state["tasks"].values():
            if (
                task_state.get("status") not in {"passed", "oversize"}
                and int(task_state.get("run_count", 0)) >= args.maximum_task_runs
            ):
                task_state["status"] = "quarantined"

        statuses = [task.get("status") for task in state["tasks"].values()]
        if state.get("status") == "running":
            state["status"] = "complete" if statuses and all(
                status == "passed" for status in statuses
            ) else "needs_repair"
        state["summary"] = {
            status: statuses.count(status) for status in sorted(set(statuses))
        }
        state["updated_at"] = now()
        atomic_write_json(args.state, state)
        return 0 if state["status"] == "complete" else 2
    finally:
        lock_path.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local semantic conversion queue")
    parser.add_argument("--task-dir", type=Path, default=DEFAULT_TASK_DIR)
    parser.add_argument("--authoring-dir", type=Path, default=DEFAULT_AUTHORING)
    parser.add_argument("--reference-dir", type=Path, default=DEFAULT_REFERENCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--model", default="qwen3:30b-instruct")
    parser.add_argument("--backend", choices=("ollama", "openai"), default="openai")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument("--context-tokens", type=int, default=49152)
    parser.add_argument("--maximum-output-tokens", type=int, default=24576)
    parser.add_argument("--minimum-output-tokens", type=int, default=8192)
    parser.add_argument("--repair-attempts", type=int, default=2)
    parser.add_argument("--maximum-task-runs", type=int, default=6)
    parser.add_argument("--seed", type=int, default=3407)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--infrastructure-retry-seconds", type=int, default=30)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(parse_args(argv))
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
