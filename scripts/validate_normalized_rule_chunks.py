from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable


ROOT = Path(__file__).resolve().parents[1]
CHUNK_SCHEMA_PATH = ROOT / "data" / "normalized_rule_chunk.schema.json"
LANGUAGE_SCHEMA_PATH = ROOT / "data" / "normalized_rule_language.schema.json"
PACKET_SCHEMA_PATH = ROOT / "data" / "bluebook_semantic_work_packet.schema.json"
DEFAULT_PACKET_DIR = ROOT / "work" / "semantic_packets"
DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"

SOURCE_HASH_FIELDS = (
    "source_corpus_sha256",
    "document_nodes_sha256",
    "correction_overlays_sha256",
    "clause_inventory_sha256",
    "reference_occurrences_sha256",
    "reference_resolutions_sha256",
)
RECORD_LINKS = {
    "semantic_unit_ids": "semantic_unit",
    "exception_ids": "exception",
    "table_ids": "table",
    "figure_ids": "figure",
    "example_ids": "example",
    "correction_application_ids": "correction_application",
    "reference_ids": "reference",
}
TOP_LEVEL_OBJECTS = {
    "semantic_unit": "semantic_units",
    "exception": "exceptions",
    "table": "tables",
    "figure": "figures",
    "example": "examples",
    "correction_application": "correction_applications",
    "reference": "references",
}
AST_KINDS = {"expression", "statement", "decision_stage"}
FORBIDDEN_SEMANTIC_RE = re.compile(
    r"\b(?:todo|unresolved|placeholder|not_started|manual_review)\b|"
    r"action:apply_[a-z0-9_]+_rule|"
    r"candidate:satisfies_stated_preference_criterion",
    re.IGNORECASE,
)


class DuplicateKeyError(ValueError):
    pass


@dataclass(frozen=True)
class IndexedObject:
    kind: str
    object_id: str
    path: tuple[str | int, ...]
    value: Mapping[str, Any]


class Audit:
    def __init__(self) -> None:
        self.errors: list[dict[str, Any]] = []

    def fail(
        self,
        code: str,
        message: str,
        *,
        path: Sequence[str | int] = (),
        **context: Any,
    ) -> None:
        self.errors.append(
            {
                "code": code,
                "message": message,
                "path": json_pointer(path),
                "context": context,
            }
        )

    def require(
        self,
        condition: bool,
        code: str,
        message: str,
        *,
        path: Sequence[str | int] = (),
        **context: Any,
    ) -> None:
        if not condition:
            self.fail(code, message, path=path, **context)


