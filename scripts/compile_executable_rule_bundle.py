from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

if __package__:
    from iupac_rule_runtime.runtime import canonical_json_bytes, digest_without_field
    from scripts import assemble_normalized_rule_corpus as corpus_assembler
else:
    import sys

    ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT_FOR_IMPORT))
    from iupac_rule_runtime.runtime import canonical_json_bytes, digest_without_field
    from scripts import assemble_normalized_rule_corpus as corpus_assembler


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "data" / "executable_rule_bundle.schema.json"
NORMALIZED_SCHEMA = ROOT / "data" / "normalized_rule_language.schema.json"
DEFAULT_OUTPUT = ROOT / "dist" / "bluebook_v3_executable_rules.json"

EXECUTABLE_SYMBOL_KINDS = {"predicate", "function", "transformation", "comparator"}
EXPRESSION_CHILDREN = ("from", "arg", "left", "right", "in", "where", "key")
STATEMENT_EXPRESSIONS = ("when", "value", "in", "stop_when", "assertion")
STATEMENT_BLOCKS = ("steps", "then", "else", "body")


class BundleCompileError(ValueError):
    pass


def normalized_source_digest(value: Mapping[str, Any], field_name: str) -> str:
    payload = dict(value)
    payload.pop(field_name, None)
    serialized = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest().upper()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BundleCompileError(f"JSON root must be an object: {path}")
    return value


