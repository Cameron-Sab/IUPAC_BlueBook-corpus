from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build_compact_semantic_tasks import (
    canonical_json_bytes,
    load_json,
    sha256_bytes,
)
from scripts.build_semantic_asset_scaffold import (
    DEFAULT_INVENTORY,
    DEFAULT_OUTPUT,
    build_asset_scaffold,
    load_asset_scaffold,
    task_asset_figures,
)
from scripts.compile_semantic_authoring import AuthoringError, compile_authoring
from scripts.render_semantic_authoring_task import authoring_view_rows
from scripts.scaffold_semantic_authoring import scaffold_authoring


ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "work" / "compact_semantic_tasks"


def task(task_id: str) -> dict:
    return load_json(TASK_DIR / f"{task_id}.json")


def test_asset_scaffold_reproduces_from_bound_clause_inventory() -> None:
    raw = DEFAULT_INVENTORY.read_bytes()
    rebuilt = build_asset_scaffold(
        load_json(DEFAULT_INVENTORY), inventory_file_sha256=sha256_bytes(raw)
    )
    stored = load_asset_scaffold(DEFAULT_OUTPUT)

    assert rebuilt == stored
    assert canonical_json_bytes(rebuilt) == DEFAULT_OUTPUT.read_bytes()
    assert rebuilt["asset_count"] == 5_181


def test_task_figures_preserve_source_order_urls_and_clause_identity() -> None:
    figures = task_asset_figures(task("P-76-part-001"), load_asset_scaffold())

    assert [figure["clause_ids"] for figure in figures] == [
        ["P-76:clause:0005"],
        ["P-76:clause:0007"],
        ["P-76:clause:0009"],
    ]
    assert figures[0]["source_urls"] == [
        "https://iupac.qmul.ac.uk/BlueBook/P7gif/P76100a.gif"
    ]
    assert all(figure["kind"] == "chemical_structure" for figure in figures)


def test_new_scaffold_omits_mechanical_assets_from_sparse_evidence() -> None:
    source_task = task("P-76-part-001")
    authoring = scaffold_authoring(source_task)
    rows = authoring_view_rows(source_task)

    assert authoring["mechanical_assets"] is True
    assert [authoring["clauses"][index - 1] for index in (5, 7, 9)] == [
        None,
        None,
        None,
    ]
    assert not any(row[0] == "U" and row[3] == "image_asset" for row in rows)


def test_mechanical_assets_compile_to_typed_figures_and_dispositions() -> None:
    source_task = task("P-76-part-001")
    authoring = scaffold_authoring(source_task)
    for index, decision in enumerate(authoring["clauses"]):
        if decision == []:
            authoring["clauses"][index] = [
                "note",
                "informative",
                "skip",
                "explanatory_note",
            ]
    authoring["examples"] = []
    for clause_index, asset_index in ((4, 5), (6, 7), (8, 9)):
        authoring["clauses"][clause_index - 1] = [
            "example",
            "illustrative",
            "compile",
        ]
        authoring["examples"].append(
            {
                "id": f"caption_{clause_index}",
                "c": [clause_index],
                "input": None,
                "ok": [],
                "bad": [],
                "shows": [
                    ["figure", f"figure.asset.p_76:clause:{asset_index:04d}"]
                ],
            }
        )

    delta, _chunk, report = compile_authoring(authoring, source_task)

    assert report["passed"] is True
    assert len(delta["figures"]) == 3
    dispositions = {
        item["clause_id"]: item for item in delta["clause_dispositions"]
    }
    assert dispositions["P-76:clause:0005"]["role"] == "figure_asset"
    assert dispositions["P-76:clause:0005"]["disposition"]["targets"] == [
        {
            "kind": "figure",
            "id": "figure.asset.p_76:clause:0005",
        }
    ]


def test_mechanical_asset_clause_cannot_be_reauthored() -> None:
    source_task = task("P-76-part-001")
    authoring = scaffold_authoring(source_task)
    for index, decision in enumerate(authoring["clauses"]):
        if decision == []:
            authoring["clauses"][index] = [
                "note",
                "informative",
                "skip",
                "explanatory_note",
            ]
    authoring["clauses"][4] = ["figure_asset", "illustrative", "compile"]

    with pytest.raises(AuthoringError, match="must remain null"):
        compile_authoring(authoring, source_task)
