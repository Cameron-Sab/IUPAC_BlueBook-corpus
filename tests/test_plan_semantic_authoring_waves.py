from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.plan_semantic_authoring_waves import pack_tasks


ROOT = Path(__file__).resolve().parents[1]


def record(task_id: str, size: int) -> dict:
    return {"task_id": task_id, "view_bytes": size}


def test_pack_tasks_is_deterministic_smallest_first_and_bounded() -> None:
    tasks = [record("P-3", 40), record("P-1", 10), record("P-2", 20)]

    first = pack_tasks(tasks, max_view_bytes=30, max_tasks=2)
    second = pack_tasks(list(reversed(tasks)), max_view_bytes=30, max_tasks=2)

    assert first == second
    assert [[item["task_id"] for item in batch] for batch in first] == [
        ["P-1", "P-2"],
        ["P-3"],
    ]
    assert all(len(batch) <= 2 for batch in first)
    assert all(
        sum(item["view_bytes"] for item in batch) <= 30
        for batch in first
        if len(batch) > 1
    )


def test_oversized_single_task_is_preserved() -> None:
    assert pack_tasks([record("P-1", 99)], max_view_bytes=30, max_tasks=2) == [
        [record("P-1", 99)]
    ]


@pytest.mark.parametrize(
    ("max_view_bytes", "max_tasks"), [(0, 1), (1, 0), (-1, 1)]
)
def test_invalid_limits_fail(max_view_bytes: int, max_tasks: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        pack_tasks([], max_view_bytes=max_view_bytes, max_tasks=max_tasks)


def test_direct_cli_help_works() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/plan_semantic_authoring_waves.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "bounded semantic-authoring waves" in result.stdout
