from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import lxml
from lxml import html as lxml_html


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / ".cache" / "bluebook_html" / "changes2.html"
DEFAULT_OUTPUT = ROOT / "data" / "bluebook_v3" / "bluebook_v3_correction_overlays.json"
SOURCE_URL = "https://iupac.qmul.ac.uk/BlueBook/changes2.html"

EVENT_TYPES = ("corrected", "modified", "added", "changed")
HEADER_RE = re.compile(
    r"(?im)^[^\r\n]*\[(?=(?:corrected|modified|added|changed)\b)[^\]\r\n]+\][^\r\n]*$"
)
EVENT_ANNOTATION_RE = re.compile(
    r"\[(?P<annotation>(?=(?:corrected|modified|added|changed)\b)[^\]]+)\]",
    re.I,
)
EVENT_RE = re.compile(
    r"\b(?P<event>corrected|modified|added|changed)\s+"
    r"(?P<date>\d{1,2}(?:\.\d{1,2}\.\d{4}|\s+[A-Za-z]+\s+\d{4}))",
    re.I,
)
RULE_RE = re.compile(r"\bP-\d+(?:\.\d+)*\b", re.I)
EXTERNAL_RULE_RE = re.compile(r"\bPhII-\d+(?:\.\d+)*\b", re.I)
PAGE_RE = re.compile(r"\bPage\s+(\d+)\b", re.I)
TABLE_RE = re.compile(r"\bTable\s+([A-Za-z0-9.-]+)", re.I)
FIGURE_RE = re.compile(r"\bFig(?:ure)?\.?\s+([A-Za-z0-9.-]+)", re.I)
APPENDIX_RE = re.compile(r"\bAppendix\s+(\d+)\b", re.I)

COMMAND_RE = re.compile(
    r"(?is)"
    r"(?P<span>"
    r"<span\b(?=[^>]*style\s*=\s*['\"][^'\"]*color\s*:\s*blue)[^>]*>\s*"
    r"(?P<span_command>add\s+in\s+a\s+box|replace\s+structure\s+by|"
    r"replace\s+by|delete|for|read|add|replace)\s*</span>"
    r")"
    r"|"
    r"(?P<plain>"
    r"(?P<plain_prefix>(?:\A\s*|<br\s*/?>\s*|<p(?:\s[^>]*)?>\s*))"
    r"(?P<plain_command>replace\s+the\s+structure\s+with\s*:?|"
    r"add\s+the\s+structure\s*:?|replace\s+by|delete|for|read|add|replace)\b"
    r")"
)
IMG_RE = re.compile(
    r"<img\b(?P<attrs>[^>]*)>",
    re.I | re.S,
)
ATTR_RE = re.compile(
    r"(?P<name>[A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.S,
)

