from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

try:
    from scripts.document_node_store import (
        DEFAULT_STORE as DEFAULT_DOCUMENT_NODE_STORE,
        DocumentNodeStoreError,
        load_document_nodes,
    )
except ModuleNotFoundError:  # Support direct script execution.
    from document_node_store import (  # type: ignore[no-redef]
        DEFAULT_STORE as DEFAULT_DOCUMENT_NODE_STORE,
        DocumentNodeStoreError,
        load_document_nodes,
    )

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = ROOT / ".cache" / "bluebook_html"
DEFAULT_ARTIFACT = DEFAULT_DOCUMENT_NODE_STORE

OFFICIAL_DOCUMENTS: tuple[tuple[str, str], ...] = (
    ("P-1", "P1.html"),
    ("P-2", "P2.html"),
    ("P-3", "P3.html"),
    ("P-4", "P4.html"),
    ("P-5", "P5.html"),
    ("P-6a", "P6.html"),
    ("P-6b", "P6a.html"),
    ("P-7", "P7.html"),
    ("P-8", "P8.html"),
    ("P-9", "P9.html"),
    ("P-10", "P10.html"),
)

PINNED_COUNTS: dict[str, int] = {
    "table": 567,
    "tr": 3782,
    "td": 9100,
    "th": 0,
    "img": 5371,
    "correction_img": 190,
    "footnote_candidate": 7,
}

TARGET_TAGS = frozenset({"table", "tr", "td", "th", "img"})
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

RULE_ID_BYTES = rb"P-\d+(?:\.\d+)*(?:\([a-z0-9]+\))?"
ACTIVE_RULE_ANCHOR_RE = re.compile(
    rb"<a\s+name\s*=\s*[\"']?([^\"'\s>]+)[\"']?[^>]*>\s*"
    rb"(?:<[^>]+>\s*)*(" + RULE_ID_BYTES + rb")\b",
    re.IGNORECASE,
)
COMMENT_RE = re.compile(rb"<!--.*?-->", re.DOTALL)
FOOTER_RE = re.compile(rb"(?im)^[ \t]*<hr\b")
BODY_END_RE = re.compile(rb"(?i)</body\s*>")
TAG_NAME_RE = re.compile(rb"<\s*/?\s*([A-Za-z][A-Za-z0-9:-]*)")
ATTRIBUTE_TEMPLATE = rb"(?i)(?:\s|<)%s\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+))"
PHYSICAL_ID_RE = re.compile(
    r"^(?P<rule>P-\d+(?:\.\d+)*(?:\([a-z0-9]+\))?):"
    r"(?P<tag>table|tr|td|th|img):(?P<ordinal>[0-9]{4,})$"
)
FOOTNOTE_CANDIDATE_RE = re.compile(
    rb"(?im)^[ \t]*(?P<marker>"
    rb"\*{1,3}"
    rb"|&#(?:0*134|0*135|0*8224|0*8225);?"
    rb"|&#[xX](?:0*86|0*87|0*2020|0*2021);?"
    rb"|&(?:dagger|Dagger);?"
    rb"|\xe2\x80[\xa0\xa1]"
    rb"|[\x86\x87]"
    rb")(?=[ \t]+\S)",
)


class AuditError(ValueError):
    """The raw census and its purported artifact do not agree exactly."""


@dataclass(frozen=True, slots=True)
class Fragment:
    document_id: str
    cache_path: str
    rule_id: str
    anchor: str
    ordinal: int
    start_byte: int
    end_byte: int
    anchor_start_byte: int
    raw_sha256: str
    active_sha256: str

    @property
    def byte_count(self) -> int:
        return self.end_byte - self.start_byte


@dataclass(frozen=True, slots=True)
class Occurrence:
    occurrence_id: str
    kind: str
    document_id: str
    cache_path: str
    rule_id: str
    fragment_ordinal: int
    ordinal: int
    fragment_start_byte: int
    fragment_end_byte: int
    document_start_byte: int
    document_end_byte: int
    raw_sha256: str
    correction: bool = False
    correction_href: str | None = None

    @property
    def fragment_span(self) -> tuple[int, int]:
        return self.fragment_start_byte, self.fragment_end_byte

    @property
    def document_span(self) -> tuple[int, int]:
        return self.document_start_byte, self.document_end_byte


