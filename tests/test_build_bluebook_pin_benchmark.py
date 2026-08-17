from __future__ import annotations

from scripts.build_bluebook_pin_benchmark import benchmark_split, extract_pin_names


def test_extract_pin_names_accepts_explicit_pin_lines_only() -> None:
    text = """CH3-CO-CH3
acetone
propan-2-one (PIN)
(2) cyclohexylethene
(1) ethenylcyclohexane (PIN; see P-52.2.8)
not an accepted name (PIN)
"""

    assert extract_pin_names(text) == ["propan-2-one", "ethenylcyclohexane"]


def test_benchmark_split_is_stable_and_closed() -> None:
    case_id = "BBPIN-0123456789ABCDEF0123"

    assert benchmark_split(case_id) == benchmark_split(case_id)
    assert benchmark_split(case_id) in {"calibration", "holdout", "final_holdout"}
