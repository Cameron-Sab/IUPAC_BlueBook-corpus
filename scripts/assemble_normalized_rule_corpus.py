from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

if __package__:
    from scripts import validate_normalized_rule_chunks as chunk_validator
else:
    import validate_normalized_rule_chunks as chunk_validator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "work" / "semantic_packets" / "manifest.json"
DEFAULT_PACKET_DIR = DEFAULT_MANIFEST.parent
DEFAULT_CHUNK_DIR = ROOT / "data" / "bluebook_v3" / "semantic_chunks"
DEFAULT_OUTPUT = ROOT / "data" / "bluebook_v3" / "bluebook_v3_rule_ir.json"
DEFAULT_SCHEMA = ROOT / "data" / "normalized_rule_language.schema.json"
DEFAULT_SOURCE_PAGES = ROOT / "data" / "bluebook_v3" / "bluebook_v3_source_pages.json"
DEFAULT_SOURCE_MANIFEST = ROOT / "data" / "source_manifest.json"

HASH_RE = re.compile(r"^[A-F0-9]{64}$")
SOURCE_HASH_FIELDS = (
    "source_corpus_sha256",
    "document_nodes_sha256",
    "correction_overlays_sha256",
    "clause_inventory_sha256",
    "reference_occurrences_sha256",
    "reference_resolutions_sha256",
)
MERGED_ARRAYS = (
    "semantic_units",
    "exceptions",
    "tables",
    "figures",
    "examples",
    "correction_applications",
    "references",
)
TOP_LEVEL_IDS = {
    "records": ("record", "record_id"),
    "exceptions": ("exception", "exception_id"),
    "figures": ("figure", "figure_id"),
    "examples": ("example", "example_id"),
    "correction_applications": ("correction_application", "application_id"),
    "references": ("reference", "reference_id"),
    "dependency_edges": ("dependency_edge", "edge_id"),
}


class AssemblyError(ValueError):
    pass