def _iter_expressions(value: Any) -> Iterator[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return
    if isinstance(value.get("op"), str) and isinstance(value.get("expression_id"), str):
        yield value
    for key in EXPRESSION_CHILDREN:
        yield from _iter_expressions(value.get(key))
    for child in value.get("args", []) if isinstance(value.get("args"), list) else []:
        yield from _iter_expressions(child)


def _iter_statements(value: Any) -> Iterator[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return
    if isinstance(value.get("op"), str) and isinstance(value.get("statement_id"), str):
        yield value
    for key in STATEMENT_BLOCKS:
        for child in value.get(key, []) if isinstance(value.get(key), list) else []:
            yield from _iter_statements(child)


def _statement_expressions(value: Any) -> Iterator[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return
    for key in STATEMENT_EXPRESSIONS:
        yield from _iter_expressions(value.get(key))
    for child in value.get("args", []) if isinstance(value.get("args"), list) else []:
        yield from _iter_expressions(child)
    bindings = value.get("bindings")
    if isinstance(bindings, Mapping):
        for expression in bindings.values():
            yield from _iter_expressions(expression)
    for key in STATEMENT_BLOCKS:
        for child in value.get(key, []) if isinstance(value.get(key), list) else []:
            yield from _statement_expressions(child)


def _program_expressions(program: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    scope = program.get("scope")
    if isinstance(scope, Mapping):
        yield from _iter_expressions(scope.get("applies_to"))
    for key in ("when", "candidates", "value", "assertion"):
        yield from _iter_expressions(program.get(key))
    for key in ("then", "else", "steps", "on_violation"):
        for statement in program.get(key, []) if isinstance(program.get(key), list) else []:
            yield from _statement_expressions(statement)
    for stage in program.get("stages", []) if isinstance(program.get("stages"), list) else []:
        if isinstance(stage, Mapping):
            yield from _iter_expressions(stage.get("guard"))
            yield from _iter_expressions(stage.get("key"))


def _program_statements(program: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    for key in ("then", "else", "steps", "on_violation"):
        for statement in program.get(key, []) if isinstance(program.get(key), list) else []:
            yield from _iter_statements(statement)


def _unique_merge(
    sources: Sequence[Mapping[str, Any]], field: str, id_field: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: dict[str, Mapping[str, Any]] = {}
    for source in sources:
        values = source.get(field, [])
        if not isinstance(values, list):
            raise BundleCompileError(f"{field} must be an array")
        for raw in values:
            if not isinstance(raw, Mapping) or not isinstance(raw.get(id_field), str):
                raise BundleCompileError(f"{field} item has no {id_field}")
            object_id = str(raw[id_field])
            previous = seen.get(object_id)
            if previous is not None:
                if previous != raw:
                    raise BundleCompileError(f"Conflicting {id_field}: {object_id}")
                continue
            seen[object_id] = raw
            result.append(deepcopy(dict(raw)))
    return result


def _symbols(sources: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    adapted: list[Mapping[str, Any]] = []
    for source in sources:
        if source.get("format") == "iupac-bluebook-normalized-rule-language":
            registry = source.get("symbol_registry")
            if not isinstance(registry, Mapping):
                raise BundleCompileError("Normalized corpus has no symbol_registry")
            adapted.append({"symbols": registry.get("symbols", [])})
        else:
            adapted.append({"symbols": source.get("symbol_declarations", [])})
    return _unique_merge(adapted, "symbols", "symbol_id")


def _source_descriptor(source: Mapping[str, Any]) -> dict[str, Any]:
    source_format = source.get("format")
    if source_format == "iupac-bluebook-normalized-rule-language":
        expected_hash = normalized_source_digest(source, "corpus_sha256")
        if source.get("corpus_sha256") != expected_hash:
            raise BundleCompileError("Normalized corpus SHA-256 does not reproduce")
        return {
            "format": source_format,
            "format_version": source.get("format_version"),
            "content_sha256": source.get("corpus_sha256"),
            "task_id": None,
            "assigned_rule_ids": [
                record.get("source_rule_id") for record in source.get("records", [])
            ],
        }
    if source_format == "iupac-bluebook-normalized-rule-chunk":
        expected_hash = normalized_source_digest(source, "chunk_sha256")
        if source.get("chunk_sha256") != expected_hash:
            raise BundleCompileError(
                f"Normalized chunk SHA-256 does not reproduce: {source.get('packet_id')}"
            )
        return {
            "format": source_format,
            "format_version": source.get("format_version"),
            "content_sha256": source.get("chunk_sha256"),
            "task_id": source.get("packet_id"),
            "assigned_rule_ids": source.get("assigned_rule_ids", []),
        }
    raise BundleCompileError(f"Unsupported input format: {source_format}")


def _entrypoints(
    records: Sequence[Mapping[str, Any]], programs: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    known = {str(program["unit_id"]) for program in programs}
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        source_rule_id = record.get("source_rule_id")
        if not isinstance(source_rule_id, str):
            continue
        program_ids = [
            str(program_id)
            for program_id in record.get("semantic_unit_ids", [])
            if str(program_id) in known
        ]
        if not program_ids or source_rule_id in seen:
            continue
        seen.add(source_rule_id)
        result.append(
            {
                "entrypoint_id": source_rule_id,
                "program_ids": program_ids,
                "input_bindings": _merge_bindings(
                    program for program in programs if str(program["unit_id"]) in program_ids
                ),
            }
        )
    for program in programs:
        program_id = str(program["unit_id"])
        result.append(
            {
                "entrypoint_id": program_id,
                "program_ids": [program_id],
                "input_bindings": deepcopy(program.get("inputs", [])),
            }
        )
    return result


def _merge_bindings(programs: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: dict[str, str] = {}
    for program in programs:
        for binding in program.get("inputs", []):
            name = str(binding["name"])
            value_type = str(binding["type"])
            previous = seen.get(name)
            if previous is not None and previous != value_type:
                raise BundleCompileError(
                    f"Entrypoint input {name} has conflicting types {previous} and {value_type}"
                )
            if previous is None:
                seen[name] = value_type
                result.append({"name": name, "type": value_type})
    return result


def _required_capabilities(
    programs: Sequence[Mapping[str, Any]],
    exceptions: Sequence[Mapping[str, Any]],
    symbols: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    declarations = {str(symbol["symbol_id"]): symbol for symbol in symbols}
    used: dict[str, str] = {}

    def require(symbol_id: Any, kind: str) -> None:
        if not isinstance(symbol_id, str):
            raise BundleCompileError(f"{kind} operation has no symbol")
        previous = used.get(symbol_id)
        if previous is not None and previous != kind:
            raise BundleCompileError(
                f"Symbol {symbol_id} is used as both {previous} and {kind}"
            )
        used[symbol_id] = kind

    expressions: list[Mapping[str, Any]] = []
    for program in programs:
        expressions.extend(_program_expressions(program))
        for statement in _program_statements(program):
            if statement.get("op") == "transform":
                require(statement.get("transformation"), "transformation")
        for stage in program.get("stages", []):
            if isinstance(stage, Mapping):
                comparator = stage.get("comparator")
                if isinstance(comparator, Mapping) and (
                    comparator.get("kind") == "custom"
                    or comparator.get("direction") == "symbol_defined"
                ):
                    require(comparator.get("symbol"), "comparator")
    for exception in exceptions:
        expressions.extend(_iter_expressions(exception.get("when")))
        effect = exception.get("effect")
        if isinstance(effect, Mapping):
            expressions.extend(_iter_expressions(effect.get("guard")))
    for expression in expressions:
        if expression.get("op") in {"predicate", "function"}:
            require(expression.get("symbol"), str(expression["op"]))

    requirements = []
    for symbol_id, expected_kind in sorted(used.items()):
        declaration = declarations.get(symbol_id)
        if declaration is None:
            raise BundleCompileError(f"Used symbol is undeclared: {symbol_id}")
        if declaration.get("kind") != expected_kind:
            raise BundleCompileError(
                f"Symbol {symbol_id} is declared as {declaration.get('kind')} but used as {expected_kind}"
            )
        requirements.append(
            {
                "symbol_id": symbol_id,
                "kind": expected_kind,
                "arguments": deepcopy(declaration.get("arguments", [])),
                "returns": declaration.get("returns"),
                "description": declaration.get("description"),
            }
        )
    return requirements


def _validate_links(
    programs: Sequence[Mapping[str, Any]],
    tables: Sequence[Mapping[str, Any]],
    entrypoints: Sequence[Mapping[str, Any]],
    *,
    complete: bool,
) -> list[str]:
    known_tables = {str(table["table_id"]) for table in tables}
    known_targets = {str(program["unit_id"]) for program in programs}
    known_targets.update(str(item["entrypoint_id"]) for item in entrypoints)
    external: set[str] = set()
    for program in programs:
        for expression in _program_expressions(program):
            if expression.get("op") == "table_lookup" and expression.get("table_id") not in known_tables:
                raise BundleCompileError(f"Unknown table lookup target: {expression.get('table_id')}")
            if expression.get("op") == "rule_outcome":
                target = str(expression.get("rule_id"))
                if target not in known_targets:
                    external.add(target)
        for statement in _program_statements(program):
            if statement.get("op") == "invoke":
                target = str(statement.get("rule_id"))
                if target not in known_targets:
                    external.add(target)
    if complete and external:
        raise BundleCompileError(
            "Complete corpus has unresolved runtime rule dependencies: " + ", ".join(sorted(external))
        )
    return sorted(external)


def _type_registry(
    programs: Sequence[Mapping[str, Any]],
    symbols: Sequence[Mapping[str, Any]],
    tables: Sequence[Mapping[str, Any]],
) -> list[str]:
    types: set[str] = set()
    for program in programs:
        for binding in [*program.get("inputs", []), *program.get("outputs", [])]:
            if isinstance(binding, Mapping) and isinstance(binding.get("type"), str):
                types.add(str(binding["type"]))
        if isinstance(program.get("entity_type"), str):
            types.add(str(program["entity_type"]))
    for symbol in symbols:
        if isinstance(symbol.get("returns"), str):
            types.add(str(symbol["returns"]))
        for binding in symbol.get("arguments", []):
            if isinstance(binding, Mapping) and isinstance(binding.get("type"), str):
                types.add(str(binding["type"]))
    for table in tables:
        for column in table.get("columns", []):
            if isinstance(column, Mapping) and isinstance(column.get("value_type"), str):
                types.add(str(column["value_type"]))
    return sorted(types)


def _source_dependencies(sources: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        for edge in source.get("dependency_edges", []):
            if not isinstance(edge, Mapping):
                continue
            edge_id = str(edge.get("edge_id"))
            if edge_id in seen:
                continue
            seen.add(edge_id)
            result.append(deepcopy(dict(edge)))
    return result


def _reject_addressable_collisions(
    programs: Sequence[Mapping[str, Any]],
    exceptions: Sequence[Mapping[str, Any]],
    tables: Sequence[Mapping[str, Any]],
) -> None:
    seen: dict[str, str] = {}

    def add(object_id: Any, kind: str) -> None:
        if not isinstance(object_id, str):
            return
        previous = seen.get(object_id)
        if previous is not None:
            raise BundleCompileError(
                f"Addressable object ID collision: {object_id} ({previous}, {kind})"
            )
        seen[object_id] = kind

    for program in programs:
        add(program.get("unit_id"), "semantic_unit")
        for expression in _program_expressions(program):
            add(expression.get("expression_id"), "expression")
        for statement in _program_statements(program):
            add(statement.get("statement_id"), "statement")
        for stage in program.get("stages", []):
            if isinstance(stage, Mapping):
                add(stage.get("stage_id"), "decision_stage")
    for exception in exceptions:
        add(exception.get("exception_id"), "exception")
        for expression in _iter_expressions(exception.get("when")):
            add(expression.get("expression_id"), "expression")
        effect = exception.get("effect")
        if isinstance(effect, Mapping):
            for expression in _iter_expressions(effect.get("guard")):
                add(expression.get("expression_id"), "expression")
    for table in tables:
        add(table.get("table_id"), "table")
        for column in table.get("columns", []):
            if isinstance(column, Mapping):
                add(column.get("column_id"), "table_column")
        for row in table.get("rows", []):
            if not isinstance(row, Mapping):
                continue
            add(row.get("row_id"), "table_row")
            for cell in row.get("cells", []):
                if isinstance(cell, Mapping):
                    add(cell.get("cell_id"), "table_cell")
        for footnote in table.get("footnotes", []):
            if isinstance(footnote, Mapping):
                add(footnote.get("footnote_id"), "table_footnote")


def compile_bundle(
    sources: Sequence[Mapping[str, Any]], *, allow_partial: bool = False
) -> dict[str, Any]:
    if not sources:
        raise BundleCompileError("At least one normalized corpus or chunk is required")
    descriptors = [_source_descriptor(source) for source in sources]
    complete = len(sources) == 1 and sources[0].get("format") == (
        "iupac-bluebook-normalized-rule-language"
    )
    if not complete and not allow_partial:
        raise BundleCompileError(
            "Chunk inputs are incomplete; pass allow_partial=True or --allow-partial explicitly"
        )
    if complete:
        normalized_schema = load_json(NORMALIZED_SCHEMA)
        try:
            corpus_assembler.validate_rule_corpus(
                sources[0], normalized_schema, NORMALIZED_SCHEMA
            )
        except corpus_assembler.AssemblyError as error:
            raise BundleCompileError(
                f"Complete normalized corpus validation failed: {error}"
            ) from error

    programs = _unique_merge(sources, "semantic_units", "unit_id")
    exceptions = _unique_merge(sources, "exceptions", "exception_id")
    tables = _unique_merge(sources, "tables", "table_id")
    records = _unique_merge(sources, "records", "record_id")
    symbols = _symbols(sources)
    _reject_addressable_collisions(programs, exceptions, tables)
    entrypoints = _entrypoints(records, programs)
    requirements = _required_capabilities(programs, exceptions, symbols)
    external_dependencies = _validate_links(
        programs, tables, entrypoints, complete=complete
    )

    exceptions.sort(
        key=lambda item: (
            -int(item["precedence"]["specificity"]),
            int(item["precedence"]["source_order"]),
            str(item["exception_id"]),
        )
    )
    bundle: dict[str, Any] = {
        "format": "iupac-bluebook-executable-rule-bundle",
        "format_version": "1.0.0",
        "execution_model": "ordered-if-then-v1",
        "complete": complete,
        "sources": descriptors,
        "type_registry": _type_registry(programs, symbols, tables),
        "capability_contract": {
            "required": requirements,
            "external_rule_dependencies": external_dependencies,
        },
        "entrypoints": entrypoints,
        "execution_order": [str(program["unit_id"]) for program in programs],
        "programs": programs,
        "exceptions": exceptions,
        "tables": tables,
        "dependency_edges": _source_dependencies(sources),
        "metrics": {
            "source_count": len(sources),
            "entrypoint_count": len(entrypoints),
            "program_count": len(programs),
            "exception_count": len(exceptions),
            "table_count": len(tables),
            "required_capability_count": len(requirements),
            "external_rule_dependency_count": len(external_dependencies),
            "dependency_edge_count": len(_source_dependencies(sources)),
        },
    }
    bundle["bundle_sha256"] = digest_without_field(bundle, "bundle_sha256")
    return bundle


def validate_bundle(bundle: Mapping[str, Any], schema_path: Path = DEFAULT_SCHEMA) -> None:
    schema = load_json(schema_path)
    normalized_schema = load_json(NORMALIZED_SCHEMA)
    registry = Registry().with_resources(
        [
            (str(normalized_schema["$id"]), Resource.from_contents(normalized_schema)),
            (str(schema["$id"]), Resource.from_contents(schema)),
        ]
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(
        schema, registry=registry, format_checker=FormatChecker()
    )
    errors = sorted(
        validator.iter_errors(bundle),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        details = "; ".join(
            f"/{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in errors
        )
        raise BundleCompileError("Executable bundle schema validation failed: " + details)
    if bundle.get("bundle_sha256") != digest_without_field(bundle, "bundle_sha256"):
        raise BundleCompileError("Executable bundle SHA-256 does not reproduce")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile normalized Blue Book semantic IR into an executable rule bundle"
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        sources = [load_json(path) for path in args.inputs]
        bundle = compile_bundle(sources, allow_partial=args.allow_partial)
        validate_bundle(bundle, args.schema)
    except (BundleCompileError, KeyError, TypeError, ValueError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1
    report = {
        "passed": True,
        "complete": bundle["complete"],
        "metrics": bundle["metrics"],
        "bundle_sha256": bundle["bundle_sha256"],
    }
    if not args.check_only:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_json_bytes(bundle))
        report["output"] = str(args.output)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
