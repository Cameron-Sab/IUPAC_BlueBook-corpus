from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator

if __package__:
    from scripts import assemble_normalized_rule_corpus as assembler
    from scripts import validate_normalized_rule_chunks as chunk_validator
    from scripts.build_compact_semantic_tasks import canonical_json_bytes, load_json
    from scripts.build_semantic_asset_scaffold import (
        load_asset_scaffold,
        task_asset_figures,
    )
    from scripts.compile_semantic_delta import compile_delta, finalize_delta
    from scripts.render_compact_semantic_task import validate_task
    from scripts.scaffold_semantic_delta import scaffold_delta
else:
    import assemble_normalized_rule_corpus as assembler
    import validate_normalized_rule_chunks as chunk_validator
    from build_compact_semantic_tasks import canonical_json_bytes, load_json
    from build_semantic_asset_scaffold import load_asset_scaffold, task_asset_figures
    from compile_semantic_delta import compile_delta, finalize_delta
    from render_compact_semantic_task import validate_task
    from scaffold_semantic_delta import scaffold_delta


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "data" / "semantic_authoring.schema.json"
DEFAULT_TASK_DIR = ROOT / "work" / "compact_semantic_tasks"
DEFAULT_DELTA_DIR = ROOT / "data" / "bluebook_v3" / "semantic_deltas"
DEFAULT_CHUNK_DIR = ROOT / "data" / "bluebook_v3" / "semantic_chunks"

EXPRESSION_OPS = {
    "lit": "literal",
    "var": "var",
    "get": "get",
    "pred": "predicate",
    "call": "function",
    "all": "all",
    "any": "any",
    "not": "not",
    "exists": "exists",
    "forall": "forall",
    "cmp": "compare",
    "lookup": "table_lookup",
    "outcome": "rule_outcome",
}
STATEMENT_OPS = {
    "seq": "sequence",
    "if": "branch",
    "set": "assign",
    "xform": "transform",
    "render": "render",
    "reject": "reject",
    "invoke": "invoke",
    "each": "iterate",
    "emit": "emit",
    "assert": "assert",
}
UNIT_KINDS = {"rule", "decision", "definition", "mapping", "procedure", "constraint"}


class AuthoringError(ValueError):
    pass


def _slug(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value).strip("_.:-")
    if not result or not result[0].isalpha():
        result = "x_" + result
    return result


def _namespace(task_id: str) -> str:
    match = re.fullmatch(r"P-(\d+)-part-(\d{3})", task_id)
    if match is None:
        raise AuthoringError(f"Invalid task id: {task_id}")
    return f"p{match.group(1)}.part{match.group(2)}"


def _ordered_clauses(task: Mapping[str, Any]) -> list[str]:
    return [
        str(unit["clause_id"])
        for rule in task["rules"]
        for unit in rule["source_units"]
    ]