@dataclass(frozen=True)
class AddressableObject:
    kind: str
    object_id: str
    path: str
    value: Mapping[str, Any]


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AssemblyError(f"{label} must be a JSON object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AssemblyError(f"{label} must be a JSON array")
    return value


def _load_canonical(path: Path, label: str) -> tuple[Mapping[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = chunk_validator.parse_json_bytes(raw)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise AssemblyError(f"Could not load {label} {path}: {error}") from error
    result = _mapping(value, label)
    try:
        canonical = chunk_validator.canonical_json_bytes(result)
    except (TypeError, ValueError) as error:
        raise AssemblyError(f"Could not canonically serialize {label} {path}: {error}") from error
    if raw != canonical:
        raise AssemblyError(f"{label} is not canonical JSON: {path}")
    return result, raw


def _validate_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or HASH_RE.fullmatch(value) is None:
        raise AssemblyError(f"{label} must be an uppercase SHA-256 digest")
    return value


def _validate_manifest(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if manifest.get("format") != "iupac-bluebook-semantic-work-packet-manifest":
        raise AssemblyError("Packet manifest has an unexpected format")
    if manifest.get("format_version") != "1.0.0":
        raise AssemblyError("Packet manifest has an unsupported format_version")
    expected_digest = chunk_validator.digest_without_field(manifest, "manifest_sha256")
    if manifest.get("manifest_sha256") != expected_digest:
        raise AssemblyError("Packet manifest SHA-256 does not reproduce")

    packets = [
        _mapping(item, f"manifest packets[{index}]")
        for index, item in enumerate(_list(manifest.get("packets"), "manifest packets"))
    ]
    if not packets:
        raise AssemblyError("Packet manifest contains no packets")
    if manifest.get("packet_count") != len(packets):
        raise AssemblyError("Packet manifest packet_count does not reproduce")

    packet_ids: list[str] = []
    assigned_rule_ids: list[str] = []
    output_names: list[str] = []
    for index, entry in enumerate(packets):
        packet_id = entry.get("packet_id")
        output_path = entry.get("output_path")
        if not isinstance(packet_id, str) or not packet_id:
            raise AssemblyError(f"Manifest packet {index} has no packet_id")
        if not isinstance(output_path, str) or not output_path:
            raise AssemblyError(f"Manifest packet {packet_id} has no output_path")
        output_name = PurePosixPath(output_path).name
        if output_name != f"{packet_id}.json":
            raise AssemblyError(
                f"Manifest output_path does not match packet_id {packet_id}: {output_path}"
            )
        packet_ids.append(packet_id)
        output_names.append(output_name)
        _validate_hash(entry.get("packet_sha256"), f"manifest packet {packet_id} hash")
        rules = _list(entry.get("assigned_rule_ids"), f"manifest packet {packet_id} rules")
        if not rules or not all(isinstance(rule_id, str) for rule_id in rules):
            raise AssemblyError(f"Manifest packet {packet_id} has invalid assigned_rule_ids")
        assigned_rule_ids.extend(rules)

    for label, values in (
        ("packet_id", packet_ids),
        ("output_path", output_names),
        ("assigned rule", assigned_rule_ids),
    ):
        duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
        if duplicates:
            raise AssemblyError(f"Duplicate manifest {label} values: {duplicates}")
    if manifest.get("assigned_rule_count") != len(assigned_rule_ids):
        raise AssemblyError("Packet manifest assigned_rule_count does not reproduce")
    for field in SOURCE_HASH_FIELDS:
        _validate_hash(manifest.get(field), f"manifest {field}")
    return packets


def _load_packets(
    entries: Sequence[Mapping[str, Any]], packet_dir: Path, manifest: Mapping[str, Any]
) -> list[tuple[Mapping[str, Any], bytes]]:
    loaded: list[tuple[Mapping[str, Any], bytes]] = []
    for entry in entries:
        packet_id = str(entry["packet_id"])
        packet, raw = _load_canonical(packet_dir / f"{packet_id}.json", "packet")
        for field in ("packet_id", "packet_sha256", "output_path", "assigned_rule_ids"):
            if packet.get(field) != entry.get(field):
                raise AssemblyError(
                    f"Packet {packet_id} field {field} differs from its manifest entry"
                )
        expected_hash = chunk_validator.digest_without_field(packet, "packet_sha256")
        if packet.get("packet_sha256") != expected_hash:
            raise AssemblyError(f"Packet {packet_id} SHA-256 does not reproduce")
        for field in SOURCE_HASH_FIELDS:
            if packet.get(field) != manifest.get(field):
                raise AssemblyError(
                    f"Packet {packet_id} field {field} differs from the manifest snapshot"
                )
        loaded.append((packet, raw))
    return loaded


def _load_chunks(
    chunk_dir: Path, entries: Sequence[Mapping[str, Any]]
) -> list[tuple[Mapping[str, Any], bytes, Path]]:
    expected_ids = [str(entry["packet_id"]) for entry in entries]
    expected_set = set(expected_ids)
    grouped: dict[str, list[tuple[Mapping[str, Any], bytes, Path]]] = defaultdict(list)
    try:
        paths = sorted(chunk_dir.glob("*.json"), key=lambda path: path.name)
    except OSError as error:
        raise AssemblyError(f"Could not enumerate semantic chunks: {error}") from error
    for path in paths:
        chunk, raw = _load_canonical(path, "semantic chunk")
        packet_id = chunk.get("packet_id")
        if not isinstance(packet_id, str):
            raise AssemblyError(f"Semantic chunk has no packet_id: {path}")
        if packet_id not in expected_set:
            raise AssemblyError(f"Semantic chunk names an unmanifested packet {packet_id}: {path}")
        grouped[packet_id].append((chunk, raw, path))

    result: list[tuple[Mapping[str, Any], bytes, Path]] = []
    for entry in entries:
        packet_id = str(entry["packet_id"])
        matches = grouped.get(packet_id, [])
        if len(matches) != 1:
            paths_text = [str(path) for _, _, path in matches]
            raise AssemblyError(
                f"Packet {packet_id} requires exactly one semantic chunk; "
                f"found {len(matches)}: {paths_text}"
            )
        chunk, raw, path = matches[0]
        expected_name = PurePosixPath(str(entry["output_path"])).name
        if path.name != expected_name:
            raise AssemblyError(
                f"Chunk filename for {packet_id} must be {expected_name}; found {path.name}"
            )
        result.append((chunk, raw, path))
    return result


def _validate_chunks(
    chunks: Sequence[tuple[Mapping[str, Any], bytes, Path]],
    packets: Sequence[tuple[Mapping[str, Any], bytes]],
    *,
    chunk_schema: Mapping[str, Any],
    language_schema: Mapping[str, Any],
    language_schema_bytes: bytes,
) -> None:
    failures: list[str] = []
    for (chunk, chunk_bytes, chunk_path), (packet, packet_bytes) in zip(chunks, packets):
        for field in SOURCE_HASH_FIELDS:
            if chunk.get(field) != packet.get(field):
                failures.append(
                    f"{chunk_path}: snapshot.source_hash /{field}: "
                    f"chunk {field} differs from packet {packet.get('packet_id')}"
                )
        report = chunk_validator.validate_chunk(
            chunk,
            packet,
            chunk_schema=chunk_schema,
            language_schema=language_schema,
            language_schema_bytes=language_schema_bytes,
            chunk_bytes=chunk_bytes,
            packet_bytes=packet_bytes,
        )
        if not report.get("passed"):
            details = "; ".join(
                f"{error.get('code')} {error.get('path')}: {error.get('message')}"
                for error in report.get("errors", [])
            )
            failures.append(f"{chunk_path}: {details}")
    if failures:
        raise AssemblyError("Invalid semantic chunks:\n" + "\n".join(failures))


def _walk_expression(
    value: Any, path: str
) -> Iterator[AddressableObject]:
    if not isinstance(value, Mapping):
        return
    object_id = value.get("expression_id")
    if isinstance(object_id, str):
        yield AddressableObject("expression", object_id, path, value)
    for key in ("from", "arg", "left", "right", "in", "where", "key"):
        if key in value:
            yield from _walk_expression(value[key], f"{path}/{key}")
    for index, child in enumerate(value.get("args", []) if isinstance(value.get("args"), list) else []):
        yield from _walk_expression(child, f"{path}/args/{index}")


def _walk_statement(value: Any, path: str) -> Iterator[AddressableObject]:
    if not isinstance(value, Mapping):
        return
    object_id = value.get("statement_id")
    if isinstance(object_id, str):
        yield AddressableObject("statement", object_id, path, value)
    for key in ("when", "value", "in", "stop_when", "assertion"):
        if key in value:
            yield from _walk_expression(value[key], f"{path}/{key}")
    for index, expression in enumerate(
        value.get("args", []) if isinstance(value.get("args"), list) else []
    ):
        yield from _walk_expression(expression, f"{path}/args/{index}")
    bindings = value.get("bindings")
    if isinstance(bindings, Mapping):
        for name, expression in bindings.items():
            yield from _walk_expression(expression, f"{path}/bindings/{name}")
    for key in ("steps", "then", "else", "body"):
        children = value.get(key, [])
        if isinstance(children, list):
            for index, child in enumerate(children):
                yield from _walk_statement(child, f"{path}/{key}/{index}")


def _iter_addressable(corpus: Mapping[str, Any]) -> Iterator[AddressableObject]:
    symbols = corpus.get("symbol_registry", {}).get("symbols", [])
    if isinstance(symbols, list):
        for index, symbol in enumerate(symbols):
            if isinstance(symbol, Mapping) and isinstance(symbol.get("symbol_id"), str):
                yield AddressableObject(
                    "symbol", symbol["symbol_id"], f"/symbol_registry/symbols/{index}", symbol
                )
    for array_name, (kind, id_field) in TOP_LEVEL_IDS.items():
        values = corpus.get(array_name, [])
        if isinstance(values, list):
            for index, value in enumerate(values):
                if isinstance(value, Mapping) and isinstance(value.get(id_field), str):
                    yield AddressableObject(kind, value[id_field], f"/{array_name}/{index}", value)

    units = corpus.get("semantic_units", [])
    if isinstance(units, list):
        for unit_index, unit in enumerate(units):
            if not isinstance(unit, Mapping):
                continue
            unit_path = f"/semantic_units/{unit_index}"
            if isinstance(unit.get("unit_id"), str):
                yield AddressableObject("semantic_unit", unit["unit_id"], unit_path, unit)
            scope = unit.get("scope")
            if isinstance(scope, Mapping):
                yield from _walk_expression(scope.get("applies_to"), f"{unit_path}/scope/applies_to")
            for key in ("when", "candidates", "value", "assertion"):
                if key in unit:
                    yield from _walk_expression(unit[key], f"{unit_path}/{key}")
            for key in ("then", "else", "steps", "on_violation"):
                children = unit.get(key, [])
                if isinstance(children, list):
                    for index, statement in enumerate(children):
                        yield from _walk_statement(statement, f"{unit_path}/{key}/{index}")
            stages = unit.get("stages", [])
            if isinstance(stages, list):
                for stage_index, stage in enumerate(stages):
                    if not isinstance(stage, Mapping):
                        continue
                    stage_path = f"{unit_path}/stages/{stage_index}"
                    if isinstance(stage.get("stage_id"), str):
                        yield AddressableObject("decision_stage", stage["stage_id"], stage_path, stage)
                    yield from _walk_expression(stage.get("guard"), f"{stage_path}/guard")
                    yield from _walk_expression(stage.get("key"), f"{stage_path}/key")

    exceptions = corpus.get("exceptions", [])
    if isinstance(exceptions, list):
        for index, exception in enumerate(exceptions):
            if not isinstance(exception, Mapping):
                continue
            path = f"/exceptions/{index}"
            yield from _walk_expression(exception.get("when"), f"{path}/when")
            effect = exception.get("effect")
            if isinstance(effect, Mapping):
                yield from _walk_expression(effect.get("guard"), f"{path}/effect/guard")

    tables = corpus.get("tables", [])
    if isinstance(tables, list):
        for table_index, table in enumerate(tables):
            if not isinstance(table, Mapping):
                continue
            table_path = f"/tables/{table_index}"
            if isinstance(table.get("table_id"), str):
                yield AddressableObject("table", table["table_id"], table_path, table)
            for array_name, kind, id_field in (
                ("columns", "table_column", "column_id"),
                ("rows", "table_row", "row_id"),
                ("footnotes", "table_footnote", "footnote_id"),
            ):
                children = table.get(array_name, [])
                if not isinstance(children, list):
                    continue
                for child_index, child in enumerate(children):
                    if not isinstance(child, Mapping):
                        continue
                    child_path = f"{table_path}/{array_name}/{child_index}"
                    if isinstance(child.get(id_field), str):
                        yield AddressableObject(kind, child[id_field], child_path, child)
                    if array_name == "rows":
                        cells = child.get("cells", [])
                        if isinstance(cells, list):
                            for cell_index, cell in enumerate(cells):
                                if isinstance(cell, Mapping) and isinstance(cell.get("cell_id"), str):
                                    yield AddressableObject(
                                        "table_cell",
                                        cell["cell_id"],
                                        f"{child_path}/cells/{cell_index}",
                                        cell,
                                    )


def _build_index(
    corpus: Mapping[str, Any], *, reject_collisions: bool = True
) -> tuple[dict[str, dict[str, AddressableObject]], dict[str, list[AddressableObject]]]:
    by_kind: dict[str, dict[str, AddressableObject]] = defaultdict(dict)
    by_id: dict[str, list[AddressableObject]] = defaultdict(list)
    for item in _iter_addressable(corpus):
        by_id[item.object_id].append(item)
        by_kind[item.kind].setdefault(item.object_id, item)
    collisions = {object_id: items for object_id, items in by_id.items() if len(items) > 1}
    if reject_collisions and collisions:
        object_id = sorted(collisions)[0]
        locations = [f"{item.kind} at {item.path}" for item in collisions[object_id]]
        raise AssemblyError(
            f"Addressable object ID collision for {object_id}: {', '.join(locations)}"
        )
    return dict(by_kind), dict(by_id)


def _iter_object_refs(
    corpus: Mapping[str, Any],
) -> Iterator[tuple[Mapping[str, Any], str, str | None]]:
    symbols = corpus.get("symbol_registry", {}).get("symbols", [])
    if isinstance(symbols, list):
        for index, symbol in enumerate(symbols):
            if not isinstance(symbol, Mapping):
                continue
            grounding = symbol.get("grounding")
            refs = grounding.get("refs", []) if isinstance(grounding, Mapping) else []
            if isinstance(refs, list):
                for ref_index, ref in enumerate(refs):
                    if isinstance(ref, Mapping):
                        yield ref, f"/symbol_registry/symbols/{index}/grounding/refs/{ref_index}", None
    dispositions = corpus.get("clause_dispositions", [])
    if isinstance(dispositions, list):
        for index, item in enumerate(dispositions):
            if not isinstance(item, Mapping):
                continue
            body = item.get("disposition")
            targets = body.get("targets", []) if isinstance(body, Mapping) else []
            if isinstance(targets, list):
                for ref_index, ref in enumerate(targets):
                    if isinstance(ref, Mapping):
                        clause_id = item.get("clause_id")
                        yield ref, f"/clause_dispositions/{index}/disposition/targets/{ref_index}", clause_id if isinstance(clause_id, str) else None
    units = corpus.get("semantic_units", [])
    if isinstance(units, list):
        for index, unit in enumerate(units):
            terminal = unit.get("terminal_tie") if isinstance(unit, Mapping) else None
            fallback = terminal.get("fallback_ref") if isinstance(terminal, Mapping) else None
            if isinstance(fallback, Mapping):
                yield fallback, f"/semantic_units/{index}/terminal_tie/fallback_ref", None
    exceptions = corpus.get("exceptions", [])
    if isinstance(exceptions, list):
        for index, exception in enumerate(exceptions):
            if not isinstance(exception, Mapping):
                continue
            target = exception.get("target")
            if isinstance(target, Mapping):
                yield target, f"/exceptions/{index}/target", None
            effect = exception.get("effect")
            if isinstance(effect, Mapping):
                for key in ("replacement", "redirect"):
                    ref = effect.get(key)
                    if isinstance(ref, Mapping):
                        yield ref, f"/exceptions/{index}/effect/{key}", None
    tables = corpus.get("tables", [])
    if isinstance(tables, list):
        for table_index, table in enumerate(tables):
            footnotes = table.get("footnotes", []) if isinstance(table, Mapping) else []
            if not isinstance(footnotes, list):
                continue
            for footnote_index, footnote in enumerate(footnotes):
                if not isinstance(footnote, Mapping):
                    continue
                refs = footnote.get("scope_refs", footnote.get("applies_to", []))
                if isinstance(refs, list):
                    for ref_index, ref in enumerate(refs):
                        if isinstance(ref, Mapping):
                            yield ref, f"/tables/{table_index}/footnotes/{footnote_index}/scope_refs/{ref_index}", None
    for array_name, field in (
        ("examples", "demonstrates"),
        ("correction_applications", "target_refs"),
    ):
        values = corpus.get(array_name, [])
        if isinstance(values, list):
            for index, value in enumerate(values):
                refs = value.get(field, []) if isinstance(value, Mapping) else []
                if isinstance(refs, list):
                    for ref_index, ref in enumerate(refs):
                        if isinstance(ref, Mapping):
                            yield ref, f"/{array_name}/{index}/{field}/{ref_index}", None
    references = corpus.get("references", [])
    if isinstance(references, list):
        for index, reference in enumerate(references):
            if not isinstance(reference, Mapping):
                continue
            for key in ("source", "target"):
                ref = reference.get(key)
                if isinstance(ref, Mapping):
                    yield ref, f"/references/{index}/{key}", None
            members = reference.get("ordered_member_refs", [])
            if isinstance(members, list):
                for ref_index, ref in enumerate(members):
                    if isinstance(ref, Mapping):
                        yield ref, f"/references/{index}/ordered_member_refs/{ref_index}", None
    edges = corpus.get("dependency_edges", [])
    if isinstance(edges, list):
        for index, edge in enumerate(edges):
            if not isinstance(edge, Mapping):
                continue
            for key in ("from", "to"):
                ref = edge.get(key)
                if isinstance(ref, Mapping):
                    yield ref, f"/dependency_edges/{index}/{key}", None


def _validate_global_refs(
    corpus: Mapping[str, Any],
    by_kind: Mapping[str, Mapping[str, AddressableObject]],
    by_id: Mapping[str, Sequence[AddressableObject]],
) -> None:
    clauses = {
        item.get("clause_id")
        for item in corpus.get("clause_dispositions", [])
        if isinstance(item, Mapping) and isinstance(item.get("clause_id"), str)
    }
    chapters = {
        record.get("chapter")
        for record in corpus.get("records", [])
        if isinstance(record, Mapping) and isinstance(record.get("chapter"), str)
    }
    chapters |= {f"chapter:{chapter}" for chapter in chapters}
    for ref, path, supporting_clause in _iter_object_refs(corpus):
        kind = ref.get("kind")
        object_id = ref.get("id")
        resolved: AddressableObject | None = None
        if kind == "external" and isinstance(object_id, str):
            continue
        if kind == "chapter" and object_id in chapters:
            continue
        if kind == "clause" and object_id in clauses:
            continue
        if isinstance(kind, str) and isinstance(object_id, str):
            candidate = by_kind.get(kind, {}).get(object_id)
            if candidate is not None and len(by_id.get(object_id, [])) == 1:
                resolved = candidate
        if resolved is None:
            actual = [item.kind for item in by_id.get(str(object_id), [])]
            raise AssemblyError(
                f"Dangling typed object reference at {path}: {kind}:{object_id}; "
                f"actual kinds={actual}"
            )
        if supporting_clause is not None:
            target_clauses = resolved.value.get("clause_ids")
            if isinstance(target_clauses, list) and supporting_clause not in target_clauses:
                raise AssemblyError(
                    f"Compiled target {kind}:{object_id} is not grounded in {supporting_clause}"
                )


def _merge_symbols(chunks: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    declarations: list[Mapping[str, Any]] = []
    by_id: dict[str, Mapping[str, Any]] = {}
    for chunk in chunks:
        symbols = _list(chunk.get("symbol_declarations"), "chunk symbol_declarations")
        for symbol in symbols:
            declaration = _mapping(symbol, "symbol declaration")
            symbol_id = declaration.get("symbol_id")
            if not isinstance(symbol_id, str):
                raise AssemblyError("Symbol declaration has no symbol_id")
            previous = by_id.get(symbol_id)
            if previous is None:
                by_id[symbol_id] = declaration
                declarations.append(declaration)
            elif previous != declaration:
                raise AssemblyError(
                    f"Conflicting declarations for symbol {symbol_id}; only identical declarations may merge"
                )
    return declarations


def _exception_key(exception: Mapping[str, Any]) -> tuple[int, int, str]:
    precedence = exception.get("precedence")
    specificity = precedence.get("specificity") if isinstance(precedence, Mapping) else None
    source_order = precedence.get("source_order") if isinstance(precedence, Mapping) else None
    return (
        -specificity if isinstance(specificity, int) else 0,
        source_order if isinstance(source_order, int) else 0,
        str(exception.get("exception_id", "")),
    )


def _sort_and_validate_exceptions(
    exceptions: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    result = sorted(exceptions, key=_exception_key)
    seen: dict[tuple[Any, Any, Any, Any], str] = {}
    for exception in result:
        target = exception.get("target")
        precedence = exception.get("precedence")
        target = target if isinstance(target, Mapping) else {}
        precedence = precedence if isinstance(precedence, Mapping) else {}
        key = (
            target.get("kind"),
            target.get("id"),
            precedence.get("specificity"),
            precedence.get("source_order"),
        )
        previous = seen.get(key)
        if previous is not None:
            raise AssemblyError(
                "Ambiguous exception precedence for "
                f"{target.get('kind')}:{target.get('id')}: {previous} and "
                f"{exception.get('exception_id')}"
            )
        seen[key] = str(exception.get("exception_id"))
    return result


def _append_unique(target: list[str], values: Iterable[Any]) -> None:
    seen = set(target)
    for value in values:
        if isinstance(value, str) and value not in seen:
            target.append(value)
            seen.add(value)


def _edge_id(key: tuple[str, str, str, str, str]) -> str:
    payload = {
        "from": {"kind": key[0], "id": key[1]},
        "relation": key[2],
        "to": {"kind": key[3], "id": key[4]},
    }
    digest = hashlib.sha256(chunk_validator.canonical_json_bytes(payload)).hexdigest().upper()
    return f"edge.{digest}"


def _project_dependency_edges(corpus: Mapping[str, Any]) -> list[dict[str, Any]]:
    projections: list[tuple[Mapping[str, Any], str, Mapping[str, Any], list[Any], str]] = []
    for reference in corpus.get("references", []):
        if isinstance(reference, Mapping):
            source = _mapping(reference.get("source"), "reference source")
            target = _mapping(reference.get("target"), "reference target")
            projections.append(
                (source, str(reference.get("relation")), target, list(reference.get("clause_ids", [])), str(reference.get("reference_id")))
            )
    for exception in corpus.get("exceptions", []):
        if isinstance(exception, Mapping):
            exception_id = str(exception.get("exception_id"))
            target = _mapping(exception.get("target"), "exception target")
            projections.append(
                ({"kind": "exception", "id": exception_id}, "exception_to", target, list(exception.get("clause_ids", [])), exception_id)
            )

    edges: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for source, relation, target, clause_ids, derived_id in projections:
        key = (
            str(source.get("kind")),
            str(source.get("id")),
            relation,
            str(target.get("kind")),
            str(target.get("id")),
        )
        edge = by_key.get(key)
        if edge is None:
            edge = {
                "edge_id": _edge_id(key),
                "from": dict(source),
                "relation": relation,
                "to": dict(target),
                "clause_ids": [],
                "derived_from_object_ids": [],
            }
            by_key[key] = edge
            edges.append(edge)
        _append_unique(edge["clause_ids"], clause_ids)
        _append_unique(edge["derived_from_object_ids"], [derived_id])

    expected = [
        str(item.get("reference_id"))
        for item in corpus.get("references", [])
        if isinstance(item, Mapping)
    ] + [
        str(item.get("exception_id"))
        for item in corpus.get("exceptions", [])
        if isinstance(item, Mapping)
    ]
    observed = [
        object_id
        for edge in edges
        for object_id in edge["derived_from_object_ids"]
    ]
    if Counter(observed) != Counter(expected):
        raise AssemblyError("Dependency edge provenance does not cover every reference and exception exactly once")
    return edges


def _source_snapshot(
    manifest: Mapping[str, Any], source_pages_path: Path, source_manifest_path: Path
) -> dict[str, str]:
    declared = manifest.get("source_snapshot")
    declared = declared if isinstance(declared, Mapping) else {}
    snapshot = {
        field: _validate_hash(
            declared.get(field, manifest.get(field)), f"source snapshot {field}"
        )
        for field in SOURCE_HASH_FIELDS
    }
    source_pages_hash = declared.get("source_pages_sha256", manifest.get("source_pages_sha256"))
    if source_pages_hash is None:
        try:
            source_pages_hash = hashlib.sha256(source_pages_path.read_bytes()).hexdigest().upper()
        except OSError as error:
            raise AssemblyError(
                "source_pages_sha256 is absent from the packet manifest and "
                f"{source_pages_path} could not be read: {error}"
            ) from error
    snapshot["source_pages_sha256"] = _validate_hash(
        source_pages_hash, "source snapshot source_pages_sha256"
    )

    effective_through = declared.get("effective_through", manifest.get("effective_through"))
    if effective_through is None:
        try:
            source_manifest = _mapping(
                chunk_validator.load_json(source_manifest_path), "source manifest"
            )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise AssemblyError(
                f"Could not load source manifest {source_manifest_path}: {error}"
            ) from error
        effective_through = source_manifest.get("effective_through")
    if not isinstance(effective_through, str):
        raise AssemblyError("source snapshot effective_through must be an ISO date")
    try:
        date.fromisoformat(effective_through)
    except ValueError as error:
        raise AssemblyError("source snapshot effective_through must be an ISO date") from error
    snapshot["effective_through"] = effective_through
    return snapshot


def _metrics(corpus: Mapping[str, Any]) -> dict[str, int]:
    dispositions = [
        item for item in corpus.get("clause_dispositions", []) if isinstance(item, Mapping)
    ]
    kinds = Counter(
        item.get("disposition", {}).get("kind")
        for item in dispositions
        if isinstance(item.get("disposition"), Mapping)
    )
    return {
        "record_count": len(corpus.get("records", [])),
        "clause_disposition_count": len(dispositions),
        "compiled_clause_count": kinds["compiled"],
        "nonoperative_clause_count": kinds["nonoperative"],
        "superseded_clause_count": kinds["superseded"],
        "semantic_unit_count": len(corpus.get("semantic_units", [])),
        "exception_count": len(corpus.get("exceptions", [])),
        "table_count": len(corpus.get("tables", [])),
        "figure_count": len(corpus.get("figures", [])),
        "example_count": len(corpus.get("examples", [])),
        "correction_application_count": len(corpus.get("correction_applications", [])),
        "reference_count": len(corpus.get("references", [])),
        "dependency_edge_count": len(corpus.get("dependency_edges", [])),
    }


def _schema_registry(schema: Mapping[str, Any], schema_path: Path) -> Registry:
    resources: list[tuple[str, Resource[Any]]] = []
    candidates = [schema_path, chunk_validator.CHUNK_SCHEMA_PATH]
    seen_paths: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen_paths or not candidate.exists():
            continue
        seen_paths.add(candidate)
        value = schema if candidate == schema_path.resolve() else chunk_validator.load_json(candidate)
        if isinstance(value, Mapping) and isinstance(value.get("$id"), str):
            resources.append((value["$id"], Resource.from_contents(value)))
    return Registry().with_resources(resources)


def _validate_final_schema(
    corpus: Mapping[str, Any], schema: Mapping[str, Any], schema_path: Path
) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(
            schema,
            registry=_schema_registry(schema, schema_path),
            format_checker=FormatChecker(),
        )
        errors = sorted(
            validator.iter_errors(corpus),
            key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
        )
    except Exception as error:
        raise AssemblyError(f"Final schema preflight failed: {error}") from error
    if errors:
        details = "; ".join(
            f"/{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in errors
        )
        raise AssemblyError(f"Assembled corpus does not match the normalized rule schema: {details}")


def validate_rule_corpus(
    corpus: Mapping[str, Any],
    schema: Mapping[str, Any],
    schema_path: Path = DEFAULT_SCHEMA,
) -> None:
    """Validate a materialized normalized corpus independently of its chunks."""

    _validate_final_schema(corpus, schema, schema_path)
    serialized = json.dumps(corpus, ensure_ascii=False, sort_keys=True)
    match = chunk_validator.FORBIDDEN_SEMANTIC_RE.search(serialized)
    if match is not None:
        raise AssemblyError(
            f"Assembled corpus contains forbidden semantic marker: {match.group(0)}"
        )

    expected_exceptions = _sort_and_validate_exceptions(
        item
        for item in corpus.get("exceptions", [])
        if isinstance(item, Mapping)
    )
    if corpus.get("exceptions") != expected_exceptions:
        raise AssemblyError("Assembled exceptions are not in deterministic precedence order")

    by_kind, by_id = _build_index(corpus)
    _validate_global_refs(corpus, by_kind, by_id)
    for record_index, record in enumerate(corpus.get("records", [])):
        if not isinstance(record, Mapping):
            continue
        record_clauses = set(record.get("clause_ids", []))
        for field, kind in chunk_validator.RECORD_LINKS.items():
            member_ids = record.get(field, [])
            if not isinstance(member_ids, list) or len(member_ids) != len(
                set(member_ids)
            ):
                raise AssemblyError(
                    f"Record {record_index} has invalid or duplicate {field}"
                )
            for object_id in member_ids:
                target = by_kind.get(kind, {}).get(object_id)
                if target is None or len(by_id.get(str(object_id), [])) != 1:
                    raise AssemblyError(
                        f"Record {record_index} has dangling {field} member {object_id}"
                    )
                target_clauses = target.value.get("clause_ids")
                if isinstance(target_clauses, list) and not set(
                    target_clauses
                ).issubset(record_clauses):
                    raise AssemblyError(
                        f"Record {record_index} member {object_id} is grounded outside its clauses"
                    )

    expected_edges = _project_dependency_edges(corpus)
    if corpus.get("dependency_edges") != expected_edges:
        raise AssemblyError(
            "Assembled dependency edges differ from deterministic projection"
        )
    if corpus.get("metrics") != _metrics(corpus):
        raise AssemblyError("Assembled corpus metrics do not reproduce")
    if corpus.get("corpus_sha256") != chunk_validator.digest_without_field(
        corpus, "corpus_sha256"
    ):
        raise AssemblyError("Assembled corpus SHA-256 does not reproduce")


def assemble_rule_corpus(
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    packet_dir: Path = DEFAULT_PACKET_DIR,
    chunk_dir: Path = DEFAULT_CHUNK_DIR,
    schema_path: Path = DEFAULT_SCHEMA,
    source_pages_path: Path = DEFAULT_SOURCE_PAGES,
    source_manifest_path: Path = DEFAULT_SOURCE_MANIFEST,
) -> dict[str, Any]:
    manifest, _ = _load_canonical(manifest_path, "packet manifest")
    entries = _validate_manifest(manifest)
    packets = _load_packets(entries, packet_dir, manifest)
    chunks_with_paths = _load_chunks(chunk_dir, entries)

    language_schema_raw = schema_path.read_bytes()
    language_schema = _mapping(
        chunk_validator.parse_json_bytes(language_schema_raw), "normalized rule schema"
    )
    chunk_schema = _mapping(
        chunk_validator.load_json(chunk_validator.CHUNK_SCHEMA_PATH), "normalized chunk schema"
    )
    _validate_chunks(
        chunks_with_paths,
        packets,
        chunk_schema=chunk_schema,
        language_schema=language_schema,
        language_schema_bytes=language_schema_raw,
    )

    chunks = [chunk for chunk, _, _ in chunks_with_paths]
    corpus: dict[str, Any] = {
        "format": "iupac-bluebook-normalized-rule-language",
        "format_version": "3.0.0",
        "conversion_stage": "complete_semantic_ir",
        "source_snapshot": _source_snapshot(manifest, source_pages_path, source_manifest_path),
        "symbol_registry": {"symbols": _merge_symbols(chunks)},
        "clause_dispositions": [
            item for chunk in chunks for item in chunk["clause_dispositions"]
        ],
        "records": [item for chunk in chunks for item in chunk["records"]],
    }
    for array_name in MERGED_ARRAYS:
        corpus[array_name] = [item for chunk in chunks for item in chunk[array_name]]
    corpus["exceptions"] = _sort_and_validate_exceptions(corpus["exceptions"])

    expected_rule_order = [
        rule_id for entry in entries for rule_id in entry["assigned_rule_ids"]
    ]
    actual_rule_order = [record.get("source_rule_id") for record in corpus["records"]]
    if actual_rule_order != expected_rule_order:
        raise AssemblyError("Assembled records do not preserve exact manifest source order")

    by_kind, by_id = _build_index(corpus)
    _validate_global_refs(corpus, by_kind, by_id)
    corpus["dependency_edges"] = _project_dependency_edges(corpus)
    by_kind, by_id = _build_index(corpus)
    _validate_global_refs(corpus, by_kind, by_id)
    for edge in corpus["dependency_edges"]:
        for object_id in edge["derived_from_object_ids"]:
            if len(by_id.get(object_id, [])) != 1:
                raise AssemblyError(
                    f"Dependency edge {edge['edge_id']} has dangling provenance {object_id}"
                )

    corpus["metrics"] = _metrics(corpus)
    corpus["corpus_sha256"] = chunk_validator.digest_without_field(
        corpus, "corpus_sha256"
    )
    validate_rule_corpus(corpus, language_schema, schema_path)
    return corpus


def write_corpus(path: Path, corpus: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(chunk_validator.canonical_json_bytes(corpus))
    temporary.replace(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strictly assemble finalized normalized semantic chunks"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--packet-dir", type=Path, default=DEFAULT_PACKET_DIR)
    parser.add_argument("--chunk-dir", type=Path, default=DEFAULT_CHUNK_DIR)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--source-pages", type=Path, default=DEFAULT_SOURCE_PAGES)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        corpus = assemble_rule_corpus(
            manifest_path=args.manifest,
            packet_dir=args.packet_dir,
            chunk_dir=args.chunk_dir,
            schema_path=args.schema,
            source_pages_path=args.source_pages,
            source_manifest_path=args.source_manifest,
        )
        write_corpus(args.output, corpus)
    except (AssemblyError, OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1
    print(
        json.dumps(
            {
                "passed": True,
                "output": str(args.output),
                "metrics": corpus["metrics"],
                "corpus_sha256": corpus["corpus_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