@dataclass(frozen=True, slots=True)
class DocumentCensus:
    document_id: str
    cache_path: str
    raw: bytes = field(repr=False)
    fragments: tuple[Fragment, ...]
    occurrences: tuple[Occurrence, ...]

    @property
    def source_sha256(self) -> str:
        return sha256_bytes(self.raw)


@dataclass(frozen=True, slots=True)
class CorpusCensus:
    documents: tuple[DocumentCensus, ...]

    @property
    def fragments(self) -> tuple[Fragment, ...]:
        return tuple(
            fragment for document in self.documents for fragment in document.fragments
        )

    @property
    def occurrences(self) -> tuple[Occurrence, ...]:
        return tuple(
            occurrence
            for document in self.documents
            for occurrence in document.occurrences
        )

    def counts(self) -> dict[str, int]:
        counts = Counter(occurrence.kind for occurrence in self.occurrences)
        counts["correction_img"] = sum(
            occurrence.kind == "img" and occurrence.correction
            for occurrence in self.occurrences
        )
        return {key: counts[key] for key in PINNED_COUNTS}


@dataclass(frozen=True, slots=True)
class AuditReport:
    document_count: int
    fragment_count: int
    artifact_occurrence_count: int
    primary_occurrence_count: int
    aggregate_occurrence_count: int
    alias_occurrence_reference_count: int
    correction_event_count: int
    footnote_marker_count: int
    counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_count": self.document_count,
            "fragment_count": self.fragment_count,
            "artifact_occurrence_count": self.artifact_occurrence_count,
            "primary_occurrence_count": self.primary_occurrence_count,
            "aggregate_occurrence_count": self.aggregate_occurrence_count,
            "alias_occurrence_reference_count": self.alias_occurrence_reference_count,
            "correction_event_count": self.correction_event_count,
            "footnote_marker_count": self.footnote_marker_count,
            "counts": self.counts,
        }


@dataclass(slots=True)
class _Element:
    tag: str
    start: int
    start_tag_end: int
    end: int
    occurrence_id: str | None
    ordinal: int | None
    attributes: dict[str, bytes]
    correction: bool = False
    correction_href: str | None = None


@dataclass(frozen=True, slots=True)
class _ArtifactOccurrence:
    occurrence_id: str
    ownership: str
    document_span: tuple[int, int]
    document_ranges: tuple[tuple[int, int], ...]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def mask_comments(raw: bytes) -> bytes:
    """Replace comment content with spaces while preserving every byte offset."""

    masked = bytearray(raw)
    for match in COMMENT_RE.finditer(raw):
        for index in range(match.start(), match.end()):
            if masked[index] not in {10, 13}:
                masked[index] = 32
    return bytes(masked)


def strip_comments(raw: bytes) -> bytes:
    return COMMENT_RE.sub(b"", raw)


def _line_start(raw: bytes, offset: int) -> int:
    return raw.rfind(b"\n", 0, offset) + 1


def _last_fragment_end(masked: bytes, anchor_end: int) -> int:
    footer = FOOTER_RE.search(masked, anchor_end)
    body_end = BODY_END_RE.search(masked, anchor_end)
    candidates = [match.start() for match in (footer, body_end) if match]
    return min(candidates) if candidates else len(masked)


def discover_fragments(
    raw: bytes, document_id: str, cache_path: str
) -> tuple[Fragment, ...]:
    masked = mask_comments(raw)
    matches = list(ACTIVE_RULE_ANCHOR_RE.finditer(masked))
    if not matches:
        raise AuditError(f"{cache_path}: no active rule anchors found")

    starts = [_line_start(raw, match.start()) for match in matches]
    if len(starts) != len(set(starts)):
        raise AuditError(f"{cache_path}: active rule anchors share a source line")

    fragments: list[Fragment] = []
    seen_rule_ids: set[str] = set()
    for index, (match, start) in enumerate(zip(matches, starts)):
        rule_id = match.group(2).decode("ascii")
        if rule_id in seen_rule_ids:
            raise AuditError(f"{cache_path}: duplicate active rule id {rule_id}")
        seen_rule_ids.add(rule_id)
        end = (
            starts[index + 1]
            if index + 1 < len(starts)
            else _last_fragment_end(masked, match.end())
        )
        if not 0 <= start < end <= len(raw):
            raise AuditError(
                f"{cache_path}:{rule_id}: invalid fragment span [{start}, {end})"
            )
        fragment_raw = raw[start:end]
        fragments.append(
            Fragment(
                document_id=document_id,
                cache_path=cache_path,
                rule_id=rule_id,
                anchor=match.group(1).decode("ascii", errors="replace"),
                ordinal=index + 1,
                start_byte=start,
                end_byte=end,
                anchor_start_byte=match.start(),
                raw_sha256=sha256_bytes(fragment_raw),
                active_sha256=sha256_bytes(strip_comments(fragment_raw)),
            )
        )
    return tuple(fragments)