def json_pointer(path: Sequence[str | int]) -> str:
    if not path:
        return ""
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
    return "/" + "/".join(escaped)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def digest_without_field(value: Mapping[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return sha256_bytes(canonical_json_bytes(payload))


def language_schema_sha256(schema_bytes: bytes | None = None) -> str:
    raw = LANGUAGE_SCHEMA_PATH.read_bytes() if schema_bytes is None else schema_bytes
    return sha256_bytes(raw)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is forbidden")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def parse_json_bytes(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8-sig"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )


def load_json(path: Path) -> Any:
    return parse_json_bytes(path.read_bytes())


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _iter_schema_refs(
    value: Any, path: tuple[str | int, ...] = ()
) -> Iterator[tuple[tuple[str | int, ...], str]]:
    if isinstance(value, Mapping):
        ref = value.get("$ref")
        if isinstance(ref, str):
            yield path + ("$ref",), ref
        for key, child in value.items():
            yield from _iter_schema_refs(child, path + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_schema_refs(child, path + (index,))


def build_schema_validator(
    chunk_schema: Mapping[str, Any],
    language_schema: Mapping[str, Any],
    audit: Audit | None = None,
) -> Draft202012Validator | None:
    target = audit or Audit()
    schemas = (
        ("chunk", chunk_schema),
        ("language", language_schema),
    )
    for label, schema in schemas:
        target.require(
            schema.get("$schema") == DRAFT_2020_12,
            "schema.dialect",
            f"{label} schema must declare JSON Schema Draft 2020-12",
            path=(label, "$schema"),
            actual=schema.get("$schema"),
        )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            target.fail(
                "schema.invalid",
                f"{label} schema is invalid: {error.message}",
                path=(label, *error.absolute_path),
            )

    chunk_id = chunk_schema.get("$id")
    language_id = language_schema.get("$id")
    if not isinstance(chunk_id, str) or not isinstance(language_id, str):
        target.fail(
            "schema.id",
            "Both schemas must have absolute string $id values",
        )
        return None

    try:
        registry = Registry().with_resources(
            [
                (chunk_id, Resource.from_contents(chunk_schema)),
                (language_id, Resource.from_contents(language_schema)),
            ]
        )
    except Exception as error:  # referencing reports several specification errors
        target.fail("schema.registry", f"Could not register schemas: {error}")
        return None

    for label, schema, base_uri in (
        ("chunk", chunk_schema, chunk_id),
        ("language", language_schema, language_id),
    ):
        resolver = registry.resolver(base_uri=base_uri)
        for path, ref in _iter_schema_refs(schema):
            try:
                resolver.lookup(ref)
            except Unresolvable as error:
                target.fail(
                    "schema.ref",
                    f"Unresolvable {label} schema reference {ref!r}: {error}",
                    path=(label, *path),
                    ref=ref,
                )

    if target.errors:
        return None
    return Draft202012Validator(
        chunk_schema,
        registry=registry,
        format_checker=FormatChecker(),
    )


def _schema_validate(
    audit: Audit, validator: Draft202012Validator, chunk: Mapping[str, Any]
) -> None:
    errors = sorted(
        validator.iter_errors(chunk),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    for error in errors:
        audit.fail(
            "chunk.schema",
            error.message,
            path=tuple(error.absolute_path),
            validator=error.validator,
        )


def _packet_schema_validate(
    audit: Audit,
    packet_schema: Mapping[str, Any],
    packet: Mapping[str, Any],
) -> None:
    audit.require(
        packet_schema.get("$schema") == DRAFT_2020_12,
        "packet_schema.dialect",
        "Packet schema must declare JSON Schema Draft 2020-12",
        path=("packet_schema", "$schema"),
        actual=packet_schema.get("$schema"),
    )
    try:
        Draft202012Validator.check_schema(packet_schema)
    except SchemaError as error:
        audit.fail(
            "packet_schema.invalid",
            f"Packet schema is invalid: {error.message}",
            path=("packet_schema", *error.absolute_path),
        )
        return
    validator = Draft202012Validator(
        packet_schema,
        format_checker=FormatChecker(),
    )
    errors = sorted(
        validator.iter_errors(packet),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    for error in errors:
        audit.fail(
            "packet.schema",
            error.message,
            path=tuple(error.absolute_path),
            validator=error.validator,
        )


def _validate_forbidden_markers(audit: Audit, chunk: Mapping[str, Any]) -> None:
    serialized = json.dumps(chunk, ensure_ascii=False, sort_keys=True)
    matches = sorted(
        {match.group(0) for match in FORBIDDEN_SEMANTIC_RE.finditer(serialized)}
    )
    for value in matches:
        audit.fail(
            "chunk.forbidden_marker",
            "Completed semantic chunks cannot contain review markers or generic fallbacks",
            value=value,
        )


def _validate_snapshot(
    audit: Audit,
    chunk: Mapping[str, Any],
    packet: Mapping[str, Any],
    expected_schema_sha256: str,
) -> None:
    audit.require(
        chunk.get("packet_id") == packet.get("packet_id"),
        "snapshot.packet_id",
        "Chunk packet_id does not identify the supplied packet",
        path=("packet_id",),
        expected=packet.get("packet_id"),
        actual=chunk.get("packet_id"),
    )
    audit.require(
        chunk.get("assigned_rule_ids") == packet.get("assigned_rule_ids"),
        "snapshot.assigned_rules",
        "Chunk assignments differ from the packet snapshot or source order",
        path=("assigned_rule_ids",),
        expected=packet.get("assigned_rule_ids"),
        actual=chunk.get("assigned_rule_ids"),
    )
    for field in SOURCE_HASH_FIELDS:
        audit.require(
            chunk.get(field) == packet.get(field),
            "snapshot.source_hash",
            f"Chunk {field} differs from the packet snapshot",
            path=(field,),
            field=field,
            expected=packet.get(field),
            actual=chunk.get(field),
        )

    packet_hash = packet.get("packet_sha256")
    if isinstance(packet_hash, str):
        expected_packet_hash = digest_without_field(packet, "packet_sha256")
        audit.require(
            packet_hash == expected_packet_hash,
            "hash.packet",
            "Packet SHA-256 does not reproduce from canonical packet content",
            path=("packet_sha256",),
            expected=expected_packet_hash,
            actual=packet_hash,
        )
    else:
        audit.fail(
            "hash.packet",
            "Packet is missing packet_sha256",
            path=("packet_sha256",),
        )
    audit.require(
        chunk.get("packet_sha256") == packet_hash,
        "snapshot.packet_hash",
        "Chunk packet_sha256 differs from the packet snapshot",
        path=("packet_sha256",),
        expected=packet_hash,
        actual=chunk.get("packet_sha256"),
    )
    audit.require(
        chunk.get("schema_sha256") == expected_schema_sha256,
        "hash.schema",
        "Chunk schema_sha256 does not match normalized_rule_language.schema.json",
        path=("schema_sha256",),
        expected=expected_schema_sha256,
        actual=chunk.get("schema_sha256"),
    )


def _packet_source_layout(
    audit: Audit, packet: Mapping[str, Any]
) -> tuple[list[str], dict[str, dict[str, Any]], list[str], dict[str, str]]:
    assigned_rule_ids = [
        value for value in _list(packet.get("assigned_rule_ids")) if isinstance(value, str)
    ]
    assignments = _list(packet.get("assigned"))
    assignment_rule_ids = [
        _mapping(assignment).get("source_rule_id") for assignment in assignments
    ]
    audit.require(
        assignment_rule_ids == assigned_rule_ids,
        "packet.assignments",
        "Packet assignments must occur exactly once in assigned_rule_ids order",
        path=("assigned",),
        expected=assigned_rule_ids,
        actual=assignment_rule_ids,
    )

    records: dict[str, dict[str, Any]] = {}
    ordered_clauses: list[str] = []
    clause_owner: dict[str, str] = {}
    for index, raw_assignment in enumerate(assignments):
        assignment = _mapping(raw_assignment)
        rule_id = assignment.get("source_rule_id")
        inventory = _mapping(assignment.get("clause_inventory_record"))
        if not isinstance(rule_id, str):
            audit.fail(
                "packet.assignment",
                "Packet assignment has no source_rule_id",
                path=("assigned", index, "source_rule_id"),
            )
            continue
        source_units = _list(inventory.get("source_units"))
        clause_ids = [
            _mapping(unit).get("unit_id")
            for unit in source_units
            if isinstance(_mapping(unit).get("unit_id"), str)
        ]
        audit.require(
            bool(clause_ids),
            "packet.clauses",
            "Assigned clause inventory record has no source clauses",
            path=("assigned", index, "clause_inventory_record", "source_units"),
            source_rule_id=rule_id,
        )
        audit.require(
            inventory.get("source_rule_id") == rule_id,
            "packet.inventory_link",
            "Clause inventory record does not belong to its packet assignment",
            path=("assigned", index, "clause_inventory_record", "source_rule_id"),
            expected=rule_id,
            actual=inventory.get("source_rule_id"),
        )
        if rule_id in records:
            audit.fail(
                "packet.assignment_duplicate",
                "Packet assigns a source rule more than once",
                path=("assigned", index),
                source_rule_id=rule_id,
            )
        records[rule_id] = {
            "record_id": inventory.get("record_id"),
            "clause_ids": clause_ids,
        }
        for clause_id in clause_ids:
            if clause_id in clause_owner:
                audit.fail(
                    "packet.clause_duplicate",
                    "Source clause occurs in more than one packet record",
                    path=("assigned", index, "clause_inventory_record", "source_units"),
                    clause_id=clause_id,
                    first_owner=clause_owner[clause_id],
                    second_owner=rule_id,
                )
            else:
                clause_owner[clause_id] = rule_id
                ordered_clauses.append(clause_id)
    return assigned_rule_ids, records, ordered_clauses, clause_owner


def _validate_coverage(
    audit: Audit,
    chunk: Mapping[str, Any],
    packet: Mapping[str, Any],
) -> tuple[set[str], dict[str, str]]:
    assigned_rule_ids, source_records, source_clauses, clause_owner = (
        _packet_source_layout(audit, packet)
    )
    chunk_records = _list(chunk.get("records"))
    record_rule_ids = [_mapping(record).get("source_rule_id") for record in chunk_records]
    audit.require(
        record_rule_ids == assigned_rule_ids,
        "coverage.records",
        "Chunk records must cover assigned rules exactly once in packet order",
        path=("records",),
        expected=assigned_rule_ids,
        actual=record_rule_ids,
    )
    for index, raw_record in enumerate(chunk_records):
        record = _mapping(raw_record)
        rule_id = record.get("source_rule_id")
        expected = source_records.get(rule_id) if isinstance(rule_id, str) else None
        if expected is None:
            continue
        audit.require(
            record.get("record_id") == expected["record_id"],
            "coverage.record_id",
            "Chunk record_id differs from its source inventory record",
            path=("records", index, "record_id"),
            source_rule_id=rule_id,
            expected=expected["record_id"],
            actual=record.get("record_id"),
        )
        audit.require(
            record.get("clause_ids") == expected["clause_ids"],
            "coverage.record_clauses",
            "Record clause_ids must exactly preserve source clause order and coverage",
            path=("records", index, "clause_ids"),
            source_rule_id=rule_id,
            expected=expected["clause_ids"],
            actual=record.get("clause_ids"),
        )

    dispositions = _list(chunk.get("clause_dispositions"))
    disposition_ids = [
        _mapping(disposition).get("clause_id") for disposition in dispositions
    ]
    counts = Counter(disposition_ids)
    expected_set = set(source_clauses)
    for clause_id in source_clauses:
        count = counts[clause_id]
        if count == 0:
            audit.fail(
                "coverage.disposition_missing",
                "Source clause has no coverage disposition",
                path=("clause_dispositions",),
                clause_id=clause_id,
            )
        elif count != 1:
            audit.fail(
                "coverage.disposition_duplicate",
                "Source clause must have exactly one coverage disposition",
                path=("clause_dispositions",),
                clause_id=clause_id,
                count=count,
            )
    for index, clause_id in enumerate(disposition_ids):
        if clause_id not in expected_set:
            audit.fail(
                "coverage.disposition_extra",
                "Coverage disposition names a clause outside the packet",
                path=("clause_dispositions", index, "clause_id"),
                clause_id=clause_id,
            )
    audit.require(
        disposition_ids == source_clauses,
        "coverage.disposition_order",
        "Clause dispositions must preserve packet source-clause order",
        path=("clause_dispositions",),
        expected=source_clauses,
        actual=disposition_ids,
    )
    return expected_set, clause_owner


def _walk_expression(
    expression: Any,
    path: tuple[str | int, ...],
    add: Any,
) -> None:
    if not isinstance(expression, Mapping):
        return
    expression_id = expression.get("expression_id")
    if isinstance(expression_id, str):
        add("expression", expression_id, path, expression)
    for key in ("from", "arg", "left", "right", "in", "where", "key"):
        if key in expression:
            _walk_expression(expression[key], path + (key,), add)
    for index, child in enumerate(_list(expression.get("args"))):
        _walk_expression(child, path + ("args", index), add)


def _walk_statement(
    statement: Any,
    path: tuple[str | int, ...],
    add: Any,
) -> None:
    if not isinstance(statement, Mapping):
        return
    statement_id = statement.get("statement_id")
    if isinstance(statement_id, str):
        add("statement", statement_id, path, statement)
    for key in ("when", "value", "in", "stop_when", "assertion"):
        if key in statement:
            _walk_expression(statement[key], path + (key,), add)
    for index, expression in enumerate(_list(statement.get("args"))):
        _walk_expression(expression, path + ("args", index), add)
    bindings = _mapping(statement.get("bindings"))
    for name, expression in bindings.items():
        _walk_expression(expression, path + ("bindings", name), add)
    for key in ("steps", "then", "else", "body"):
        for index, child in enumerate(_list(statement.get(key))):
            _walk_statement(child, path + (key, index), add)


def _build_object_index(
    audit: Audit, chunk: Mapping[str, Any]
) -> tuple[
    dict[str, dict[str, list[IndexedObject]]],
    dict[str, list[IndexedObject]],
]:
    by_kind: dict[str, dict[str, list[IndexedObject]]] = defaultdict(
        lambda: defaultdict(list)
    )
    by_id: dict[str, list[IndexedObject]] = defaultdict(list)

    def add(
        kind: str,
        object_id: str,
        path: tuple[str | int, ...],
        value: Mapping[str, Any],
    ) -> None:
        entry = IndexedObject(kind, object_id, path, value)
        by_kind[kind][object_id].append(entry)
        by_id[object_id].append(entry)

    simple_arrays = (
        ("records", "record", "record_id"),
        ("symbol_declarations", "symbol", "symbol_id"),
        ("exceptions", "exception", "exception_id"),
        ("figures", "figure", "figure_id"),
        ("examples", "example", "example_id"),
        ("correction_applications", "correction_application", "application_id"),
        ("references", "reference", "reference_id"),
        ("dependency_edges", "dependency_edge", "edge_id"),
    )
    for array_name, kind, id_field in simple_arrays:
        for index, raw_item in enumerate(_list(chunk.get(array_name))):
            item = _mapping(raw_item)
            object_id = item.get(id_field)
            if isinstance(object_id, str):
                add(kind, object_id, (array_name, index), item)

    for unit_index, raw_unit in enumerate(_list(chunk.get("semantic_units"))):
        unit = _mapping(raw_unit)
        unit_path: tuple[str | int, ...] = ("semantic_units", unit_index)
        unit_id = unit.get("unit_id")
        if isinstance(unit_id, str):
            add("semantic_unit", unit_id, unit_path, unit)
        scope = _mapping(unit.get("scope"))
        _walk_expression(scope.get("applies_to"), unit_path + ("scope", "applies_to"), add)
        for key in ("when", "candidates", "value", "assertion"):
            if key in unit:
                _walk_expression(unit[key], unit_path + (key,), add)
        for key in ("then", "else", "steps", "on_violation"):
            for index, statement in enumerate(_list(unit.get(key))):
                _walk_statement(statement, unit_path + (key, index), add)
        for stage_index, raw_stage in enumerate(_list(unit.get("stages"))):
            stage = _mapping(raw_stage)
            stage_path = unit_path + ("stages", stage_index)
            stage_id = stage.get("stage_id")
            if isinstance(stage_id, str):
                add("decision_stage", stage_id, stage_path, stage)
            _walk_expression(stage.get("guard"), stage_path + ("guard",), add)
            _walk_expression(stage.get("key"), stage_path + ("key",), add)

    for exception_index, raw_exception in enumerate(_list(chunk.get("exceptions"))):
        exception = _mapping(raw_exception)
        exception_path: tuple[str | int, ...] = ("exceptions", exception_index)
        _walk_expression(exception.get("when"), exception_path + ("when",), add)
        effect = _mapping(exception.get("effect"))
        _walk_expression(effect.get("guard"), exception_path + ("effect", "guard"), add)

    for table_index, raw_table in enumerate(_list(chunk.get("tables"))):
        table = _mapping(raw_table)
        table_path: tuple[str | int, ...] = ("tables", table_index)
        table_id = table.get("table_id")
        if isinstance(table_id, str):
            add("table", table_id, table_path, table)
        for column_index, raw_column in enumerate(_list(table.get("columns"))):
            column = _mapping(raw_column)
            column_id = column.get("column_id")
            if isinstance(column_id, str):
                add(
                    "table_column",
                    column_id,
                    table_path + ("columns", column_index),
                    column,
                )
        for row_index, raw_row in enumerate(_list(table.get("rows"))):
            row = _mapping(raw_row)
            row_path = table_path + ("rows", row_index)
            row_id = row.get("row_id")
            if isinstance(row_id, str):
                add("table_row", row_id, row_path, row)
            for cell_index, raw_cell in enumerate(_list(row.get("cells"))):
                cell = _mapping(raw_cell)
                cell_id = cell.get("cell_id")
                if isinstance(cell_id, str):
                    add(
                        "table_cell",
                        cell_id,
                        row_path + ("cells", cell_index),
                        cell,
                    )
        for footnote_index, raw_footnote in enumerate(_list(table.get("footnotes"))):
            footnote = _mapping(raw_footnote)
            footnote_id = footnote.get("footnote_id")
            if isinstance(footnote_id, str):
                add(
                    "table_footnote",
                    footnote_id,
                    table_path + ("footnotes", footnote_index),
                    footnote,
                )

    for object_id, entries in by_id.items():
        if len(entries) > 1:
            audit.fail(
                "id.duplicate",
                "Addressable object id is not globally unique",
                path=entries[1].path,
                object_id=object_id,
                locations=[json_pointer(entry.path) for entry in entries],
                kinds=[entry.kind for entry in entries],
            )
            if any(entry.kind in AST_KINDS for entry in entries):
                audit.fail(
                    "ast.addressability",
                    "AST node id cannot identify exactly one nested node",
                    path=entries[1].path,
                    object_id=object_id,
                )
    return by_kind, by_id


def _iter_object_refs(
    chunk: Mapping[str, Any],
) -> Iterator[tuple[Mapping[str, Any], tuple[str | int, ...], str | None]]:
    for index, raw_symbol in enumerate(_list(chunk.get("symbol_declarations"))):
        grounding = _mapping(_mapping(raw_symbol).get("grounding"))
        for ref_index, ref in enumerate(_list(grounding.get("refs"))):
            yield _mapping(ref), (
                "symbol_declarations",
                index,
                "grounding",
                "refs",
                ref_index,
            ), None
    for index, raw_disposition in enumerate(_list(chunk.get("clause_dispositions"))):
        disposition = _mapping(raw_disposition)
        body = _mapping(disposition.get("disposition"))
        for ref_index, ref in enumerate(_list(body.get("targets"))):
            yield _mapping(ref), (
                "clause_dispositions",
                index,
                "disposition",
                "targets",
                ref_index,
            ), disposition.get("clause_id") if isinstance(disposition.get("clause_id"), str) else None
    for index, raw_unit in enumerate(_list(chunk.get("semantic_units"))):
        terminal = _mapping(_mapping(raw_unit).get("terminal_tie"))
        fallback = terminal.get("fallback_ref")
        if isinstance(fallback, Mapping):
            yield fallback, ("semantic_units", index, "terminal_tie", "fallback_ref"), None
    for index, raw_exception in enumerate(_list(chunk.get("exceptions"))):
        exception = _mapping(raw_exception)
        target = exception.get("target")
        if isinstance(target, Mapping):
            yield target, ("exceptions", index, "target"), None
        effect = _mapping(exception.get("effect"))
        for key in ("replacement", "redirect"):
            ref = effect.get(key)
            if isinstance(ref, Mapping):
                yield ref, ("exceptions", index, "effect", key), None
    for table_index, raw_table in enumerate(_list(chunk.get("tables"))):
        table = _mapping(raw_table)
        for footnote_index, raw_footnote in enumerate(_list(table.get("footnotes"))):
            footnote = _mapping(raw_footnote)
            for ref_index, ref in enumerate(_list(footnote.get("applies_to"))):
                yield _mapping(ref), (
                    "tables",
                    table_index,
                    "footnotes",
                    footnote_index,
                    "applies_to",
                    ref_index,
                ), None
    for index, raw_example in enumerate(_list(chunk.get("examples"))):
        for ref_index, ref in enumerate(_list(_mapping(raw_example).get("demonstrates"))):
            yield _mapping(ref), ("examples", index, "demonstrates", ref_index), None
    for index, raw_application in enumerate(_list(chunk.get("correction_applications"))):
        for ref_index, ref in enumerate(_list(_mapping(raw_application).get("target_refs"))):
            yield _mapping(ref), (
                "correction_applications",
                index,
                "target_refs",
                ref_index,
            ), None
    for index, raw_reference in enumerate(_list(chunk.get("references"))):
        reference = _mapping(raw_reference)
        for key in ("source", "target"):
            ref = reference.get(key)
            if isinstance(ref, Mapping):
                yield ref, ("references", index, key), None
        for ref_index, ref in enumerate(_list(reference.get("ordered_member_refs"))):
            yield _mapping(ref), (
                "references",
                index,
                "ordered_member_refs",
                ref_index,
            ), None
    for index, raw_edge in enumerate(_list(chunk.get("dependency_edges"))):
        edge = _mapping(raw_edge)
        for key in ("from", "to"):
            ref = edge.get(key)
            if isinstance(ref, Mapping):
                yield ref, ("dependency_edges", index, key), None


def _resolve_ref(
    ref: Mapping[str, Any],
    by_kind: dict[str, dict[str, list[IndexedObject]]],
    by_id: dict[str, list[IndexedObject]],
    source_clauses: set[str],
    chapters: set[str],
) -> IndexedObject | None:
    kind = ref.get("kind")
    object_id = ref.get("id")
    if not isinstance(kind, str) or not isinstance(object_id, str):
        return None
    if kind == "external":
        return IndexedObject(kind, object_id, (), ref)
    if kind == "chapter":
        return IndexedObject(kind, object_id, (), ref) if object_id in chapters else None
    if kind == "clause":
        return IndexedObject(kind, object_id, (), ref) if object_id in source_clauses else None
    matches = by_kind.get(kind, {}).get(object_id, [])
    if len(matches) == 1 and len(by_id.get(object_id, [])) == 1:
        return matches[0]
    return None


def _validate_refs(
    audit: Audit,
    chunk: Mapping[str, Any],
    by_kind: dict[str, dict[str, list[IndexedObject]]],
    by_id: dict[str, list[IndexedObject]],
    source_clauses: set[str],
) -> None:
    chapters = {
        f"chapter:{record.get('chapter')}"
        for record in map(_mapping, _list(chunk.get("records")))
        if isinstance(record.get("chapter"), str)
    }
    chapters.update(chapter.removeprefix("chapter:") for chapter in list(chapters))
    for ref, path, supporting_clause in _iter_object_refs(chunk):
        resolved = _resolve_ref(ref, by_kind, by_id, source_clauses, chapters)
        if resolved is None:
            object_id = ref.get("id")
            actual_kinds = [
                entry.kind for entry in by_id.get(object_id, [])
            ] if isinstance(object_id, str) else []
            audit.fail(
                "ref.unresolved",
                "Typed object reference does not resolve to exactly one object",
                path=path,
                kind=ref.get("kind"),
                object_id=object_id,
                actual_kinds=actual_kinds,
            )
            continue
        if supporting_clause is not None:
            target_clauses = resolved.value.get("clause_ids")
            if isinstance(target_clauses, list):
                audit.require(
                    supporting_clause in target_clauses,
                    "coverage.target_clause",
                    "Compiled disposition target is not grounded in its source clause",
                    path=path,
                    clause_id=supporting_clause,
                    target_id=resolved.object_id,
                )


def _iter_clause_lists(
    value: Any,
    path: tuple[str | int, ...] = (),
) -> Iterator[tuple[list[Any], tuple[str | int, ...]]]:
    if isinstance(value, Mapping):
        is_literal = value.get("op") == "literal" and "expression_id" in value
        for key, child in value.items():
            child_path = path + (key,)
            if key in {
                "clause_ids",
                "before_clause_ids",
                "after_clause_ids",
                "successor_clause_ids",
            } and isinstance(child, list):
                yield child, child_path
            elif not (is_literal and key == "value"):
                yield from _iter_clause_lists(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_clause_lists(child, path + (index,))


def _validate_clause_refs(
    audit: Audit, chunk: Mapping[str, Any], source_clauses: set[str]
) -> None:
    for clause_ids, path in _iter_clause_lists(chunk):
        for index, clause_id in enumerate(clause_ids):
            audit.require(
                clause_id in source_clauses,
                "coverage.unknown_clause",
                "Semantic object cites a clause outside the packet source snapshot",
                path=(*path, index),
                clause_id=clause_id,
            )


def _validate_record_links(
    audit: Audit,
    chunk: Mapping[str, Any],
    by_kind: dict[str, dict[str, list[IndexedObject]]],
) -> None:
    memberships: dict[tuple[str, str], list[str]] = defaultdict(list)
    for record_index, raw_record in enumerate(_list(chunk.get("records"))):
        record = _mapping(raw_record)
        record_clauses = set(_list(record.get("clause_ids")))
        record_id = record.get("record_id")
        for field, kind in RECORD_LINKS.items():
            for item_index, object_id in enumerate(_list(record.get(field))):
                entries = by_kind.get(kind, {}).get(object_id, [])
                if len(entries) != 1:
                    audit.fail(
                        "record.ref",
                        "Record member id does not resolve to exactly one top-level object",
                        path=("records", record_index, field, item_index),
                        kind=kind,
                        object_id=object_id,
                    )
                    continue
                memberships[(kind, object_id)].append(str(record_id))
                value = entries[0].value
                object_clauses: set[Any]
                if kind == "correction_application":
                    object_clauses = set(_list(value.get("before_clause_ids"))) | set(
                        _list(value.get("after_clause_ids"))
                    )
                else:
                    object_clauses = set(_list(value.get("clause_ids")))
                audit.require(
                    object_clauses <= record_clauses,
                    "record.ownership",
                    "Record-linked object cites clauses owned by a different record",
                    path=entries[0].path,
                    record_id=record_id,
                    object_id=object_id,
                    foreign_clauses=sorted(object_clauses - record_clauses),
                )

    for kind, array_name in TOP_LEVEL_OBJECTS.items():
        for raw_object in _list(chunk.get(array_name)):
            value = _mapping(raw_object)
            id_field = {
                "semantic_unit": "unit_id",
                "exception": "exception_id",
                "table": "table_id",
                "figure": "figure_id",
                "example": "example_id",
                "correction_application": "application_id",
                "reference": "reference_id",
            }[kind]
            object_id = value.get(id_field)
            owners = memberships.get((kind, object_id), [])
            audit.require(
                len(owners) == 1,
                "record.membership",
                "Every top-level semantic object must belong to exactly one record",
                path=(array_name,),
                kind=kind,
                object_id=object_id,
                owners=owners,
            )


def _require_symbol(
    audit: Audit,
    by_kind: dict[str, dict[str, list[IndexedObject]]],
    symbol_id: Any,
    expected_kind: str,
    path: tuple[str | int, ...],
) -> None:
    entries = by_kind.get("symbol", {}).get(symbol_id, [])
    if len(entries) != 1:
        audit.fail(
            "symbol.unresolved",
            "Semantic AST symbol is not declared exactly once in this chunk",
            path=path,
            symbol_id=symbol_id,
            expected_kind=expected_kind,
        )
        return
    audit.require(
        entries[0].value.get("kind") == expected_kind,
        "symbol.kind",
        "Semantic AST symbol declaration has the wrong kind",
        path=path,
        symbol_id=symbol_id,
        expected_kind=expected_kind,
        actual_kind=entries[0].value.get("kind"),
    )


def _known_rule_ids(chunk: Mapping[str, Any], packet: Mapping[str, Any]) -> set[str]:
    result = {
        value
        for value in _list(chunk.get("assigned_rule_ids"))
        if isinstance(value, str)
    }
    for record in map(_mapping, _list(packet.get("context_records"))):
        rule_id = record.get("source_rule_id")
        if isinstance(rule_id, str):
            result.add(rule_id)
    for edge in map(_mapping, _list(packet.get("relation_edges"))):
        for key in ("source", "target"):
            value = edge.get(key)
            if isinstance(value, str) and value.startswith("P-"):
                result.add(value)
    return result


def _validate_ast_and_tables(
    audit: Audit,
    chunk: Mapping[str, Any],
    packet: Mapping[str, Any],
    by_kind: dict[str, dict[str, list[IndexedObject]]],
) -> None:
    known_rules = _known_rule_ids(chunk, packet)
    for entry in [
        item
        for entries in by_kind.get("expression", {}).values()
        for item in entries
    ]:
        expression = entry.value
        op = expression.get("op")
        if op in {"predicate", "function"}:
            _require_symbol(
                audit,
                by_kind,
                expression.get("symbol"),
                str(op),
                entry.path + ("symbol",),
            )
        if op == "table_lookup":
            table_id = expression.get("table_id")
            tables = by_kind.get("table", {}).get(table_id, [])
            if len(tables) != 1:
                audit.fail(
                    "table.unresolved",
                    "table_lookup does not resolve to exactly one table",
                    path=entry.path + ("table_id",),
                    table_id=table_id,
                )
            else:
                column_ids = {
                    _mapping(column).get("column_id")
                    for column in _list(tables[0].value.get("columns"))
                }
                audit.require(
                    expression.get("column_id") in column_ids,
                    "table.column",
                    "table_lookup column_id is not a column of the selected table",
                    path=entry.path + ("column_id",),
                    table_id=table_id,
                    column_id=expression.get("column_id"),
                )
        if op == "rule_outcome":
            audit.require(
                expression.get("rule_id") in known_rules,
                "rule.unresolved",
                "rule_outcome references a rule absent from packet assignments and context",
                path=entry.path + ("rule_id",),
                rule_id=expression.get("rule_id"),
            )

    for entry in [
        item
        for entries in by_kind.get("statement", {}).values()
        for item in entries
    ]:
        statement = entry.value
        op = statement.get("op")
        if op == "transform":
            _require_symbol(
                audit,
                by_kind,
                statement.get("transformation"),
                "transformation",
                entry.path + ("transformation",),
            )
        if op in {"reject", "assert"}:
            _require_symbol(
                audit,
                by_kind,
                statement.get("reason_code"),
                "reason_code",
                entry.path + ("reason_code",),
            )
        if op == "invoke":
            audit.require(
                statement.get("rule_id") in known_rules,
                "rule.unresolved",
                "invoke references a rule absent from packet assignments and context",
                path=entry.path + ("rule_id",),
                rule_id=statement.get("rule_id"),
            )

    for unit_index, raw_unit in enumerate(_list(chunk.get("semantic_units"))):
        unit = _mapping(raw_unit)
        unit_path: tuple[str | int, ...] = ("semantic_units", unit_index)
        if unit.get("kind") == "mapping":
            audit.require(
                len(by_kind.get("table", {}).get(unit.get("table_id"), [])) == 1,
                "table.unresolved",
                "Mapping semantic unit table_id does not resolve",
                path=unit_path + ("table_id",),
                table_id=unit.get("table_id"),
            )
        stages = _list(unit.get("stages"))
        if stages:
            ordinals = [_mapping(stage).get("ordinal") for stage in stages]
            audit.require(
                ordinals == list(range(1, len(stages) + 1)),
                "decision.order",
                "Decision stages must use contiguous source order ordinals",
                path=unit_path + ("stages",),
                actual=ordinals,
            )
            stage_ids = [_mapping(stage).get("stage_id") for stage in stages]
            for stage_index, raw_stage in enumerate(stages):
                stage = _mapping(raw_stage)
                on_tie = _mapping(stage.get("on_tie"))
                expected_next = (
                    stage_ids[stage_index + 1] if stage_index + 1 < len(stage_ids) else None
                )
                audit.require(
                    on_tie.get("next_stage_id") == expected_next,
                    "decision.tie_edge",
                    "Decision on_tie edge must point to the immediately following stage",
                    path=unit_path + ("stages", stage_index, "on_tie", "next_stage_id"),
                    expected=expected_next,
                    actual=on_tie.get("next_stage_id"),
                )
                comparator = _mapping(stage.get("comparator"))
                symbol = comparator.get("symbol")
                table_id = comparator.get("table_id")
                if symbol is not None:
                    _require_symbol(
                        audit,
                        by_kind,
                        symbol,
                        "comparator",
                        unit_path + ("stages", stage_index, "comparator", "symbol"),
                    )
                if table_id is not None:
                    audit.require(
                        len(by_kind.get("table", {}).get(table_id, [])) == 1,
                        "table.unresolved",
                        "Decision comparator table_id does not resolve",
                        path=unit_path
                        + ("stages", stage_index, "comparator", "table_id"),
                        table_id=table_id,
                    )
            terminal = _mapping(unit.get("terminal_tie"))
            fallback = terminal.get("fallback_ref")
            audit.require(
                (terminal.get("mode") == "apply_fallback") == isinstance(fallback, Mapping),
                "decision.terminal_tie",
                "Only apply_fallback terminal ties may carry a fallback_ref",
                path=unit_path + ("terminal_tie",),
            )

    for table_index, raw_table in enumerate(_list(chunk.get("tables"))):
        table = _mapping(raw_table)
        table_path: tuple[str | int, ...] = ("tables", table_index)
        columns = list(map(_mapping, _list(table.get("columns"))))
        rows = list(map(_mapping, _list(table.get("rows"))))
        column_ids = [column.get("column_id") for column in columns]
        audit.require(
            [column.get("ordinal") for column in columns]
            == list(range(1, len(columns) + 1)),
            "table.column_order",
            "Table column ordinals must be contiguous and source ordered",
            path=table_path + ("columns",),
        )
        audit.require(
            [row.get("ordinal") for row in rows] == list(range(1, len(rows) + 1)),
            "table.row_order",
            "Table row ordinals must be contiguous and source ordered",
            path=table_path + ("rows",),
        )
        for row_index, row in enumerate(rows):
            cell_columns = [
                _mapping(cell).get("column_id") for cell in _list(row.get("cells"))
            ]
            audit.require(
                cell_columns == column_ids,
                "table.row_shape",
                "Each table row must contain one cell per column in column order",
                path=table_path + ("rows", row_index, "cells"),
                expected=column_ids,
                actual=cell_columns,
            )
        contract = _mapping(table.get("contract"))
        for field in ("key_column_ids", "result_column_ids"):
            for index, column_id in enumerate(_list(contract.get(field))):
                audit.require(
                    column_id in column_ids,
                    "table.contract_column",
                    "Table contract references an unknown column",
                    path=table_path + ("contract", field, index),
                    column_id=column_id,
                )


def _validate_exceptions(audit: Audit, chunk: Mapping[str, Any]) -> None:
    exceptions = list(map(_mapping, _list(chunk.get("exceptions"))))

    def precedence_key(exception: Mapping[str, Any]) -> tuple[int, int, str]:
        precedence = _mapping(exception.get("precedence"))
        specificity = precedence.get("specificity")
        source_order = precedence.get("source_order")
        return (
            -specificity if isinstance(specificity, int) else 0,
            source_order if isinstance(source_order, int) else 0,
            str(exception.get("exception_id", "")),
        )

    if exceptions and exceptions != sorted(exceptions, key=precedence_key):
        audit.fail(
            "exception.order",
            "Exceptions must be ordered by descending specificity, source order, then id",
            path=("exceptions",),
            actual=[exception.get("exception_id") for exception in exceptions],
            expected=[
                exception.get("exception_id")
                for exception in sorted(exceptions, key=precedence_key)
            ],
        )
    seen: dict[tuple[Any, Any, Any, Any], str] = {}
    for index, exception in enumerate(exceptions):
        target = _mapping(exception.get("target"))
        precedence = _mapping(exception.get("precedence"))
        key = (
            target.get("kind"),
            target.get("id"),
            precedence.get("specificity"),
            precedence.get("source_order"),
        )
        previous = seen.get(key)
        if previous is not None:
            audit.fail(
                "exception.precedence_duplicate",
                "Exceptions for one target cannot share an ambiguous precedence",
                path=("exceptions", index, "precedence"),
                previous_exception_id=previous,
                exception_id=exception.get("exception_id"),
            )
        else:
            seen[key] = str(exception.get("exception_id"))


def _validate_reference_edges(
    audit: Audit,
    chunk: Mapping[str, Any],
    by_id: dict[str, list[IndexedObject]],
) -> None:
    references = list(map(_mapping, _list(chunk.get("references"))))
    for index, reference in enumerate(references):
        target = _mapping(reference.get("target"))
        external = target.get("kind") == "external"
        audit.require(
            (reference.get("resolution") == "external") == external,
            "reference.resolution",
            "External resolution and external target kind must agree",
            path=("references", index, "resolution"),
        )
        members = _list(reference.get("ordered_member_refs"))
        member_keys = [
            (_mapping(member).get("kind"), _mapping(member).get("id"))
            for member in members
        ]
        audit.require(
            len(member_keys) == len(set(member_keys)),
            "reference.member_duplicate",
            "ordered_member_refs must not contain duplicate members",
            path=("references", index, "ordered_member_refs"),
        )
        if reference.get("resolution") == "range":
            audit.require(
                bool(members),
                "reference.range_members",
                "Range references require an ordered, resolved member list",
                path=("references", index, "ordered_member_refs"),
            )

    edges = list(map(_mapping, _list(chunk.get("dependency_edges"))))
    if not edges:
        return
    triples: dict[tuple[Any, Any, Any, Any, Any], str] = {}
    for index, edge in enumerate(edges):
        from_ref = _mapping(edge.get("from"))
        to_ref = _mapping(edge.get("to"))
        triple = (
            from_ref.get("kind"),
            from_ref.get("id"),
            edge.get("relation"),
            to_ref.get("kind"),
            to_ref.get("id"),
        )
        if triple in triples:
            audit.fail(
                "edge.duplicate",
                "Dependency graph contains a duplicate typed edge",
                path=("dependency_edges", index),
                first_edge_id=triples[triple],
                edge_id=edge.get("edge_id"),
            )
        else:
            triples[triple] = str(edge.get("edge_id"))
        for source_index, object_id in enumerate(
            _list(edge.get("derived_from_object_ids"))
        ):
            audit.require(
                len(by_id.get(object_id, [])) == 1,
                "edge.provenance",
                "Dependency edge provenance id is not uniquely addressable",
                path=("dependency_edges", index, "derived_from_object_ids", source_index),
                object_id=object_id,
            )

    for index, reference in enumerate(references):
        source = _mapping(reference.get("source"))
        target = _mapping(reference.get("target"))
        triple = (
            source.get("kind"),
            source.get("id"),
            reference.get("relation"),
            target.get("kind"),
            target.get("id"),
        )
        matching = [
            edge
            for edge in edges
            if (
                _mapping(edge.get("from")).get("kind"),
                _mapping(edge.get("from")).get("id"),
                edge.get("relation"),
                _mapping(edge.get("to")).get("kind"),
                _mapping(edge.get("to")).get("id"),
            )
            == triple
            and reference.get("reference_id")
            in _list(edge.get("derived_from_object_ids"))
        ]
        audit.require(
            len(matching) == 1,
            "edge.reference_projection",
            "Each reference must project to exactly one dependency edge",
            path=("references", index),
            reference_id=reference.get("reference_id"),
        )


def _expected_metrics(chunk: Mapping[str, Any]) -> dict[str, int]:
    dispositions = list(map(_mapping, _list(chunk.get("clause_dispositions"))))
    disposition_kinds = Counter(
        _mapping(disposition.get("disposition")).get("kind")
        for disposition in dispositions
    )
    metrics = {
        "record_count": len(_list(chunk.get("records"))),
        "clause_disposition_count": len(dispositions),
        "compiled_clause_count": disposition_kinds["compiled"],
        "nonoperative_clause_count": disposition_kinds["nonoperative"],
        "superseded_clause_count": disposition_kinds["superseded"],
        "semantic_unit_count": len(_list(chunk.get("semantic_units"))),
        "exception_count": len(_list(chunk.get("exceptions"))),
        "table_count": len(_list(chunk.get("tables"))),
        "figure_count": len(_list(chunk.get("figures"))),
        "example_count": len(_list(chunk.get("examples"))),
        "correction_application_count": len(
            _list(chunk.get("correction_applications"))
        ),
        "reference_count": len(_list(chunk.get("references"))),
        "symbol_declaration_count": len(_list(chunk.get("symbol_declarations"))),
    }
    if "dependency_edges" in chunk:
        metrics["dependency_edge_count"] = len(_list(chunk.get("dependency_edges")))
    return metrics


def _validate_metrics_and_hash(
    audit: Audit, chunk: Mapping[str, Any]
) -> dict[str, int]:
    expected_metrics = _expected_metrics(chunk)
    metrics = _mapping(chunk.get("chunk_metrics"))
    for field, expected in expected_metrics.items():
        if field in metrics or field != "dependency_edge_count":
            audit.require(
                metrics.get(field) == expected,
                "metrics.mismatch",
                "Chunk metric does not reproduce from chunk content",
                path=("chunk_metrics", field),
                field=field,
                expected=expected,
                actual=metrics.get(field),
            )
    try:
        expected_hash = digest_without_field(chunk, "chunk_sha256")
    except (TypeError, ValueError) as error:
        audit.fail("hash.chunk", f"Chunk cannot be canonically hashed: {error}")
    else:
        audit.require(
            chunk.get("chunk_sha256") == expected_hash,
            "hash.chunk",
            "Chunk SHA-256 does not reproduce from canonical chunk content",
            path=("chunk_sha256",),
            expected=expected_hash,
            actual=chunk.get("chunk_sha256"),
        )
    return expected_metrics


def validate_chunk(
    chunk: Mapping[str, Any],
    packet: Mapping[str, Any],
    *,
    chunk_schema: Mapping[str, Any] | None = None,
    language_schema: Mapping[str, Any] | None = None,
    packet_schema: Mapping[str, Any] | None = None,
    language_schema_bytes: bytes | None = None,
    chunk_bytes: bytes | None = None,
    packet_bytes: bytes | None = None,
) -> dict[str, Any]:
    """Validate one semantic conversion chunk against its immutable work packet."""

    audit = Audit()
    if packet_schema is None:
        packet_schema = _mapping(load_json(PACKET_SCHEMA_PATH))
    _packet_schema_validate(audit, packet_schema, packet)
    if chunk_schema is None:
        loaded_chunk_schema = load_json(CHUNK_SCHEMA_PATH)
        chunk_schema = _mapping(loaded_chunk_schema)
    if language_schema is None:
        raw_language_schema = LANGUAGE_SCHEMA_PATH.read_bytes()
        loaded_language_schema = parse_json_bytes(raw_language_schema)
        language_schema = _mapping(loaded_language_schema)
        if language_schema_bytes is None:
            language_schema_bytes = raw_language_schema
    elif language_schema_bytes is None:
        language_schema_bytes = canonical_json_bytes(language_schema)

    validator = build_schema_validator(chunk_schema, language_schema, audit)
    if validator is not None:
        _schema_validate(audit, validator, chunk)
    _validate_forbidden_markers(audit, chunk)

    expected_schema_hash = language_schema_sha256(language_schema_bytes)
    _validate_snapshot(audit, chunk, packet, expected_schema_hash)
    source_clauses, _ = _validate_coverage(audit, chunk, packet)
    by_kind, by_id = _build_object_index(audit, chunk)
    _validate_clause_refs(audit, chunk, source_clauses)
    _validate_refs(audit, chunk, by_kind, by_id, source_clauses)
    _validate_record_links(audit, chunk, by_kind)
    _validate_ast_and_tables(audit, chunk, packet, by_kind)
    _validate_exceptions(audit, chunk)
    _validate_reference_edges(audit, chunk, by_id)
    metrics = _validate_metrics_and_hash(audit, chunk)

    if chunk_bytes is not None:
        try:
            canonical_chunk = canonical_json_bytes(chunk)
        except (TypeError, ValueError) as error:
            audit.fail("hash.chunk", f"Chunk cannot be serialized canonically: {error}")
        else:
            audit.require(
                chunk_bytes == canonical_chunk,
                "json.chunk_canonical",
                "Chunk file bytes are not canonical JSON",
            )
    if packet_bytes is not None:
        try:
            canonical_packet = canonical_json_bytes(packet)
        except (TypeError, ValueError) as error:
            audit.fail("hash.packet", f"Packet cannot be serialized canonically: {error}")
        else:
            audit.require(
                packet_bytes == canonical_packet,
                "json.packet_canonical",
                "Packet file bytes are not canonical JSON",
            )

    return {
        "passed": not audit.errors,
        "error_count": len(audit.errors),
        "errors": audit.errors,
        "metrics": metrics,
    }


validate_normalized_rule_chunk = validate_chunk


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strictly validate normalized semantic conversion chunks"
    )
    parser.add_argument("chunks", nargs="*", type=Path)
    parser.add_argument(
        "--chunk",
        action="append",
        type=Path,
        dest="chunk_options",
        help="Chunk path; may be repeated (positional chunk paths are also accepted)",
    )
    parser.add_argument(
        "--packet",
        action="append",
        type=Path,
        help="Packet path; repeat once per chunk (otherwise --packet-dir is used)",
    )
    parser.add_argument("--packet-dir", type=Path, default=DEFAULT_PACKET_DIR)
    args = parser.parse_args(argv)
    args.chunks = [*args.chunks, *(args.chunk_options or [])]
    if not args.chunks:
        parser.error("at least one chunk path is required")
    return args


def _packet_paths(args: argparse.Namespace, chunks: list[Mapping[str, Any]]) -> list[Path]:
    if args.packet:
        if len(args.packet) != len(chunks):
            raise ValueError("--packet must be repeated exactly once per chunk")
        return args.packet
    paths: list[Path] = []
    for chunk in chunks:
        packet_id = chunk.get("packet_id")
        if not isinstance(packet_id, str):
            raise ValueError("chunk has no string packet_id for packet lookup")
        paths.append(args.packet_dir / f"{packet_id}.json")
    return paths


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        chunk_bytes = [path.read_bytes() for path in args.chunks]
        chunks = [parse_json_bytes(raw) for raw in chunk_bytes]
        packet_paths = _packet_paths(args, list(map(_mapping, chunks)))
        packet_bytes = [path.read_bytes() for path in packet_paths]
        packets = [parse_json_bytes(raw) for raw in packet_bytes]
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 2

    reports = []
    for chunk_path, packet_path, chunk, packet, raw_chunk, raw_packet in zip(
        args.chunks,
        packet_paths,
        chunks,
        packets,
        chunk_bytes,
        packet_bytes,
    ):
        report = validate_chunk(
            _mapping(chunk),
            _mapping(packet),
            chunk_bytes=raw_chunk,
            packet_bytes=raw_packet,
        )
        reports.append(
            {
                "chunk": str(chunk_path),
                "packet": str(packet_path),
                **report,
            }
        )
    result = {
        "passed": all(report["passed"] for report in reports),
        "chunk_count": len(reports),
        "reports": reports,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
