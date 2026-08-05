from __future__ import annotations

from scripts.benchmark_chebi_iupac_names import (
    classify_result,
    name_features,
    ratio,
)


def test_name_structure_result_classification() -> None:
    expected = "AAAAAAAAAAAAAA-BBBBBBBBBB-C"
    assert classify_result(expected, expected) == "exact_inchikey_match"
    assert (
        classify_result(expected, "AAAAAAAAAAAAAA-CCCCCCCCCC-D")
        == "connectivity_match_stereo_or_protonation_differs"
    )
    assert (
        classify_result(expected, "ZZZZZZZZZZZZZZ-BBBBBBBBBB-C")
        == "connectivity_mismatch"
    )
    assert classify_result(expected, "") == "parse_failure"
    assert classify_result("", expected) == "missing_expected_key"


def test_name_feature_groups_are_nonexclusive() -> None:
    features = name_features("(2R)-2-aminocyclohexane-1-carboxylic acid")
    assert {"acid", "ring_system", "stereochemistry"}.issubset(features)
    assert name_features("methane") == ["other"]


def test_ratio_is_stable_and_handles_empty_denominator() -> None:
    assert ratio(1, 3) == 0.333333
    assert ratio(0, 0) is None