def _find_tag_end(raw: bytes, start: int) -> int:
    # This is intentionally a physical lexer, not an HTML repair parser. The
    # official P-15.4.0 source has a stray quote before a literal tag boundary;
    # honoring that quote would swallow the following malformed <tr> occurrence.
    closing_bracket = raw.find(b">", start + 1)
    return len(raw) if closing_bracket < 0 else closing_bracket + 1


def _iter_tags(raw: bytes) -> Iterator[tuple[str, int, int, str, bool]]:
    """Yield active start/end tags without parsing or normalizing the HTML."""

    index = 0
    while index < len(raw):
        start = raw.find(b"<", index)
        if start < 0:
            return
        if raw.startswith(b"<!--", start):
            comment_end = raw.find(b"-->", start + 4)
            index = len(raw) if comment_end < 0 else comment_end + 3
            continue
        if start + 1 >= len(raw):
            return
        following = raw[start + 1]
        if following not in {33, 47, 63} and not (
            65 <= following <= 90 or 97 <= following <= 122
        ):
            index = start + 1
            continue
        end = _find_tag_end(raw, start)
        token = raw[start:end]
        name_match = TAG_NAME_RE.match(token)
        if name_match is None or token.startswith((b"<!", b"<?")):
            index = end
            continue
        tag = name_match.group(1).decode("ascii").lower()
        closing = re.match(rb"<\s*/", token) is not None
        self_closing = bool(re.search(rb"/\s*>\s*$", token)) or tag in VOID_TAGS
        yield ("end" if closing else "start", start, end, tag, self_closing)
        index = end


def _attribute(start_tag: bytes, name: str) -> bytes | None:
    pattern = re.compile(ATTRIBUTE_TEMPLATE % re.escape(name.encode("ascii")))
    match = pattern.search(start_tag)
    if match is None:
        return None
    return next(group for group in match.groups() if group is not None)


def _attributes(start_tag: bytes, names: Sequence[str]) -> dict[str, bytes]:
    return {
        name: value
        for name in names
        if (value := _attribute(start_tag, name)) is not None
    }


def _normalized_path(value: bytes | None) -> str:
    if value is None:
        return ""
    decoded = value.decode("ascii", errors="ignore").replace("\\", "/").lower()
    return decoded.split("#", 1)[0].split("?", 1)[0]