class Expander:
    def __init__(self, task: Mapping[str, Any]) -> None:
        self.task = task
        self.namespace = _namespace(str(task["task_id"]))
        self.ordered_clauses = _ordered_clauses(task)
        self.unit_ids: dict[str, str] = {}
        self.table_ids: dict[str, str] = {}
        self.column_ids: dict[tuple[str, str], str] = {}
        self.exception_ids: dict[str, str] = {}

    def clauses(self, indexes: Any) -> list[str]:
        if not isinstance(indexes, list) or not indexes:
            raise AuthoringError("Clause index list must be a nonempty array")
        result = []
        for raw in indexes:
            if not isinstance(raw, int) or isinstance(raw, bool):
                raise AuthoringError(f"Clause index must be an integer: {raw!r}")
            if raw < 1 or raw > len(self.ordered_clauses):
                raise AuthoringError(f"Clause index outside task: {raw}")
            result.append(self.ordered_clauses[raw - 1])
        if len(set(result)) != len(result):
            raise AuthoringError(f"Duplicate clause indexes: {indexes}")
        return result

    def unit_id(self, value: str) -> str:
        if value.startswith("unit."):
            return value
        return self.unit_ids.get(value, f"unit.{self.namespace}.{_slug(value)}")

    def table_id(self, value: str) -> str:
        if value.startswith("table."):
            return value
        return self.table_ids.get(value, f"table.{self.namespace}.{_slug(value)}")

    def column_id(self, table: str, value: str) -> str:
        if value.startswith("column."):
            return value
        return self.column_ids.get(
            (table, value), f"column.{self.namespace}.{_slug(table)}.{_slug(value)}"
        )

    def object_ref(self, value: Any) -> dict[str, str]:
        if not isinstance(value, list) or len(value) != 2:
            raise AuthoringError(f"Object reference must be [kind,id]: {value!r}")
        kind, object_id = value
        if not isinstance(kind, str):
            raise AuthoringError("Object reference kind must be a string")
        if kind == "clause":
            return {"kind": kind, "id": self.clauses([object_id])[0]}
        if not isinstance(object_id, str):
            raise AuthoringError("Object reference id must be a string")
        if kind == "semantic_unit":
            object_id = self.unit_id(object_id)
        elif kind == "table":
            object_id = self.table_id(object_id)
        elif kind == "exception" and not object_id.startswith("exception."):
            object_id = f"exception.{self.namespace}.{_slug(object_id)}"
        return {"kind": kind, "id": object_id}

    def expression(
        self, value: Any, *, owner: str, path: str, clause_ids: list[str]
    ) -> dict[str, Any]:
        if not isinstance(value, list) or not value or not isinstance(value[0], str):
            value = ["lit", value]
        short_op = value[0]
        op = EXPRESSION_OPS.get(short_op)
        if op is None:
            raise AuthoringError(f"Unknown expression operation at {path}: {short_op}")
        result: dict[str, Any] = {
            "expression_id": f"expr.{self.namespace}.{_slug(owner)}.{_slug(path)}",
            "clause_ids": clause_ids,
            "op": op,
        }
        if short_op == "lit":
            if len(value) != 2:
                raise AuthoringError(f"lit expects one value at {path}")
            result["value"] = value[1]
        elif short_op == "var":
            if len(value) != 2 or not isinstance(value[1], str):
                raise AuthoringError(f"var expects a name at {path}")
            result["name"] = value[1]
        elif short_op == "get":
            if len(value) != 3 or not isinstance(value[2], str):
                raise AuthoringError(f"get expects expression and path at {path}")
            result["from"] = self.expression(
                value[1], owner=owner, path=f"{path}.from", clause_ids=clause_ids
            )
            result["path"] = value[2]
        elif short_op in {"pred", "call"}:
            if len(value) < 2 or not isinstance(value[1], str):
                raise AuthoringError(f"{short_op} expects a symbol at {path}")
            result["symbol"] = value[1]
            result["args"] = [
                self.expression(
                    arg,
                    owner=owner,
                    path=f"{path}.arg{index}",
                    clause_ids=clause_ids,
                )
                for index, arg in enumerate(value[2:], 1)
            ]
        elif short_op in {"all", "any"}:
            if len(value) < 2:
                raise AuthoringError(f"{short_op} requires at least one argument at {path}")
            result["args"] = [
                self.expression(
                    arg,
                    owner=owner,
                    path=f"{path}.arg{index}",
                    clause_ids=clause_ids,
                )
                for index, arg in enumerate(value[1:], 1)
            ]
        elif short_op == "not":
            if len(value) != 2:
                raise AuthoringError(f"not expects one expression at {path}")
            result["arg"] = self.expression(
                value[1], owner=owner, path=f"{path}.arg", clause_ids=clause_ids
            )
        elif short_op in {"exists", "forall"}:
            if len(value) != 4 or not isinstance(value[1], str):
                raise AuthoringError(f"{short_op} expects bind, collection, predicate at {path}")
            result["bind"] = value[1]
            result["in"] = self.expression(
                value[2], owner=owner, path=f"{path}.in", clause_ids=clause_ids
            )
            result["where"] = self.expression(
                value[3], owner=owner, path=f"{path}.where", clause_ids=clause_ids
            )
        elif short_op == "cmp":
            if len(value) != 4 or not isinstance(value[1], str):
                raise AuthoringError(f"cmp expects relation, left, right at {path}")
            result["relation"] = value[1]
            result["left"] = self.expression(
                value[2], owner=owner, path=f"{path}.left", clause_ids=clause_ids
            )
            result["right"] = self.expression(
                value[3], owner=owner, path=f"{path}.right", clause_ids=clause_ids
            )
        elif short_op == "lookup":
            if len(value) != 4 or not isinstance(value[1], str) or not isinstance(value[3], str):
                raise AuthoringError(f"lookup expects table, key, column at {path}")
            result["table_id"] = self.table_id(value[1])
            result["key"] = self.expression(
                value[2], owner=owner, path=f"{path}.key", clause_ids=clause_ids
            )
            result["column_id"] = self.column_id(value[1], value[3])
        elif short_op == "outcome":
            if len(value) != 3 or not all(isinstance(item, str) for item in value[1:]):
                raise AuthoringError(f"outcome expects rule id and outcome at {path}")
            result["rule_id"] = value[1]
            result["outcome"] = value[2]
        return result

    def statements(
        self, values: Any, *, owner: str, path: str, clause_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not isinstance(values, list):
            raise AuthoringError(f"Statement block must be an array at {path}")
        return [
            self.statement(
                value,
                owner=owner,
                path=f"{path}.{index}",
                clause_ids=clause_ids,
            )
            for index, value in enumerate(values, 1)
        ]

    def statement(
        self, value: Any, *, owner: str, path: str, clause_ids: list[str]
    ) -> dict[str, Any]:
        if not isinstance(value, list) or not value or not isinstance(value[0], str):
            raise AuthoringError(f"Statement must be an operation array at {path}")
        short_op = value[0]
        op = STATEMENT_OPS.get(short_op)
        if op is None:
            raise AuthoringError(f"Unknown statement operation at {path}: {short_op}")
        result: dict[str, Any] = {
            "statement_id": f"stmt.{self.namespace}.{_slug(owner)}.{_slug(path)}",
            "clause_ids": clause_ids,
            "op": op,
        }
        if short_op == "seq":
            result["steps"] = self.statements(
                value[1:], owner=owner, path=f"{path}.steps", clause_ids=clause_ids
            )
        elif short_op == "if":
            if len(value) != 4:
                raise AuthoringError(f"if expects condition, then, else at {path}")
            result["when"] = self.expression(
                value[1], owner=owner, path=f"{path}.when", clause_ids=clause_ids
            )
            result["then"] = self.statements(
                value[2], owner=owner, path=f"{path}.then", clause_ids=clause_ids
            )
            result["else"] = self.statements(
                value[3], owner=owner, path=f"{path}.else", clause_ids=clause_ids
            )
        elif short_op == "set":
            if len(value) != 3 or not isinstance(value[1], str):
                raise AuthoringError(f"set expects target and value at {path}")
            result["target"] = value[1]
            result["value"] = self.expression(
                value[2], owner=owner, path=f"{path}.value", clause_ids=clause_ids
            )
        elif short_op == "xform":
            if len(value) < 3 or not all(isinstance(item, str) for item in value[1:3]):
                raise AuthoringError(f"xform expects target, symbol, args at {path}")
            result["target"] = value[1]
            result["transformation"] = value[2]
            result["args"] = [
                self.expression(
                    arg,
                    owner=owner,
                    path=f"{path}.arg{index}",
                    clause_ids=clause_ids,
                )
                for index, arg in enumerate(value[3:], 1)
            ]
        elif short_op == "render":
            if len(value) != 4 or not all(isinstance(item, str) for item in value[1:3]):
                raise AuthoringError(f"render expects component, position, value at {path}")
            result["component"] = value[1]
            result["position"] = value[2]
            result["value"] = self.expression(
                value[3], owner=owner, path=f"{path}.value", clause_ids=clause_ids
            )
        elif short_op == "reject":
            if len(value) != 3 or not all(isinstance(item, str) for item in value[1:]):
                raise AuthoringError(f"reject expects target and reason at {path}")
            result["target"] = value[1]
            result["reason_code"] = value[2]
        elif short_op == "invoke":
            if len(value) != 3 or not isinstance(value[1], str) or not isinstance(value[2], Mapping):
                raise AuthoringError(f"invoke expects rule and bindings at {path}")
            result["rule_id"] = value[1]
            result["bindings"] = {
                str(name): self.expression(
                    expression,
                    owner=owner,
                    path=f"{path}.binding.{_slug(str(name))}",
                    clause_ids=clause_ids,
                )
                for name, expression in value[2].items()
            }
        elif short_op == "each":
            if len(value) != 5 or not isinstance(value[1], str):
                raise AuthoringError(f"each expects bind, collection, body, stop at {path}")
            result["bind"] = value[1]
            result["in"] = self.expression(
                value[2], owner=owner, path=f"{path}.in", clause_ids=clause_ids
            )
            result["body"] = self.statements(
                value[3], owner=owner, path=f"{path}.body", clause_ids=clause_ids
            )
            result["stop_when"] = self.expression(
                value[4], owner=owner, path=f"{path}.stop", clause_ids=clause_ids
            )
        elif short_op == "emit":
            if len(value) != 2:
                raise AuthoringError(f"emit expects a value at {path}")
            result["value"] = self.expression(
                value[1], owner=owner, path=f"{path}.value", clause_ids=clause_ids
            )
        elif short_op == "assert":
            if len(value) != 3 or not isinstance(value[2], str):
                raise AuthoringError(f"assert expects expression and reason at {path}")
            result["assertion"] = self.expression(
                value[1], owner=owner, path=f"{path}.assertion", clause_ids=clause_ids
            )
            result["reason_code"] = value[2]
        return result

    @staticmethod
    def bindings(value: Any, label: str) -> list[dict[str, str]]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise AuthoringError(f"{label} bindings must be an array")
        result = []
        for item in value:
            if not isinstance(item, list) or len(item) != 2 or not all(
                isinstance(member, str) for member in item
            ):
                raise AuthoringError(f"{label} binding must be [name,type]: {item!r}")
            result.append({"name": item[0], "type": item[1]})
        return result

    def scope(
        self, value: Any, *, owner: str, clause_ids: list[str]
    ) -> dict[str, Any]:
        if value is None:
            regimes = ["all"]
            expression = ["lit", True]
        else:
            if not isinstance(value, Mapping):
                raise AuthoringError(f"Scope for {owner} must be an object")
            regimes = value.get("r", ["all"])
            expression = value.get("if", ["lit", True])
        return {
            "regimes": regimes,
            "applies_to": self.expression(
                expression, owner=owner, path="scope", clause_ids=clause_ids
            ),
        }

    def register_ids(self, authoring: Mapping[str, Any]) -> None:
        for unit in authoring["units"]:
            if not isinstance(unit, Mapping) or not isinstance(unit.get("id"), str):
                raise AuthoringError("Every unit needs a string id")
            self.unit_ids[str(unit["id"])] = self.unit_id(str(unit["id"]))
        for table in authoring["tables"]:
            if not isinstance(table, Mapping) or not isinstance(table.get("id"), str):
                raise AuthoringError("Every table needs a string id")
            self.table_ids[str(table["id"])] = self.table_id(str(table["id"]))
            for column in table.get("cols", []):
                if not isinstance(column, list) or len(column) < 3:
                    raise AuthoringError(
                        f"Table column must be [id,label,type,clauses?]: {column!r}"
                    )
                column_slug = str(column[0])
                self.column_ids[(str(table["id"]), column_slug)] = self.column_id(
                    str(table["id"]), column_slug
                )

    def unit(self, source: Mapping[str, Any]) -> dict[str, Any]:
        slug = str(source["id"])
        kind = source.get("k")
        if kind not in UNIT_KINDS:
            raise AuthoringError(f"Unknown unit kind for {slug}: {kind}")
        clause_ids = self.clauses(source.get("c"))
        result: dict[str, Any] = {
            "unit_id": self.unit_id(slug),
            "kind": kind,
            "force": source.get("f", "definition" if kind == "definition" else "required"),
            "clause_ids": clause_ids,
            "scope": self.scope(source.get("scope"), owner=slug, clause_ids=clause_ids),
            "inputs": self.bindings(source.get("in"), f"{slug} input"),
            "outputs": self.bindings(source.get("out"), f"{slug} output"),
        }
        if kind == "rule":
            result["when"] = self.expression(
                source["if"], owner=slug, path="when", clause_ids=clause_ids
            )
            result["then"] = self.statements(
                source.get("then", []), owner=slug, path="then", clause_ids=clause_ids
            )
            result["else"] = self.statements(
                source.get("else", []), owner=slug, path="else", clause_ids=clause_ids
            )
        elif kind == "definition":
            result["term"] = source["term"]
            result["entity_type"] = source["entity"]
            result["value"] = self.expression(
                source["value"], owner=slug, path="value", clause_ids=clause_ids
            )
        elif kind == "procedure":
            result["steps"] = self.statements(
                source["steps"], owner=slug, path="steps", clause_ids=clause_ids
            )
        elif kind == "constraint":
            result["assertion"] = self.expression(
                source["assert"], owner=slug, path="assertion", clause_ids=clause_ids
            )
            result["on_violation"] = self.statements(
                source["violation"],
                owner=slug,
                path="violation",
                clause_ids=clause_ids,
            )
        elif kind == "mapping":
            result["table_id"] = self.table_id(str(source["table"]))
        elif kind == "decision":
            result["candidates"] = self.expression(
                source["candidates"],
                owner=slug,
                path="candidates",
                clause_ids=clause_ids,
            )
            stages = []
            for index, stage in enumerate(source["stages"], 1):
                if not isinstance(stage, Mapping):
                    raise AuthoringError(f"Decision stage {slug}.{index} must be an object")
                stage_slug = str(stage.get("id", f"stage{index}"))
                stage_clauses = self.clauses(stage.get("c", source["c"]))
                comparator = stage["cmp"]
                if not isinstance(comparator, list) or len(comparator) != 4:
                    raise AuthoringError(f"Comparator must be [kind,direction,symbol,table]: {slug}")
                stages.append(
                    {
                        "stage_id": f"stage.{self.namespace}.{_slug(slug)}.{_slug(stage_slug)}",
                        "ordinal": index,
                        "clause_ids": stage_clauses,
                        "guard": self.expression(
                            stage.get("if", ["lit", True]),
                            owner=slug,
                            path=f"stage.{stage_slug}.guard",
                            clause_ids=stage_clauses,
                        ),
                        "key": self.expression(
                            stage["key"],
                            owner=slug,
                            path=f"stage.{stage_slug}.key",
                            clause_ids=stage_clauses,
                        ),
                        "comparator": {
                            "kind": comparator[0],
                            "direction": comparator[1],
                            "symbol": comparator[2],
                            "table_id": (
                                self.table_id(comparator[3])
                                if isinstance(comparator[3], str)
                                else None
                            ),
                        },
                        "on_tie": {
                            "mode": "continue",
                            "next_stage_id": (
                                f"stage.{self.namespace}.{_slug(slug)}."
                                f"{_slug(str(source['stages'][index].get('id', f'stage{index + 1}')))}"
                                if index < len(source["stages"])
                                else None
                            ),
                        },
                    }
                )
            result["stages"] = stages
            tie = source["tie"]
            if not isinstance(tie, list) or len(tie) != 2:
                raise AuthoringError(f"Terminal tie must be [mode,ref|null]: {slug}")
            result["terminal_tie"] = {
                "mode": tie[0],
                "fallback_ref": self.object_ref(tie[1]) if tie[1] is not None else None,
            }
        return result

    def symbols(self, values: Any) -> list[dict[str, Any]]:
        result = []
        for source in values:
            if not isinstance(source, Mapping):
                raise AuthoringError("Symbol must be an object")
            grounding = source.get("g", ["primitive", [], source.get("id")])
            if not isinstance(grounding, list) or len(grounding) != 3:
                raise AuthoringError(f"Symbol grounding must be [kind,refs,primitive]: {source}")
            result.append(
                {
                    "symbol_id": source["id"],
                    "kind": source["k"],
                    "description": source["d"],
                    "arguments": self.bindings(source.get("a"), f"{source['id']} argument"),
                    "returns": source["ret"],
                    "grounding": {
                        "kind": grounding[0],
                        "refs": [self.object_ref(ref) for ref in grounding[1]],
                        "primitive": grounding[2],
                    },
                }
            )
        return result

    def exception(self, source: Mapping[str, Any]) -> dict[str, Any]:
        slug = str(source["id"])
        clause_ids = self.clauses(source["c"])
        mode = source["mode"]
        return {
            "exception_id": (
                slug
                if slug.startswith("exception.")
                else f"exception.{self.namespace}.{_slug(slug)}"
            ),
            "clause_ids": clause_ids,
            "when": self.expression(
                source["if"], owner=f"exception.{slug}", path="when", clause_ids=clause_ids
            ),
            "target": self.object_ref(source["target"]),
            "effect": {
                "mode": mode,
                "replacement": (
                    self.object_ref(source["replacement"])
                    if source.get("replacement") is not None
                    else None
                ),
                "guard": (
                    self.expression(
                        source["guard"],
                        owner=f"exception.{slug}",
                        path="guard",
                        clause_ids=clause_ids,
                    )
                    if source.get("guard") is not None
                    else None
                ),
                "redirect": (
                    self.object_ref(source["redirect"])
                    if source.get("redirect") is not None
                    else None
                ),
            },
            "precedence": {
                "specificity": source.get("specificity", 0),
                "source_order": source["order"],
            },
        }

    def table(self, source: Mapping[str, Any]) -> dict[str, Any]:
        slug = str(source["id"])
        clause_ids = self.clauses(source["c"])
        columns = []
        column_slugs = []
        for index, column in enumerate(source["cols"], 1):
            column_slug, label, value_type = column[:3]
            column_clauses = self.clauses(column[3]) if len(column) == 4 else clause_ids
            column_slugs.append(str(column_slug))
            columns.append(
                {
                    "column_id": self.column_id(slug, str(column_slug)),
                    "ordinal": index,
                    "label": label,
                    "value_type": value_type,
                    "clause_ids": column_clauses,
                }
            )
        rows = []
        for index, row in enumerate(source["rows"], 1):
            if not isinstance(row, Mapping):
                raise AuthoringError(f"Table row must be an object: {slug}.{index}")
            row_slug = str(row.get("id", f"row{index}"))
            row_clauses = self.clauses(row.get("c", source["c"]))
            values = row["v"]
            if not isinstance(values, list) or len(values) != len(columns):
                raise AuthoringError(
                    f"Table row {slug}.{row_slug} has {len(values)} values; expected {len(columns)}"
                )
            cells = [
                {
                    "cell_id": (
                        f"cell.{self.namespace}.{_slug(slug)}.{_slug(row_slug)}."
                        f"{_slug(column_slug)}"
                    ),
                    "column_id": self.column_id(slug, column_slug),
                    "value": value,
                    "clause_ids": row_clauses,
                }
                for column_slug, value in zip(column_slugs, values)
            ]
            rows.append(
                {
                    "row_id": f"row.{self.namespace}.{_slug(slug)}.{_slug(row_slug)}",
                    "ordinal": index,
                    "rank_group": row.get("rank"),
                    "cells": cells,
                    "clause_ids": row_clauses,
                }
            )
        footnotes = []
        for index, footnote in enumerate(source.get("footnotes", []), 1):
            footnote_clauses = self.clauses(footnote.get("c", source["c"]))
            footnotes.append(
                {
                    "footnote_id": (
                        f"footnote.{self.namespace}.{_slug(slug)}."
                        f"{_slug(str(footnote.get('id', index)))}"
                    ),
                    "marker": footnote["marker"],
                    "text": footnote["text"],
                    "scope_refs": [
                        self.object_ref(ref) for ref in footnote["scope"]
                    ],
                    "clause_ids": footnote_clauses,
                }
            )
        contract = source["contract"]
        return {
            "table_id": self.table_id(slug),
            "label": source.get("label"),
            "title": source.get("title"),
            "clause_ids": clause_ids,
            "columns": columns,
            "rows": rows,
            "footnotes": footnotes,
            "contract": {
                "key_column_ids": [
                    self.column_id(slug, value) for value in contract["key"]
                ],
                "result_column_ids": [
                    self.column_id(slug, value) for value in contract["result"]
                ],
                "cardinality": contract["cardinality"],
                "ordering": contract["ordering"],
            },
        }

    def example(self, source: Mapping[str, Any]) -> dict[str, Any]:
        slug = str(source["id"])
        return {
            "example_id": (
                slug if slug.startswith("example.") else f"example.{self.namespace}.{_slug(slug)}"
            ),
            "clause_ids": self.clauses(source["c"]),
            "operative": False,
            "input": source.get("input"),
            "accepted_names": source.get("ok", []),
            "rejected_names": source.get("bad", []),
            "demonstrates": [self.object_ref(ref) for ref in source.get("shows", [])],
            "explanation": source.get("why"),
        }

    def figure(self, source: Mapping[str, Any]) -> dict[str, Any]:
        slug = str(source["id"])
        return {
            "figure_id": (
                slug if slug.startswith("figure.") else f"figure.{self.namespace}.{_slug(slug)}"
            ),
            "clause_ids": self.clauses(source["c"]),
            "kind": source["k"],
            "source_urls": source["urls"],
            "content_sha256": source.get("sha256"),
        }

    def correction(self, source: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "application_id": source["id"],
            "overlay_id": source["overlay"],
            "operation_id": source["operation"],
            "status": source["status"],
            "before_clause_ids": self.clauses(source["before"]) if source.get("before") else [],
            "after_clause_ids": self.clauses(source["after"]) if source.get("after") else [],
            "target_refs": [self.object_ref(ref) for ref in source["targets"]],
            "effective_date": source.get("date"),
            "result_sha256": source["sha256"],
        }


def _validate_authoring(authoring: Mapping[str, Any], schema_path: Path) -> None:
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(authoring),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        details = "; ".join(
            f"/{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in errors
        )
        raise AuthoringError("Compact authoring schema failed: " + details)


def _dispositions(
    authoring: Mapping[str, Any],
    expander: Expander,
    semantic_payload: Mapping[str, Any],
    scaffold: Mapping[str, Any],
    mechanical_compiled_clause_ids: set[str],
) -> list[dict[str, Any]]:
    clauses = authoring["clauses"]
    if len(clauses) != len(expander.ordered_clauses):
        raise AuthoringError(
            f"Authoring clauses must contain exactly {len(expander.ordered_clauses)} ordered entries"
        )
    targets_by_clause: dict[str, list[dict[str, str]]] = defaultdict(list)
    corpus_view = {
        "symbol_registry": {"symbols": []},
        "semantic_units": semantic_payload["semantic_units"],
        "exceptions": semantic_payload["exceptions"],
        "tables": semantic_payload["tables"],
        "figures": semantic_payload["figures"],
        "examples": semantic_payload["examples"],
        "correction_applications": semantic_payload["correction_applications"],
        "references": [],
        "records": [],
        "dependency_edges": [],
    }
    for item in assembler._iter_addressable(corpus_view):
        if item.kind == "symbol":
            continue
        clause_ids = item.value.get("clause_ids")
        if not isinstance(clause_ids, list):
            continue
        for clause_id in clause_ids:
            target = {"kind": item.kind, "id": item.object_id}
            if target not in targets_by_clause[str(clause_id)]:
                targets_by_clause[str(clause_id)].append(target)

    prefilled = {
        item["clause_id"]: item for item in scaffold["clause_dispositions"]
    }
    result = []
    for clause_id, source in zip(expander.ordered_clauses, clauses):
        if clause_id in prefilled:
            if source is not None:
                raise AuthoringError(
                    f"Mechanically proven clause must remain null in authoring: {clause_id}"
                )
            result.append(prefilled[clause_id])
            continue
        if source is None:
            if clause_id not in mechanical_compiled_clause_ids:
                raise AuthoringError(f"Unresolved clause decision remains: {clause_id}")
            targets = targets_by_clause.get(clause_id, [])
            if not targets or any(target["kind"] != "figure" for target in targets):
                raise AuthoringError(
                    f"Mechanical asset clause has invalid semantic target: {clause_id}"
                )
            result.append(
                {
                    "clause_id": clause_id,
                    "role": "figure_asset",
                    "force": "illustrative",
                    "disposition": {"kind": "compiled", "targets": targets},
                }
            )
            continue
        if clause_id in mechanical_compiled_clause_ids:
            raise AuthoringError(
                f"Mechanically generated asset clause must remain null: {clause_id}"
            )
        if not isinstance(source, list) or len(source) < 3:
            raise AuthoringError(f"Malformed clause decision for {clause_id}")
        role, force, disposition = source[:3]
        if disposition == "compile":
            targets = targets_by_clause.get(clause_id, [])
            if not targets:
                raise AuthoringError(f"Compiled clause has no semantic target: {clause_id}")
            body: dict[str, Any] = {"kind": "compiled", "targets": targets}
        elif disposition == "skip":
            if len(source) != 4:
                raise AuthoringError(f"Skipped clause requires a reason: {clause_id}")
            body = {"kind": "nonoperative", "reason_code": source[3]}
        elif disposition == "supersede":
            if len(source) != 5 or not isinstance(source[3], list):
                raise AuthoringError(f"Superseded clause requires applications and successors: {clause_id}")
            body = {
                "kind": "superseded",
                "correction_application_ids": source[3],
                "successor_clause_ids": expander.clauses(source[4]),
            }
        else:
            raise AuthoringError(f"Unknown disposition {disposition!r}: {clause_id}")
        result.append(
            {"clause_id": clause_id, "role": role, "force": force, "disposition": body}
        )
    return result


def _citation_bindings(
    values: Any,
    expander: Expander,
    task: Mapping[str, Any],
    scaffold: Mapping[str, Any],
) -> list[dict[str, Any]]:
    occurrences = {
        occurrence["occurrence_id"]: occurrence
        for rule in task["rules"]
        for occurrence in rule["references"]
    }
    result = [dict(item) for item in scaffold["citation_bindings"]]
    prefilled_occurrences = {
        occurrence_id
        for item in result
        for occurrence_id in item["occurrence_ids"]
    }
    for index, source in enumerate(values, 1):
        if not isinstance(source, Mapping):
            raise AuthoringError("Citation binding must be an object")
        occurrence_ids = source.get("o")
        if isinstance(occurrence_ids, str):
            occurrence_ids = [occurrence_ids]
        if not isinstance(occurrence_ids, list) or not occurrence_ids:
            raise AuthoringError("Citation binding needs occurrence id(s)")
        unknown = set(occurrence_ids).difference(occurrences)
        if unknown:
            raise AuthoringError(f"Citation binding has unknown occurrences: {sorted(unknown)}")
        duplicate = set(occurrence_ids).intersection(prefilled_occurrences)
        if duplicate:
            raise AuthoringError(
                f"Mechanically prefilled occurrences must not be reauthored: {sorted(duplicate)}"
            )
        first = occurrences[occurrence_ids[0]]
        resolution = source.get(
            "resolution",
            "external" if first["effective_target_kind"] == "external_or_historical" else "exact",
        )
        binding: dict[str, Any] = {
            "reference_id": source.get(
                "id", f"reference.{expander.namespace}.citation{index}"
            ),
            "clause_ids": expander.clauses(source["c"]),
            "relation": source["rel"],
            "occurrence_ids": occurrence_ids,
            "resolution": resolution,
            "ordered_member_refs": [
                expander.object_ref(ref) for ref in source.get("members", [])
            ],
        }
        if "src" in source:
            binding["source"] = expander.object_ref(source["src"])
        if first["effective_target_kind"] == "external_or_historical":
            override = source.get(
                "target", ["external", first["effective_target_rule_id"]]
            )
            binding["target_override"] = expander.object_ref(override)
        result.append(binding)
    expected_order = [
        occurrence["occurrence_id"]
        for rule in task["rules"]
        for occurrence in rule["references"]
    ]
    order = {occurrence_id: index for index, occurrence_id in enumerate(expected_order)}
    result.sort(key=lambda item: order[item["occurrence_ids"][0]])
    observed = [
        occurrence_id for item in result for occurrence_id in item["occurrence_ids"]
    ]
    if observed != expected_order:
        missing = [item for item in expected_order if item not in observed]
        raise AuthoringError(
            "Authoring refs plus mechanical refs must cover task occurrences in source order; "
            f"missing={missing}"
        )
    return result


def _infer_reason_symbols(
    semantic_payload: Mapping[str, Any], symbols: list[dict[str, Any]]
) -> None:
    declared = {symbol["symbol_id"] for symbol in symbols}
    refs_by_reason: dict[str, list[dict[str, str]]] = defaultdict(list)
    corpus_view = {
        "symbol_registry": {"symbols": []},
        "semantic_units": semantic_payload["semantic_units"],
        "exceptions": semantic_payload["exceptions"],
        "tables": semantic_payload["tables"],
        "figures": [],
        "examples": [],
        "correction_applications": [],
        "references": [],
        "records": [],
        "dependency_edges": [],
    }
    for item in assembler._iter_addressable(corpus_view):
        if item.kind != "statement":
            continue
        reason_code = item.value.get("reason_code")
        if isinstance(reason_code, str):
            refs_by_reason[reason_code].append(
                {"kind": "statement", "id": item.object_id}
            )
    for reason_code, refs in sorted(refs_by_reason.items()):
        if reason_code in declared:
            continue
        phrase = reason_code.rsplit(".", 1)[-1].replace("_", " ")
        symbols.append(
            {
                "symbol_id": reason_code,
                "kind": "reason_code",
                "description": f"Reason emitted when {phrase}.",
                "arguments": [],
                "returns": "reason_code",
                "grounding": {
                    "kind": "composition",
                    "refs": refs,
                    "primitive": None,
                },
            }
        )


def expand_authoring(
    authoring: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    schema_path: Path = DEFAULT_SCHEMA,
) -> dict[str, Any]:
    validate_task(task)
    _validate_authoring(authoring, schema_path)
    if authoring["task_id"] != task["task_id"]:
        raise AuthoringError("Authoring task_id differs from compact task")
    expander = Expander(task)
    expander.register_ids(authoring)
    scaffold = scaffold_delta(dict(task))
    mechanical_figures = (
        task_asset_figures(task, load_asset_scaffold())
        if authoring.get("mechanical_assets", False)
        else []
    )
    mechanical_compiled_clause_ids = {
        clause_id
        for figure in mechanical_figures
        for clause_id in figure["clause_ids"]
    }
    authored_figures = [expander.figure(figure) for figure in authoring["figures"]]
    authored_figure_clauses = {
        clause_id
        for figure in authored_figures
        for clause_id in figure["clause_ids"]
    }
    duplicate_asset_clauses = (
        mechanical_compiled_clause_ids.intersection(authored_figure_clauses)
    )
    if duplicate_asset_clauses:
        raise AuthoringError(
            "Mechanically generated assets must not be reauthored: "
            f"{sorted(duplicate_asset_clauses)}"
        )
    semantic_payload: dict[str, Any] = {
        "symbol_declarations": expander.symbols(authoring["symbols"]),
        "semantic_units": [expander.unit(unit) for unit in authoring["units"]],
        "exceptions": [
            expander.exception(exception) for exception in authoring["exceptions"]
        ],
        "tables": [expander.table(table) for table in authoring["tables"]],
        "figures": [*mechanical_figures, *authored_figures],
        "examples": [expander.example(example) for example in authoring["examples"]],
        "correction_applications": [
            expander.correction(correction) for correction in authoring["corrections"]
        ],
    }
    _infer_reason_symbols(
        semantic_payload, semantic_payload["symbol_declarations"]
    )
    delta: dict[str, Any] = {
        **semantic_payload,
        "clause_dispositions": _dispositions(
            authoring,
            expander,
            semantic_payload,
            scaffold,
            mechanical_compiled_clause_ids,
        ),
        "citation_bindings": _citation_bindings(
            authoring["refs"], expander, task, scaffold
        ),
        "additional_references": list(authoring["additional_refs"]),
    }
    finalize_delta(delta, dict(task))
    return delta


def compile_authoring(
    authoring: Mapping[str, Any],
    task: Mapping[str, Any],
    *,
    schema_path: Path = DEFAULT_SCHEMA,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    delta = expand_authoring(authoring, task, schema_path=schema_path)
    chunk, report = compile_delta(delta, dict(task))
    return delta, chunk, report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Expand compact semantic authoring into a strict normalized delta"
    )
    parser.add_argument("authoring", type=Path)
    parser.add_argument("--task", type=Path)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--delta-output", type=Path)
    parser.add_argument("--chunk-output", type=Path)
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        authoring = load_json(args.authoring)
        task_path = args.task or DEFAULT_TASK_DIR / f"{authoring['task_id']}.json"
        task = load_json(task_path)
        delta, chunk, report = compile_authoring(
            authoring, task, schema_path=args.schema
        )
        if not report["passed"]:
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 1
        delta_output = args.delta_output or DEFAULT_DELTA_DIR / f"{task['task_id']}.json"
        chunk_output = args.chunk_output or DEFAULT_CHUNK_DIR / f"{task['task_id']}.json"
        delta_bytes = canonical_json_bytes(delta)
        chunk_bytes = chunk_validator.canonical_json_bytes(chunk)
        if args.check_only:
            if not delta_output.exists() or delta_output.read_bytes() != delta_bytes:
                raise AuthoringError(f"Delta output is missing or stale: {delta_output}")
            if not chunk_output.exists() or chunk_output.read_bytes() != chunk_bytes:
                raise AuthoringError(f"Chunk output is missing or stale: {chunk_output}")
        else:
            delta_output.parent.mkdir(parents=True, exist_ok=True)
            chunk_output.parent.mkdir(parents=True, exist_ok=True)
            delta_output.write_bytes(delta_bytes)
            chunk_output.write_bytes(chunk_bytes)
        output = {
            "passed": True,
            "task_id": task["task_id"],
            "authoring_bytes": len(args.authoring.read_bytes()),
            "delta_bytes": len(delta_bytes),
            "reduction_percent": round(
                100 * (1 - len(args.authoring.read_bytes()) / max(len(delta_bytes), 1)), 2
            ),
            "delta_sha256": delta["delta_sha256"],
            "chunk_sha256": chunk["chunk_sha256"],
        }
        print(json.dumps(output, indent=2))
        return 0
    except (AuthoringError, KeyError, TypeError, ValueError, OSError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