CP1252_CONTROL_MAP = {
    code: bytes([code]).decode("cp1252")
    for code in range(0x80, 0xA0)
    if code not in {0x81, 0x8D, 0x8F, 0x90, 0x9D}
}
MOJIBAKE_REPLACEMENTS = {
    "\u00e2\u0080\u0094": "\u2014",
    "\u00e2\u20ac\u201d": "\u2014",
    "\u00e2\u0080\u0093": "\u2013",
    "\u00e2\u20ac\u201c": "\u2013",
    "\u00e2\u0080\u00b2": "\u2032",
    "\u00e2\u20ac\u00b2": "\u2032",
    "\u00e2\u0080\u0098": "\u2018",
    "\u00e2\u0080\u0099": "\u2019",
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _repair_text(value: str) -> str:
    value = value.translate(CP1252_CONTROL_MAP).replace("\u00a0", " ")
    for damaged, repaired in MOJIBAKE_REPLACEMENTS.items():
        value = value.replace(damaged, repaired)
    return value


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", _repair_text(value)).strip()


def _render_element(element: Any, parts: list[str]) -> None:
    tag = str(element.tag).lower() if isinstance(element.tag, str) else ""
    if tag in {"br", "p", "tr", "table", "div", "hr"}:
        parts.append("\n")
    if tag == "img":
        source = element.get("src", "").strip()
        parts.append(f"[image: {source}]" if source else "[image]")
    if element.text:
        parts.append(element.text)
    for child in element:
        _render_element(child, parts)
        if child.tail:
            parts.append(child.tail)
    if tag in {"p", "tr", "table", "div"}:
        parts.append("\n")


def visible_text(fragment: str) -> str:
    if not fragment.strip():
        return ""
    parent = lxml_html.fragment_fromstring(fragment, create_parent="div")
    parts: list[str] = []
    _render_element(parent, parts)
    lines = [_compact_text(line) for line in "".join(parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _byte_offset(source: str, offset: int) -> int:
    return len(source[:offset].encode("utf-8"))


def _relative_source_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _parse_date(value: str) -> date:
    value = value.strip()
    date_format = "%d.%m.%Y" if "." in value else "%d %B %Y"
    return datetime.strptime(value, date_format).date()


def parse_events(annotation: str) -> list[dict[str, str]]:
    events = []
    for match in EVENT_RE.finditer(annotation):
        event_type = match.group("event").lower()
        source_date = match.group("date")
        events.append(
            {
                "event_type": event_type,
                "effective_date": _parse_date(source_date).isoformat(),
                "source_text": _compact_text(match.group(0)),
            }
        )
    if not events:
        raise ValueError(f"Correction annotation has no dated event: [{annotation}]")
    return events


def _extract_attributes(fragment: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for match in ATTR_RE.finditer(fragment):
        value = match.group("value")
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        attributes[match.group("name").lower()] = value
    return attributes


def extract_assets(fragment: str) -> list[dict[str, Any]]:
    assets = []
    for match in IMG_RE.finditer(fragment):
        attributes = _extract_attributes(match.group("attrs"))
        source_path = attributes.get("src", "").strip()
        if not source_path:
            continue
        asset: dict[str, Any] = {
            "kind": "image",
            "source_path": source_path,
            "source_url": urljoin(SOURCE_URL, source_path),
        }
        if attributes.get("alt"):
            asset["alt_text"] = _compact_text(attributes["alt"])
        if attributes.get("width", "").isdigit():
            asset["width"] = int(attributes["width"])
        if attributes.get("height", "").isdigit():
            asset["height"] = int(attributes["height"])
        assets.append(asset)
    return assets


def parse_target(selector_html: str) -> dict[str, Any]:
    selector_text = _compact_text(visible_text(selector_html))
    selectors: list[dict[str, Any]] = []

    if re.search(r"\bContents\b", selector_text, re.I):
        selectors.append({"kind": "contents", "value": "Contents"})

    rule_ids = [match.group(0).upper() for match in RULE_RE.finditer(selector_text)]
    for index, rule_id in enumerate(rule_ids):
        relation = "renamed_from" if index > 0 and re.search(
            rf"\bwas\s+{re.escape(rule_id)}\b", selector_text, re.I
        ) else "target"
        selectors.append({"kind": "rule", "rule_id": rule_id, "relation": relation})

    for match in PAGE_RE.finditer(selector_text):
        selectors.append({"kind": "page", "page": int(match.group(1))})
    for match in TABLE_RE.finditer(selector_text):
        selectors.append({"kind": "table", "label": match.group(1)})
    for match in FIGURE_RE.finditer(selector_text):
        selectors.append({"kind": "figure", "label": match.group(1)})
    for match in APPENDIX_RE.finditer(selector_text):
        selectors.append({"kind": "appendix", "number": int(match.group(1))})

    selectors.append({"kind": "location", "text": selector_text})
    return {"selector_text": selector_text, "selectors": selectors}


def _normalize_command(value: str) -> str:
    return _compact_text(value).lower().rstrip(":")


def _command_kind(value: str) -> str:
    if value.startswith("for"):
        return "for"
    if value.startswith("read"):
        return "read"
    if value.startswith("add"):
        return "add"
    if value.startswith("delete"):
        return "delete"
    if value.startswith("replace"):
        return "replace"
    raise ValueError(f"Unsupported correction command: {value}")


def _operation_provenance(
    source: str,
    source_start: int,
    source_end: int,
    source_html: str,
    dom_path: str,
) -> dict[str, Any]:
    return {
        "source_line_start": _line_number(source, source_start),
        "source_line_end": _line_number(source, max(source_start, source_end - 1)),
        "source_byte_start": _byte_offset(source, source_start),
        "source_byte_end": _byte_offset(source, source_end),
        "fragment_sha256": sha256_bytes(source_html.encode("utf-8")),
        "source_dom_path": dom_path,
    }


def _make_operation(
    *,
    overlay_id: str,
    ordinal: int,
    kind: str,
    source: str,
    source_start: int,
    source_end: int,
    source_html: str,
    source_text: str,
    before_text: str | None = None,
    after_text: str | None = None,
    instruction_text: str | None = None,
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "operation_id": f"{overlay_id}:OP-{ordinal:03d}",
        "kind": kind,
        "source_text": source_text,
        "source_html": source_html,
        "assets": extract_assets(source_html),
        "provenance": _operation_provenance(
            source,
            source_start,
            source_end,
            source_html,
            f"/html/body/correction-overlay[@id='{overlay_id}']/operation[{ordinal}]",
        ),
    }
    if before_text:
        operation["before_text"] = before_text
    if after_text:
        operation["after_text"] = after_text
    if instruction_text:
        operation["instruction_text"] = instruction_text
    return operation


def parse_operations(
    *,
    source: str,
    payload_start: int,
    payload_end: int,
    overlay_id: str,
) -> list[dict[str, Any]]:
    payload = source[payload_start:payload_end]
    matches = list(COMMAND_RE.finditer(payload))
    clauses: list[dict[str, Any]] = []

    if matches:
        preamble_html = payload[: matches[0].start()]
        preamble_text = _compact_text(visible_text(preamble_html))
        if preamble_text:
            clauses.append(
                {
                    "command": "instruction",
                    "label": "source instruction",
                    "text": preamble_text,
                    "start": 0,
                    "end": matches[0].start(),
                }
            )

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(payload)
        command_text = match.group("span_command") or match.group("plain_command") or ""
        content_html = payload[match.end() : end]
        content_text = _compact_text(visible_text(content_html))
        label = _normalize_command(command_text)
        clauses.append(
            {
                "command": _command_kind(label),
                "label": label,
                "text": content_text,
                "start": match.start(),
                "content_start": match.end(),
                "end": end,
            }
        )

    if not matches:
        instruction_text = _compact_text(visible_text(payload))
        if instruction_text:
            clauses.append(
                {
                    "command": "instruction",
                    "label": "source instruction",
                    "text": instruction_text,
                    "start": 0,
                    "end": len(payload),
                }
            )

    operations: list[dict[str, Any]] = []
    clause_index = 0
    while clause_index < len(clauses):
        clause = clauses[clause_index]
        command = clause["command"]
        operation_start = payload_start + int(clause["start"])

        if (
            command == "for"
            and clause_index + 1 < len(clauses)
            and clauses[clause_index + 1]["command"] == "read"
        ):
            following = clauses[clause_index + 1]
            operation_end = payload_start + int(following["end"])
            operation_html = source[operation_start:operation_end]
            before_text = str(clause["text"])
            after_text = str(following["text"])
            source_text = _compact_text(
                f"{clause['label']} {before_text} {following['label']} {after_text}"
            )
            operations.append(
                _make_operation(
                    overlay_id=overlay_id,
                    ordinal=len(operations) + 1,
                    kind="replacement",
                    source=source,
                    source_start=operation_start,
                    source_end=operation_end,
                    source_html=operation_html,
                    source_text=source_text,
                    before_text=before_text,
                    after_text=after_text,
                )
            )
            clause_index += 2
            continue

        operation_end = payload_start + int(clause["end"])
        operation_html = source[operation_start:operation_end]
        content_text = str(clause["text"])
        source_text = (
            content_text
            if command == "instruction"
            else _compact_text(f"{clause['label']} {content_text}")
        )

        if command == "add":
            operation_kind = "addition"
            field = {"after_text": content_text}
        elif command == "delete":
            operation_kind = "deletion"
            field = {"before_text": content_text}
        elif command == "replace":
            operation_kind = "replacement"
            field = {"after_text": content_text}
        else:
            operation_kind = "instruction"
            field = {"instruction_text": source_text}

        operations.append(
            _make_operation(
                overlay_id=overlay_id,
                ordinal=len(operations) + 1,
                kind=operation_kind,
                source=source,
                source_start=operation_start,
                source_end=operation_end,
                source_html=operation_html,
                source_text=source_text,
                **field,
            )
        )
        clause_index += 1

    if not operations:
        raise ValueError(f"Correction {overlay_id} has no operative source text")
    return operations


def _reference_relation(text: str, start: int) -> str:
    context = text[max(0, start - 45) : start].lower()
    if re.search(r"\bwas\s*$", context):
        return "renamed_from"
    if re.search(r"\bconflict(?:s|ing)?\s+with\s*$", context):
        return "conflicts_with"
    if re.search(r"\bsee\s*$", context):
        return "see_also"
    if re.search(r"(?:in accordance with|criteria of|applied)\s*$", context):
        return "invokes"
    if re.search(r"\bchange\s+from\s*$", context):
        return "supersedes"
    return "mentions"


def extract_references(
    *,
    overlay_id: str,
    target: dict[str, Any],
    operations: Iterable[dict[str, Any]],
) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []

    for selector in target["selectors"]:
        if selector["kind"] != "rule":
            continue
        relation = selector["relation"]
        references.append(
            {
                "reference_id": f"{overlay_id}:REF-{len(references) + 1:03d}",
                "target_type": "rule",
                "target": selector["rule_id"],
                "relation": relation,
                "source_text": target["selector_text"],
                "source_dom_path": "/html/body/correction-overlay/header",
            }
        )

    for operation in operations:
        operation_text = operation["source_text"]
        for match in RULE_RE.finditer(operation_text):
            references.append(
                {
                    "reference_id": f"{overlay_id}:REF-{len(references) + 1:03d}",
                    "target_type": "rule",
                    "target": match.group(0).upper(),
                    "relation": _reference_relation(operation_text, match.start()),
                    "source_text": operation_text,
                    "source_dom_path": operation["provenance"]["source_dom_path"],
                }
            )
        for match in EXTERNAL_RULE_RE.finditer(operation_text):
            references.append(
                {
                    "reference_id": f"{overlay_id}:REF-{len(references) + 1:03d}",
                    "target_type": "external_rule",
                    "target": match.group(0),
                    "relation": _reference_relation(operation_text, match.start()),
                    "source_text": operation_text,
                    "source_dom_path": operation["provenance"]["source_dom_path"],
                }
            )
    return references


def _record_status(operations: Iterable[dict[str, Any]]) -> str:
    operation_list = list(operations)
    target_deletion = any(
        operation["kind"] == "deletion"
        and re.search(r"\bthis\s+section\b", operation.get("before_text", ""), re.I)
        for operation in operation_list
    )
    if target_deletion and all(operation["kind"] == "deletion" for operation in operation_list):
        return "deleted"
    if any(operation["kind"] == "replacement" for operation in operation_list):
        return "replaced"
    return "applied"


def _record_ranges(
    source: str,
    start: int,
    end: int,
    source_html: str,
    overlay_id: str,
) -> dict[str, Any]:
    return {
        "source_path": "",
        "source_url": SOURCE_URL,
        "source_line_start": _line_number(source, start),
        "source_line_end": _line_number(source, max(start, end - 1)),
        "source_byte_start": _byte_offset(source, start),
        "source_byte_end": _byte_offset(source, end),
        "fragment_sha256": sha256_bytes(source_html.encode("utf-8")),
        "source_dom_path": f"/html/body/correction-overlay[@id='{overlay_id}']",
    }


def extract_correction_overlays(source_path: Path) -> dict[str, Any]:
    source_bytes = source_path.read_bytes()
    source = source_bytes.decode("utf-8")
    header_matches = list(HEADER_RE.finditer(source))
    if not header_matches:
        raise ValueError(f"No dated corrections found in {source_path}")

    trailer_match = re.search(r"(?is)\n\s*<hr>\s*\n\s*Return\s+to\s*:", source)
    records = []
    source_path_text = _relative_source_path(source_path)

    for index, header_match in enumerate(header_matches):
        start = header_match.start()
        if index + 1 < len(header_matches):
            end = header_matches[index + 1].start()
        else:
            end = trailer_match.start() if trailer_match and trailer_match.start() > start else len(source)
        while end > start and source[end - 1].isspace():
            end -= 1

        header_html = source[header_match.start() : header_match.end()]
        annotation_match = EVENT_ANNOTATION_RE.search(header_html)
        if annotation_match is None:
            raise ValueError(f"Correction header has no event annotation: {header_html}")
        selector_html = header_html[: annotation_match.start()].strip()
        events = parse_events(annotation_match.group("annotation"))
        target = parse_target(selector_html)
        source_html = source[start:end]
        overlay_digest = sha256_bytes(source_html.encode("utf-8"))[:16]
        overlay_id = f"BBV3-CORR-{overlay_digest}"
        operations = parse_operations(
            source=source,
            payload_start=header_match.end(),
            payload_end=end,
            overlay_id=overlay_id,
        )
        provenance = _record_ranges(source, start, end, source_html, overlay_id)
        provenance["source_path"] = source_path_text
        record = {
            "overlay_id": overlay_id,
            "effective_date": max(event["effective_date"] for event in events),
            "events": events,
            "status": _record_status(operations),
            "target": target,
            "operations": operations,
            "references": extract_references(
                overlay_id=overlay_id,
                target=target,
                operations=operations,
            ),
            "source_text": visible_text(source_html),
            "source_html": source_html,
            "provenance": provenance,
        }
        records.append(record)

    status_counts = Counter(record["status"] for record in records)
    event_counts = Counter(
        event["event_type"] for record in records for event in record["events"]
    )
    operation_counts = Counter(
        operation["kind"] for record in records for operation in record["operations"]
    )
    selector_counts = Counter(
        selector["kind"] for record in records for selector in record["target"]["selectors"]
    )
    title_match = re.search(r"(?is)<title>(.*?)</title>", source)

    return {
        "format": "iupac-blue-book-correction-overlays",
        "format_version": "1.0.0",
        "conversion_stage": "lossless_correction_overlay",
        "source_document": {
            "title": _compact_text(visible_text(title_match.group(1))) if title_match else "Web Blue Book errata",
            "source_path": source_path_text,
            "source_url": SOURCE_URL,
            "source_sha256": sha256_bytes(source_bytes),
            "source_byte_count": len(source_bytes),
            "encoding": "UTF-8",
            "segmentation": "dated-event-header source ranges",
            "dom_model": "canonical correction-overlay DOM over exact source ranges",
            "toolchain": {"python": platform.python_version(), "lxml": lxml.__version__},
        },
        "record_count": len(records),
        "counters": {
            "status": {name: status_counts[name] for name in ("applied", "deleted", "replaced")},
            "event_type": {name: event_counts[name] for name in EVENT_TYPES},
            "operation_kind": {
                name: operation_counts[name]
                for name in ("addition", "deletion", "instruction", "replacement")
            },
            "selector_kind": {
                name: selector_counts[name]
                for name in ("appendix", "contents", "figure", "location", "page", "rule", "table")
            },
        },
        "records": records,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract official Blue Book web corrections as lossless typed overlays."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    corpus = extract_correction_overlays(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(corpus))
    print(
        f"Wrote {corpus['record_count']} correction overlays to {args.output} "
        f"({corpus['counters']['operation_kind']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