def _scan_elements(
    raw_fragment: bytes,
    fragment: Fragment,
) -> list[Occurrence]:
    elements: list[_Element] = []
    stack: list[_Element] = []
    ordinals: Counter[str] = Counter()

    def close_open(tags: set[str], offset: int) -> None:
        match_index = next(
            (
                position
                for position in range(len(stack) - 1, -1, -1)
                if stack[position].tag in tags
            ),
            None,
        )
        if match_index is None:
            return
        for dangling in stack[match_index:]:
            dangling.end = offset
        del stack[match_index:]

    for kind, start, end, tag, self_closing in _iter_tags(raw_fragment):
        if kind == "start":
            if tag == "p":
                close_open({"p"}, start)
            elif tag == "tr":
                close_open({"td", "th", "tr"}, start)
            elif tag in {"td", "th"}:
                close_open({"td", "th"}, start)
            elif tag == "li":
                close_open({"li"}, start)

            ordinal: int | None = None
            occurrence_id: str | None = None
            if tag in TARGET_TAGS:
                ordinals[tag] += 1
                ordinal = ordinals[tag]
                occurrence_id = f"{fragment.rule_id}:{tag}:{ordinal:04d}"

            start_tag = raw_fragment[start:end]
            attributes = _attributes(start_tag, ("src", "href"))
            correction = False
            correction_href: str | None = None
            if tag == "img":
                src_path = _normalized_path(attributes.get("src"))
                correction = src_path.endswith("/alter.gif") or src_path == "alter.gif"
                if correction:
                    anchor = next(
                        (element for element in reversed(stack) if element.tag == "a"),
                        None,
                    )
                    href = anchor.attributes.get("href") if anchor is not None else None
                    correction_href = (
                        href.decode("ascii", errors="replace") if href is not None else None
                    )
                    href_path = _normalized_path(href)
                    if not (
                        href_path in {"changes.html", "changes2.html"}
                        or href_path.endswith(("/changes.html", "/changes2.html"))
                    ):
                        raise AuditError(
                            f"{fragment.rule_id}: correction image at byte {start} "
                            "is not linked to an official changes page"
                        )

            element = _Element(
                tag=tag,
                start=start,
                start_tag_end=end,
                end=end if self_closing else len(raw_fragment),
                occurrence_id=occurrence_id,
                ordinal=ordinal,
                attributes=attributes,
                correction=correction,
                correction_href=correction_href,
            )
            elements.append(element)
            if not self_closing:
                stack.append(element)
            continue

        match_index = next(
            (
                position
                for position in range(len(stack) - 1, -1, -1)
                if stack[position].tag == tag
            ),
            None,
        )
        if match_index is None:
            continue
        for dangling in stack[match_index + 1 :]:
            dangling.end = start
        stack[match_index].end = end
        del stack[match_index:]

    occurrences: list[Occurrence] = []
    for element in elements:
        if element.occurrence_id is None or element.ordinal is None:
            continue
        exact = raw_fragment[element.start : element.end]
        occurrences.append(
            Occurrence(
                occurrence_id=element.occurrence_id,
                kind=element.tag,
                document_id=fragment.document_id,
                cache_path=fragment.cache_path,
                rule_id=fragment.rule_id,
                fragment_ordinal=fragment.ordinal,
                ordinal=element.ordinal,
                fragment_start_byte=element.start,
                fragment_end_byte=element.end,
                document_start_byte=fragment.start_byte + element.start,
                document_end_byte=fragment.start_byte + element.end,
                raw_sha256=sha256_bytes(exact),
                correction=element.correction,
                correction_href=element.correction_href,
            )
        )
    return occurrences


def _scan_footnote_candidates(
    raw_fragment: bytes,
    fragment: Fragment,
) -> list[Occurrence]:
    masked = mask_comments(raw_fragment)
    candidates: list[Occurrence] = []
    for ordinal, match in enumerate(FOOTNOTE_CANDIDATE_RE.finditer(masked), start=1):
        start, end = match.span("marker")
        exact = raw_fragment[start:end]
        candidates.append(
            Occurrence(
                occurrence_id=(
                    f"{fragment.rule_id}:footnote-candidate:{ordinal:04d}"
                ),
                kind="footnote_candidate",
                document_id=fragment.document_id,
                cache_path=fragment.cache_path,
                rule_id=fragment.rule_id,
                fragment_ordinal=fragment.ordinal,
                ordinal=ordinal,
                fragment_start_byte=start,
                fragment_end_byte=end,
                document_start_byte=fragment.start_byte + start,
                document_end_byte=fragment.start_byte + end,
                raw_sha256=sha256_bytes(exact),
            )
        )
    return candidates


def scan_document(path: Path, document_id: str) -> DocumentCensus:
    raw = path.read_bytes()
    fragments = discover_fragments(raw, document_id, path.name)
    occurrences: list[Occurrence] = []
    for fragment in fragments:
        fragment_raw = raw[fragment.start_byte : fragment.end_byte]
        occurrences.extend(_scan_elements(fragment_raw, fragment))
        occurrences.extend(_scan_footnote_candidates(fragment_raw, fragment))
    return DocumentCensus(
        document_id=document_id,
        cache_path=path.name,
        raw=raw,
        fragments=fragments,
        occurrences=tuple(occurrences),
    )


def build_census(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    documents: Sequence[tuple[str, str]] = OFFICIAL_DOCUMENTS,
) -> CorpusCensus:
    missing = [filename for _, filename in documents if not (cache_dir / filename).is_file()]
    if missing:
        raise AuditError(f"missing official HTML cache files: {', '.join(missing)}")
    census = CorpusCensus(
        tuple(
            scan_document(cache_dir / filename, document_id)
            for document_id, filename in documents
        )
    )
    verify_replay_and_uniqueness(census)
    return census


