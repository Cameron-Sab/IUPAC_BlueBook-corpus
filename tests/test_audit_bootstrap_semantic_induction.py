from __future__ import annotations

from scripts.audit_bootstrap_semantic_induction import _fold, audit_induction


def test_fold_is_stable() -> None:
    assert _fold("P-73-part-002", 5) == _fold("P-73-part-002", 5)
    assert 0 <= _fold("P-73-part-002", 5) < 5


def test_cross_validation_retains_every_authored_clause() -> None:
    report = audit_induction(fold_count=3)

    assert report["passed"] is True
    assert report["task_count"] > 0
    assert report["metrics"]["retention_rate"] == 1.0