def verify_replay_and_uniqueness(census: CorpusCensus) -> None:
    ids: set[str] = set()
    spans: set[tuple[str, int, int, str]] = set()
    fragment_ids: set[str] = set()
    for document in census.documents:
        for fragment in document.fragments:
            if fragment.rule_id in fragment_ids:
                raise AuditError(f"duplicate active fragment rule id {fragment.rule_id}")
            fragment_ids.add(fragment.rule_id)
            exact = document.raw[fragment.start_byte : fragment.end_byte]
            if sha256_bytes(exact) != fragment.raw_sha256:
                raise AuditError(f"{fragment.rule_id}: fragment byte replay failed")
            if sha256_bytes(strip_comments(exact)) != fragment.active_sha256:
                raise AuditError(f"{fragment.rule_id}: active fragment replay failed")

        fragments = {fragment.rule_id: fragment for fragment in document.fragments}
        for occurrence in document.occurrences:
            if occurrence.occurrence_id in ids:
                raise AuditError(f"duplicate raw occurrence id {occurrence.occurrence_id}")
            ids.add(occurrence.occurrence_id)
            fragment = fragments[occurrence.rule_id]
            if occurrence.document_start_byte != (
                fragment.start_byte + occurrence.fragment_start_byte
            ) or occurrence.document_end_byte != (
                fragment.start_byte + occurrence.fragment_end_byte
            ):
                raise AuditError(
                    f"{occurrence.occurrence_id}: fragment/document offsets diverge"
                )
            if not (
                0
                <= occurrence.fragment_start_byte
                < occurrence.fragment_end_byte
                <= fragment.byte_count
            ):
                raise AuditError(f"{occurrence.occurrence_id}: invalid occurrence span")
            exact = document.raw[
                occurrence.document_start_byte : occurrence.document_end_byte
            ]
            if sha256_bytes(exact) != occurrence.raw_sha256:
                raise AuditError(f"{occurrence.occurrence_id}: byte replay failed")
            span_key = (
                document.document_id,
                occurrence.document_start_byte,
                occurrence.document_end_byte,
                occurrence.kind,
            )
            if span_key in spans:
                raise AuditError(
                    f"{occurrence.occurrence_id}: duplicate raw occurrence span"
                )
            spans.add(span_key)


def assert_pinned_counts(
    census: CorpusCensus,
    expected: Mapping[str, int] = PINNED_COUNTS,
) -> dict[str, int]:
    observed = census.counts()
    expected_dict = dict(expected)
    if observed != expected_dict:
        differences = ", ".join(
            f"{key}: expected {expected_dict.get(key)!r}, observed {observed.get(key)!r}"
            for key in sorted(set(expected_dict) | set(observed))
            if expected_dict.get(key) != observed.get(key)
        )
        raise AuditError(f"raw physical census changed ({differences})")
    return observed


def artifact_metric_counts(
    occurrences: Sequence[Occurrence],
) -> dict[str, int]:
    counts = Counter(occurrence.kind for occurrence in occurrences)
    return {
        "physical_table_occurrence_count": counts["table"],
        "physical_row_occurrence_count": counts["tr"],
        "physical_cell_occurrence_count": counts["td"] + counts["th"],
        "physical_image_occurrence_count": counts["img"],
        "correction_event_count": sum(
            occurrence.kind == "img" and occurrence.correction
            for occurrence in occurrences
        ),
        "footnote_block_count": counts["footnote_candidate"],
    }


def _validate_artifact_metrics(
    value: Any,
    expected: Mapping[str, int],
    label: str,
) -> None:
    if not isinstance(value, dict):
        raise AuditError(f"{label}: physical source metrics are missing")
    differences = {
        key: (expected_value, value.get(key))
        for key, expected_value in expected.items()
        if value.get(key) != expected_value
    }
    if differences:
        raise AuditError(f"{label}: physical source metrics changed ({differences})")


def _walk_dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _require_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise AuditError(f"{label}: expected an integer byte offset")
    return value


def _validate_ranges(
    ranges: Any,
    document: DocumentCensus,
    fragment: Fragment,
    label: str,
    *,
    check_manifest: str | None = None,
) -> tuple[tuple[int, int], ...]:
    if not isinstance(ranges, list) or not ranges:
        raise AuditError(f"{label}: source ranges are missing")
    replayed: list[tuple[int, int]] = []
    previous: tuple[int, int] | None = None
    for index, part in enumerate(ranges):
        if not isinstance(part, dict):
            raise AuditError(f"{label}: source range {index} is not an object")
        fragment_start = _require_int(
            part.get("fragment_start_byte"), f"{label}: range {index} fragment start"
        )
        fragment_end = _require_int(
            part.get("fragment_end_byte"), f"{label}: range {index} fragment end"
        )
        document_start = _require_int(
            part.get("document_start_byte"), f"{label}: range {index} document start"
        )
        document_end = _require_int(
            part.get("document_end_byte"), f"{label}: range {index} document end"
        )
        if not 0 <= fragment_start < fragment_end <= fragment.byte_count:
            raise AuditError(f"{label}: range {index} lies outside its fragment")
        if document_start != fragment.start_byte + fragment_start or document_end != (
            fragment.start_byte + fragment_end
        ):
            raise AuditError(f"{label}: range {index} uses inconsistent offset bases")
        current = (document_start, document_end)
        if previous is not None and current < previous:
            raise AuditError(f"{label}: source ranges are not ordered")
        previous = current
        exact = document.raw[document_start:document_end]
        if sha256_bytes(exact) != part.get("raw_sha256"):
            raise AuditError(f"{label}: range {index} byte replay failed")
        replayed.append(current)
    if check_manifest is not None and sha256_bytes(canonical_json_bytes(ranges)) != check_manifest:
        raise AuditError(f"{label}: source manifest digest failed")
    return tuple(replayed)


def _validate_provenance(
    source: Any,
    document: DocumentCensus,
    fragment: Fragment,
    label: str,
) -> tuple[
    str,
    str | None,
    tuple[int, int],
    tuple[tuple[int, int], ...],
]:
    if not isinstance(source, dict):
        raise AuditError(f"{label}: source provenance is missing")
    ownership = source.get("ownership")
    if not isinstance(ownership, dict):
        raise AuditError(f"{label}: source ownership is missing")
    kind = ownership.get("kind")
    owner_ref = ownership.get("owner_ref")
    if kind not in {"primary", "alias", "aggregate"}:
        raise AuditError(f"{label}: unknown source ownership {kind!r}")
    ranges = _validate_ranges(
        source.get("parts"),
        document,
        fragment,
        label,
        check_manifest=source.get("manifest_sha256"),
    )
    span = ranges[0][0], ranges[-1][1]
    return kind, owner_ref, span, ranges


def _active_document_ranges(
    raw: bytes, start: int, end: int
) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    cursor = start
    for match in COMMENT_RE.finditer(raw, start, end):
        if cursor < match.start():
            ranges.append((cursor, match.start()))
        cursor = match.end()
    if cursor < end:
        ranges.append((cursor, end))
    return tuple(ranges)


def _artifact_occurrences_in_fragment(
    artifact_fragment: dict[str, Any],
    document: DocumentCensus,
    fragment: Fragment,
) -> tuple[
    list[_ArtifactOccurrence],
    set[str],
    set[tuple[str, int, int]],
]:
    occurrences: list[_ArtifactOccurrence] = []
    correction_ids: set[str] = set()
    footnote_marker_spans: set[tuple[str, int, int]] = set()

    for value in _walk_dicts(artifact_fragment.get("nodes", [])):
        occurrence_id = value.get("occurrence_id")
        match = PHYSICAL_ID_RE.fullmatch(occurrence_id) if isinstance(occurrence_id, str) else None
        if match is not None:
            if match.group("rule") != fragment.rule_id:
                raise AuditError(
                    f"{occurrence_id}: artifact occurrence is in fragment {fragment.rule_id}"
                )
            ownership, owner_ref, span, ranges = _validate_provenance(
                value.get("source"), document, fragment, occurrence_id
            )
            if ownership in {"primary", "alias"} and owner_ref != occurrence_id:
                raise AuditError(
                    f"{occurrence_id}: {ownership} source owner is {owner_ref!r}"
                )
            occurrences.append(
                _ArtifactOccurrence(occurrence_id, ownership, span, ranges)
            )

        if value.get("kind") == "source_event" and value.get("event_kind") == "correction":
            event_id = value.get("occurrence_id")
            event_match = PHYSICAL_ID_RE.fullmatch(event_id) if isinstance(event_id, str) else None
            if event_match is None or event_match.group("tag") != "img":
                raise AuditError(
                    f"{fragment.rule_id}: correction event lacks a physical image id"
                )
            if event_id in correction_ids:
                raise AuditError(f"{event_id}: duplicate correction event")
            correction_ids.add(event_id)

        marker = value.get("marker")
        field_sources = value.get("field_sources")
        marker_source = (
            field_sources.get("marker") if isinstance(field_sources, dict) else None
        )
        if marker_source is not None and isinstance(marker, str) and re.fullmatch(
            r"\*{1,3}|[\u2020\u2021]+", marker
        ):
            ownership = marker_source.get("ownership")
            if not isinstance(ownership, dict) or ownership.get("kind") != "primary":
                raise AuditError(f"{fragment.rule_id}: footnote marker is not primary")
            ranges = _validate_ranges(
                marker_source.get("mapping"),
                document,
                fragment,
                f"{fragment.rule_id}: footnote marker",
            )
            span = ranges[0][0], ranges[-1][1]
            span_key = document.document_id, span[0], span[1]
            if span_key in footnote_marker_spans:
                raise AuditError(
                    f"{fragment.rule_id}: duplicate footnote marker source span {span}"
                )
            footnote_marker_spans.add(span_key)

    return occurrences, correction_ids, footnote_marker_spans


def reconcile_artifact(
    census: CorpusCensus,
    artifact: Mapping[str, Any],
    expected_counts: Mapping[str, int] = PINNED_COUNTS,
) -> AuditReport:
    artifact_documents = artifact.get("documents")
    if not isinstance(artifact_documents, list):
        raise AuditError("document artifact has no documents array")
    expected_documents = {document.document_id: document for document in census.documents}
    observed_ids = [document.get("document_id") for document in artifact_documents]
    if set(observed_ids) != set(expected_documents) or len(observed_ids) != len(set(observed_ids)):
        raise AuditError("document artifact does not cover each official chapter exactly once")
    _validate_artifact_metrics(
        artifact.get("metrics"),
        artifact_metric_counts(census.occurrences),
        "corpus",
    )

    raw_by_id = {
        occurrence.occurrence_id: occurrence
        for occurrence in census.occurrences
        if occurrence.kind in TARGET_TAGS
    }
    represented_by_id: dict[str, _ArtifactOccurrence] = {}
    aliases: list[_ArtifactOccurrence] = []
    artifact_correction_ids: set[str] = set()
    artifact_footnote_spans: set[tuple[str, int, int]] = set()

    for artifact_document in artifact_documents:
        document_id = artifact_document.get("document_id")
        document = expected_documents[document_id]
        if artifact_document.get("cache_path") != document.cache_path:
            raise AuditError(f"{document_id}: artifact cache path changed")
        if artifact_document.get("source_byte_count") != len(document.raw):
            raise AuditError(f"{document_id}: artifact source byte count changed")
        if artifact_document.get("source_sha256") != document.source_sha256:
            raise AuditError(f"{document_id}: artifact source digest changed")
        _validate_artifact_metrics(
            artifact_document.get("source_metrics"),
            artifact_metric_counts(document.occurrences),
            document_id,
        )

        artifact_fragments = artifact_document.get("fragments")
        if not isinstance(artifact_fragments, list) or len(artifact_fragments) != len(
            document.fragments
        ):
            raise AuditError(f"{document_id}: artifact fragment coverage changed")
        for artifact_fragment, fragment in zip(artifact_fragments, document.fragments):
            if artifact_fragment.get("rule_id") != fragment.rule_id:
                raise AuditError(f"{document_id}: artifact fragment order changed")
            if artifact_fragment.get("anchor") != fragment.anchor:
                raise AuditError(f"{fragment.rule_id}: artifact anchor changed")
            if artifact_fragment.get("ordinal") != fragment.ordinal:
                raise AuditError(f"{fragment.rule_id}: artifact ordinal changed")
            source = artifact_fragment.get("source")
            expected_source = {
                "offset_unit": "byte",
                "start_byte": fragment.start_byte,
                "end_byte": fragment.end_byte,
                "anchor_start_byte": fragment.anchor_start_byte,
                "raw_sha256": fragment.raw_sha256,
                "active_sha256": fragment.active_sha256,
            }
            if not isinstance(source, dict) or any(
                source.get(key) != value for key, value in expected_source.items()
            ):
                raise AuditError(f"{fragment.rule_id}: artifact fragment source changed")

            found, correction_ids, marker_spans = _artifact_occurrences_in_fragment(
                artifact_fragment, document, fragment
            )
            artifact_correction_ids.update(correction_ids)
            overlap = artifact_footnote_spans & marker_spans
            if overlap:
                raise AuditError(f"duplicate artifact footnote marker spans: {sorted(overlap)}")
            artifact_footnote_spans.update(marker_spans)
            for occurrence in found:
                if occurrence.ownership in {"primary", "aggregate"}:
                    if occurrence.occurrence_id in represented_by_id:
                        raise AuditError(
                            f"{occurrence.occurrence_id}: more than one artifact owner"
                        )
                    represented_by_id[occurrence.occurrence_id] = occurrence
                elif occurrence.ownership == "alias":
                    aliases.append(occurrence)

    raw_ids = set(raw_by_id)
    artifact_ids = set(represented_by_id)
    if artifact_ids != raw_ids:
        missing = sorted(raw_ids - artifact_ids)[:8]
        unexpected = sorted(artifact_ids - raw_ids)[:8]
        raise AuditError(
            "artifact physical occurrence ids changed "
            f"(missing={missing}, unexpected={unexpected})"
        )
    for occurrence_id, artifact_occurrence in represented_by_id.items():
        raw_occurrence = raw_by_id[occurrence_id]
        expected_ranges = _active_document_ranges(
            expected_documents[raw_occurrence.document_id].raw,
            raw_occurrence.document_start_byte,
            raw_occurrence.document_end_byte,
        )
        if artifact_occurrence.ownership == "primary" and (
            artifact_occurrence.document_span != raw_occurrence.document_span
            or artifact_occurrence.document_ranges != expected_ranges
        ):
            raise AuditError(
                f"{occurrence_id}: artifact span {artifact_occurrence.document_span} "
                f"does not match raw span {raw_occurrence.document_span}"
            )
        if artifact_occurrence.ownership == "aggregate" and not all(
            expected_range in artifact_occurrence.document_ranges
            for expected_range in expected_ranges
        ):
            raise AuditError(
                f"{occurrence_id}: aggregate source does not retain the complete "
                "active raw occurrence ranges"
            )
    for alias in aliases:
        owner = represented_by_id.get(alias.occurrence_id)
        if owner is None:
            raise AuditError(f"{alias.occurrence_id}: alias has no artifact owner")
        if alias.document_span != owner.document_span:
            raise AuditError(f"{alias.occurrence_id}: alias span differs from its owner")
        if alias.document_ranges != owner.document_ranges:
            raise AuditError(f"{alias.occurrence_id}: alias ranges differ from its owner")

    raw_correction_ids = {
        occurrence.occurrence_id
        for occurrence in raw_by_id.values()
        if occurrence.kind == "img" and occurrence.correction
    }
    if artifact_correction_ids != raw_correction_ids:
        raise AuditError("artifact correction events do not match raw correction images")

    raw_footnote_spans = {
        (
            occurrence.document_id,
            occurrence.document_start_byte,
            occurrence.document_end_byte,
        )
        for occurrence in census.occurrences
        if occurrence.kind == "footnote_candidate"
    }
    if artifact_footnote_spans != raw_footnote_spans:
        raise AuditError(
            "artifact footnote marker mappings do not match raw leading candidates"
        )

    counts = assert_pinned_counts(census, expected_counts)
    return AuditReport(
        document_count=len(census.documents),
        fragment_count=len(census.fragments),
        artifact_occurrence_count=len(represented_by_id),
        primary_occurrence_count=sum(
            occurrence.ownership == "primary"
            for occurrence in represented_by_id.values()
        ),
        aggregate_occurrence_count=sum(
            occurrence.ownership == "aggregate"
            for occurrence in represented_by_id.values()
        ),
        alias_occurrence_reference_count=len(aliases),
        correction_event_count=len(artifact_correction_ids),
        footnote_marker_count=len(artifact_footnote_spans),
        counts=counts,
    )


def audit(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    artifact_path: Path = DEFAULT_ARTIFACT,
) -> AuditReport:
    census = build_census(cache_dir)
    assert_pinned_counts(census)
    try:
        artifact = load_document_nodes(artifact_path)
    except FileNotFoundError as error:
        raise AuditError(f"document artifact is missing: {artifact_path}") from error
    except (DocumentNodeStoreError, json.JSONDecodeError) as error:
        raise AuditError(f"document artifact is invalid JSON: {error}") from error
    return reconcile_artifact(census, artifact)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Independently audit physical HTML occurrences against the document-node artifact."
        )
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--json", action="store_true", help="print the audit report as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit(args.cache_dir, args.artifact)
    except AuditError as error:
        print(f"HTML physical occurrence audit failed: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        counts = ", ".join(f"{key}={value}" for key, value in report.counts.items())
        print(
            f"Audited {report.fragment_count} active fragments across "
            f"{report.document_count} documents: {counts}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
