from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = ROOT / ".cache" / "bluebook_html"
DEFAULT_SCHEMA = ROOT / "data" / "bluebook_reference_occurrences.schema.json"
DEFAULT_CONTEXT_CHARACTERS = 120

CHAPTER_FILES: tuple[tuple[str, str], ...] = (
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

RULE_ID_BYTES = rb"P-\d+(?:\.\d+)*(?:\([a-z0-9]+\))?"
ACTIVE_RULE_ANCHOR_RE = re.compile(
    rb"<a\s+name\s*=\s*[\"']?([^\"'\s>]+)[\"']?[^>]*>\s*"
    rb"(?:<[^>]+>\s*)*(" + RULE_ID_BYTES + rb")\b",
    re.IGNORECASE,
)
RULE_REFERENCE_RE = re.compile(
    rb"(?<![A-Za-z0-9])(" + RULE_ID_BYTES + rb")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
COMMENT_RE = re.compile(rb"<!--.*?-->", re.DOTALL)
FOOTER_RE = re.compile(rb"(?im)^[ \t]*<hr\b")
BODY_END_RE = re.compile(rb"(?i)</body\s*>")
TAG_NAME_RE = re.compile(
    rb"<\s*(?P<closing>/)?\s*(?P<tag>[A-Za-z][A-Za-z0-9:-]*)"
)
ATTRIBUTE_RE = re.compile(
    rb"(?P<name>[^\s\"'<>/=]+)"
    rb"(?:\s*=\s*(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'|"
    rb"(?P<bare>[^\s\"'=<>`]+)))?"
)
ENTITY_RE = re.compile(
    rb"&(?:#[0-9]{1,8}|#[xX][0-9A-Fa-f]{1,8}|[A-Za-z][A-Za-z0-9]{1,31});?"
)

VOID_TAGS = {
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
VISIBLE_BREAK_TAGS = {
    "address",
    "blockquote",
    "br",
    "center",
    "dd",
    "div",
    "dl",
    "dt",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
IMPLICIT_LINK_CLOSE_TAGS = {
    "address",
    "blockquote",
    "center",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
CP1252_CONTROL_MAP = {
    code: bytes([code]).decode("cp1252")
    for code in range(0x80, 0xA0)
    if code not in {0x81, 0x8D, 0x8F, 0x90, 0x9D}
}


@dataclass(frozen=True)
class Lexeme:
    kind: str
    start: int
    end: int
    tag: str | None = None
    closing: bool = False
    self_closing: bool = False


@dataclass(frozen=True)
class AttributeValue:
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class RuleFragment:
    document_id: str
    cache_path: str
    rule_id: str
    anchor: str
    ordinal: int
    start: int
    end: int
    heading_start: int
    heading_end: int
    raw_sha256: str
    active_sha256: str


@dataclass
class SourceArtifact:
    artifact_id: str
    document_id: str
    cache_path: str
    path: Path
    source_url: str
    raw: bytes
    source_sha256: str
    fragments: list[RuleFragment]


@dataclass(frozen=True)
class HrefResolution:
    resolved_url: str
    document: SourceArtifact | None
    anchor: str | None
    fragment_candidates: tuple[RuleFragment, ...]


@dataclass
class Link:
    ordinal: int
    start: int
    opening_end: int
    href: AttributeValue
    resolution: HrefResolution
    end: int | None = None
    represented_targets: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class VisibleChar:
    value: str
    raw_start: int | None
    raw_end: int | None


@dataclass
class ExtractionIndex:
    artifacts: list[SourceArtifact]
    artifacts_by_filename: dict[str, SourceArtifact]
    fragments_by_rule_id: dict[str, RuleFragment]
    fragments_by_anchor: dict[tuple[str, str], tuple[RuleFragment, ...]]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _mask_comments(raw: bytes) -> bytes:
    masked = bytearray(raw)
    for match in COMMENT_RE.finditer(raw):
        for offset in range(match.start(), match.end()):
            if masked[offset] not in {10, 13}:
                masked[offset] = 32
    return bytes(masked)


def _line_start(raw: bytes, offset: int) -> int:
    return raw.rfind(b"\n", 0, offset) + 1


def _last_fragment_end(masked: bytes, anchor_end: int) -> int:
    footer = FOOTER_RE.search(masked, anchor_end)
    body_end = BODY_END_RE.search(masked, anchor_end)
    candidates = [match.start() for match in (footer, body_end) if match is not None]
    return min(candidates) if candidates else len(masked)


def _canonical_rule_id(value: bytes | str) -> str:
    text = value.decode("ascii") if isinstance(value, bytes) else value
    text = text.upper()
    if "(" in text:
        prefix, suffix = text.split("(", 1)
        text = prefix + "(" + suffix.lower()
    return text


def _decode_source_text(value: bytes) -> str:
    decoded = value.decode("utf-8", errors="replace")
    decoded = html.unescape(decoded).translate(CP1252_CONTROL_MAP)
    return decoded.replace("\u00a0", " ")


def _is_tag_start(raw: bytes, offset: int) -> bool:
    if offset + 1 >= len(raw) or raw[offset] != 60:
        return False
    following = raw[offset + 1]
    return following in {33, 47, 63} or 65 <= following <= 90 or 97 <= following <= 122


def _tag_end(raw: bytes, start: int) -> int:
    quote: int | None = None
    offset = start + 1
    while offset < len(raw):
        byte = raw[offset]
        if quote is None and byte in {34, 39}:
            quote = byte
        elif quote == byte:
            quote = None
        elif quote is None and byte == 62:
            return offset + 1
        offset += 1
    return len(raw)


def _lex_html(raw: bytes) -> Iterator[Lexeme]:
    offset = 0
    while offset < len(raw):
        if raw.startswith(b"<!--", offset):
            closing = raw.find(b"-->", offset + 4)
            end = len(raw) if closing < 0 else closing + 3
            yield Lexeme("comment", offset, end)
            offset = end
            continue
        if not _is_tag_start(raw, offset):
            end = offset + 1
            while end < len(raw) and not _is_tag_start(raw, end):
                end += 1
            yield Lexeme("text", offset, end)
            offset = end
            continue

        end = _tag_end(raw, offset)
        token = raw[offset:end]
        name_match = TAG_NAME_RE.match(token)
        if name_match is None or token.startswith((b"<!", b"<?")):
            yield Lexeme("markup", offset, end)
        else:
            tag = name_match.group("tag").decode("ascii").lower()
            closing = name_match.group("closing") is not None
            self_closing = token.rstrip().endswith(b"/>") or tag in VOID_TAGS
            yield Lexeme("tag", offset, end, tag, closing, self_closing)
        offset = end


def _attribute_value(raw: bytes, lexeme: Lexeme, name: str) -> AttributeValue | None:
    token = raw[lexeme.start : lexeme.end]
    tag_match = TAG_NAME_RE.match(token)
    if tag_match is None:
        return None
    for match in ATTRIBUTE_RE.finditer(token, tag_match.end(), len(token) - 1):
        if match.group("name").decode("ascii", errors="ignore").lower() != name:
            continue
        group_name = next(
            (
                candidate
                for candidate in ("double", "single", "bare")
                if match.group(candidate) is not None
            ),
            None,
        )
        if group_name is None:
            return None
        relative_start, relative_end = match.span(group_name)
        start = lexeme.start + relative_start
        end = lexeme.start + relative_end
        return AttributeValue(_decode_source_text(raw[start:end]), start, end)
    return None


def _source_url(raw: bytes, filename: str) -> str:
    fallback = f"https://iupac.qmul.ac.uk/BlueBook/{filename}"
    for lexeme in _lex_html(raw):
        if lexeme.kind == "tag" and not lexeme.closing and lexeme.tag == "base":
            href = _attribute_value(raw, lexeme, "href")
            return urllib.parse.urljoin(fallback, href.value) if href else fallback
        if lexeme.kind == "tag" and lexeme.closing and lexeme.tag == "head":
            break
    return fallback


def _extract_fragments(
    raw: bytes, document_id: str, cache_path: str
) -> list[RuleFragment]:
    masked = _mask_comments(raw)
    matches = list(ACTIVE_RULE_ANCHOR_RE.finditer(masked))
    if not matches:
        raise ValueError(f"No active rule anchors found in {cache_path}")
    starts = [_line_start(raw, match.start()) for match in matches]
    if len(starts) != len(set(starts)):
        raise ValueError(f"Two active rule anchors share a source line in {cache_path}")

    fragments: list[RuleFragment] = []
    seen_rule_ids: set[str] = set()
    for ordinal, match in enumerate(matches, start=1):
        rule_id = _canonical_rule_id(match.group(2))
        if rule_id in seen_rule_ids:
            raise ValueError(f"Duplicate active rule id {rule_id} in {cache_path}")
        seen_rule_ids.add(rule_id)
        start = starts[ordinal - 1]
        end = (
            starts[ordinal]
            if ordinal < len(starts)
            else _last_fragment_end(masked, match.end())
        )
        if not 0 <= start < end <= len(raw):
            raise ValueError(f"Invalid active fragment bounds for {rule_id} in {cache_path}")
        raw_fragment = raw[start:end]
        fragments.append(
            RuleFragment(
                document_id=document_id,
                cache_path=cache_path,
                rule_id=rule_id,
                anchor=match.group(1).decode("ascii", errors="replace"),
                ordinal=ordinal,
                start=start,
                end=end,
                heading_start=match.start(2),
                heading_end=match.end(2),
                raw_sha256=sha256_bytes(raw_fragment),
                active_sha256=sha256_bytes(COMMENT_RE.sub(b"", raw_fragment)),
            )
        )
    return fragments


def _load_index(
    cache_dir: Path, chapter_files: Sequence[tuple[str, str]]
) -> ExtractionIndex:
    if not chapter_files:
        raise ValueError("At least one chapter source is required")
    document_ids = [document_id for document_id, _ in chapter_files]
    filenames = [filename for _, filename in chapter_files]
    if len(document_ids) != len(set(document_ids)):
        raise ValueError("Chapter document ids must be unique")
    if len({filename.casefold() for filename in filenames}) != len(filenames):
        raise ValueError("Chapter filenames must be unique")

    artifacts: list[SourceArtifact] = []
    for document_id, filename in chapter_files:
        path = cache_dir / filename
        raw = path.read_bytes()
        artifacts.append(
            SourceArtifact(
                artifact_id=f"html:{document_id}",
                document_id=document_id,
                cache_path=filename,
                path=path,
                source_url=_source_url(raw, filename),
                raw=raw,
                source_sha256=sha256_bytes(raw),
                fragments=_extract_fragments(raw, document_id, filename),
            )
        )

    artifacts_by_filename = {
        artifact.cache_path.casefold(): artifact for artifact in artifacts
    }
    fragments_by_rule_id: dict[str, RuleFragment] = {}
    anchor_candidates: dict[tuple[str, str], list[RuleFragment]] = {}
    for artifact in artifacts:
        for fragment in artifact.fragments:
            previous = fragments_by_rule_id.get(fragment.rule_id)
            if previous is not None:
                raise ValueError(
                    f"Duplicate active rule id {fragment.rule_id} in "
                    f"{previous.cache_path} and {fragment.cache_path}"
                )
            fragments_by_rule_id[fragment.rule_id] = fragment
            anchor_key = (artifact.cache_path.casefold(), fragment.anchor.casefold())
            anchor_candidates.setdefault(anchor_key, []).append(fragment)
    fragments_by_anchor = {
        key: tuple(candidates) for key, candidates in anchor_candidates.items()
    }
    return ExtractionIndex(
        artifacts=artifacts,
        artifacts_by_filename=artifacts_by_filename,
        fragments_by_rule_id=fragments_by_rule_id,
        fragments_by_anchor=fragments_by_anchor,
    )


def _url_filename(value: str) -> str:
    path = urllib.parse.unquote(urllib.parse.urlsplit(value).path)
    return path.rsplit("/", 1)[-1].casefold()


def _resolve_href(
    href: str, source_artifact: SourceArtifact, index: ExtractionIndex
) -> HrefResolution:
    resolved_url = urllib.parse.urljoin(source_artifact.source_url, href)
    parsed = urllib.parse.urlsplit(resolved_url)
    filename = _url_filename(resolved_url) or source_artifact.cache_path.casefold()
    target_document = index.artifacts_by_filename.get(filename)
    anchor = urllib.parse.unquote(parsed.fragment) or None
    target_fragments: tuple[RuleFragment, ...] = ()
    if target_document is not None and anchor is not None:
        target_fragments = index.fragments_by_anchor.get(
            (target_document.cache_path.casefold(), anchor.casefold()), ()
        )
        if not target_fragments and re.fullmatch(
            RULE_ID_BYTES.decode("ascii"), anchor, re.IGNORECASE
        ):
            candidate = index.fragments_by_rule_id.get(_canonical_rule_id(anchor))
            if candidate is not None and candidate.cache_path == target_document.cache_path:
                target_fragments = (candidate,)
    return HrefResolution(resolved_url, target_document, anchor, target_fragments)


def _select_href_fragment(
    resolution: HrefResolution, cited_rule_id: str | None
) -> RuleFragment | None:
    if cited_rule_id is not None:
        matching = [
            fragment
            for fragment in resolution.fragment_candidates
            if fragment.rule_id == cited_rule_id
        ]
        if len(matching) == 1:
            return matching[0]
    if len(resolution.fragment_candidates) == 1:
        return resolution.fragment_candidates[0]
    return None


def _artifact_for_fragment(
    fragment: RuleFragment, index: ExtractionIndex
) -> SourceArtifact:
    return index.artifacts_by_filename[fragment.cache_path.casefold()]


def _document_binding(artifact: SourceArtifact | None) -> dict[str, Any] | None:
    if artifact is None:
        return None
    return {
        "artifact_id": artifact.artifact_id,
        "document_id": artifact.document_id,
        "cache_path": artifact.cache_path,
        "source_artifact_sha256": artifact.source_sha256,
    }


def _target_fragment_binding(fragment: RuleFragment | None) -> dict[str, Any] | None:
    if fragment is None:
        return None
    return {
        "anchor": fragment.anchor,
        "ordinal": fragment.ordinal,
        "document_start_byte": fragment.start,
        "document_end_byte": fragment.end,
        "raw_sha256": fragment.raw_sha256,
        "active_sha256": fragment.active_sha256,
    }


def _infer_chapter_document(
    rule_id: str, index: ExtractionIndex
) -> SourceArtifact | None:
    if not re.fullmatch(r"P-\d+", rule_id):
        return None
    exact = [artifact for artifact in index.artifacts if artifact.document_id == rule_id]
    return exact[0] if len(exact) == 1 else None


def _target_payload(
    cited_rule_id: str | None,
    link_resolution: HrefResolution | None,
    index: ExtractionIndex,
) -> dict[str, Any]:
    href_fragment = (
        _select_href_fragment(link_resolution, cited_rule_id)
        if link_resolution is not None
        else None
    )
    if href_fragment is not None:
        fragment = href_fragment
        artifact = _artifact_for_fragment(fragment, index)
        return {
            "rule_id": fragment.rule_id,
            "resolution": "active_rule",
            "resolution_basis": "href_anchor",
            "document": _document_binding(artifact),
            "active_fragment": _target_fragment_binding(fragment),
        }

    if (
        link_resolution is not None
        and link_resolution.document is not None
        and link_resolution.anchor is None
        and cited_rule_id is not None
    ):
        return {
            "rule_id": cited_rule_id,
            "resolution": "document",
            "resolution_basis": "href_document",
            "document": _document_binding(link_resolution.document),
            "active_fragment": None,
        }

    if cited_rule_id is not None:
        fragment = index.fragments_by_rule_id.get(cited_rule_id)
        if fragment is not None:
            artifact = _artifact_for_fragment(fragment, index)
            return {
                "rule_id": fragment.rule_id,
                "resolution": "active_rule",
                "resolution_basis": "reference_text",
                "document": _document_binding(artifact),
                "active_fragment": _target_fragment_binding(fragment),
            }
        inferred_document = _infer_chapter_document(cited_rule_id, index)
        if inferred_document is not None:
            return {
                "rule_id": cited_rule_id,
                "resolution": "document",
                "resolution_basis": "inferred_document",
                "document": _document_binding(inferred_document),
                "active_fragment": None,
            }

    if cited_rule_id is None:
        raise ValueError("An unresolved href-only occurrence has no P-rule target")
    partial_document = link_resolution.document if link_resolution is not None else None
    return {
        "rule_id": cited_rule_id,
        "resolution": "unresolved",
        "resolution_basis": "href" if link_resolution is not None else "reference_text",
        "document": _document_binding(partial_document),
        "active_fragment": None,
    }


def _utf8_character_width(raw: bytes, offset: int, end: int) -> int:
    first = raw[offset]
    if first < 0x80:
        return 1
    if 0xC2 <= first <= 0xDF:
        width = 2
    elif 0xE0 <= first <= 0xEF:
        width = 3
    elif 0xF0 <= first <= 0xF4:
        width = 4
    else:
        return 1
    if offset + width > end or any(
        not 0x80 <= byte <= 0xBF for byte in raw[offset + 1 : offset + width]
    ):
        return 1
    return width


def _decode_text_lexeme(raw: bytes, start: int, end: int) -> Iterator[VisibleChar]:
    offset = start
    while offset < end:
        entity = ENTITY_RE.match(raw, offset, end)
        if entity is not None:
            decoded = _decode_source_text(entity.group(0))
            for character in decoded:
                yield VisibleChar(character, entity.start(), entity.end())
            offset = entity.end()
            continue
        width = _utf8_character_width(raw, offset, end)
        decoded = raw[offset : offset + width].decode("utf-8", errors="replace")
        for character in decoded.translate(CP1252_CONTROL_MAP):
            yield VisibleChar(character, offset, offset + width)
        offset += width


def _visible_characters(raw: bytes, lexemes: Sequence[Lexeme]) -> list[VisibleChar]:
    unnormalized: list[VisibleChar] = []
    suppressed: list[str] = []
    for lexeme in lexemes:
        if lexeme.kind == "tag" and lexeme.tag in {"script", "style"}:
            if lexeme.closing:
                if suppressed and suppressed[-1] == lexeme.tag:
                    suppressed.pop()
            elif not lexeme.self_closing:
                suppressed.append(lexeme.tag)
            continue
        if suppressed or lexeme.kind == "comment":
            continue
        if lexeme.kind == "text":
            unnormalized.extend(_decode_text_lexeme(raw, lexeme.start, lexeme.end))
        elif lexeme.kind == "tag" and lexeme.tag in VISIBLE_BREAK_TAGS:
            unnormalized.append(VisibleChar(" ", None, None))

    normalized: list[VisibleChar] = []
    for item in unnormalized:
        value = item.value.replace("\u00a0", " ")
        for character in value:
            if character.isspace():
                if normalized and normalized[-1].value != " ":
                    normalized.append(VisibleChar(" ", item.raw_start, item.raw_end))
            else:
                normalized.append(VisibleChar(character, item.raw_start, item.raw_end))
    while normalized and normalized[-1].value == " ":
        normalized.pop()
    return normalized


def _visible_text_for_span(
    characters: Sequence[VisibleChar], start: int, end: int
) -> str:
    return "".join(
        item.value
        for item in characters
        if item.raw_start is not None
        and item.raw_end is not None
        and start <= item.raw_start
        and item.raw_end <= end
    ).strip()


def _context_payload(
    characters: Sequence[VisibleChar],
    focus_start: int,
    focus_end: int,
    context_characters: int,
) -> dict[str, Any]:
    text = "".join(item.value for item in characters)
    overlapping = [
        index
        for index, item in enumerate(characters)
        if item.raw_start is not None
        and item.raw_end is not None
        and item.raw_start < focus_end
        and focus_start < item.raw_end
    ]
    if overlapping:
        reference_start = min(overlapping)
        reference_end = max(overlapping) + 1
        window_start = max(0, reference_start - context_characters)
        window_end = min(len(text), reference_end + context_characters)
    else:
        insertion = next(
            (
                index
                for index, item in enumerate(characters)
                if item.raw_start is not None and item.raw_start >= focus_start
            ),
            len(text),
        )
        reference_start = reference_end = None
        window_start = max(0, insertion - context_characters)
        window_end = min(len(text), insertion + context_characters)

    while window_start < window_end and text[window_start] == " ":
        window_start += 1
    while window_end > window_start and text[window_end - 1] == " ":
        window_end -= 1
    snippet = text[window_start:window_end]
    local_start = None if reference_start is None else reference_start - window_start
    local_end = None if reference_end is None else reference_end - window_start
    return {
        "character_unit": "unicode_code_point",
        "radius": context_characters,
        "text": snippet,
        "reference_start": local_start,
        "reference_end": local_end,
        "text_sha256": sha256_bytes(snippet.encode("utf-8")),
    }


def _exact_span(
    raw_fragment: bytes,
    fragment: RuleFragment,
    start: int,
    end: int,
) -> dict[str, Any]:
    if not 0 <= start < end <= len(raw_fragment):
        raise ValueError(f"Invalid occurrence span in {fragment.rule_id}: {start}:{end}")
    return {
        "offset_unit": "byte",
        "fragment_start_byte": start,
        "fragment_end_byte": end,
        "document_start_byte": fragment.start + start,
        "document_end_byte": fragment.start + end,
        "raw_sha256": sha256_bytes(raw_fragment[start:end]),
    }


def _source_binding(
    artifact: SourceArtifact,
    fragment: RuleFragment,
    raw_fragment: bytes,
    start: int,
    end: int,
) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "source_artifact_sha256": artifact.source_sha256,
        "fragment": {
            "document_start_byte": fragment.start,
            "document_end_byte": fragment.end,
            "raw_sha256": fragment.raw_sha256,
            "active_sha256": fragment.active_sha256,
        },
        "span": _exact_span(raw_fragment, fragment, start, end),
    }


def _href_payload(
    link: Link,
    cited_rule_id: str | None,
    raw_fragment: bytes,
    fragment: RuleFragment,
) -> dict[str, Any]:
    href_fragment = _select_href_fragment(link.resolution, cited_rule_id)
    target_rule_id = href_fragment.rule_id if href_fragment is not None else None
    cited_target_match = (
        cited_rule_id == target_rule_id
        if cited_rule_id is not None and target_rule_id is not None
        else None
    )
    return {
        "value": link.href.value,
        "resolved_url": link.resolution.resolved_url,
        "target_document_id": (
            link.resolution.document.document_id
            if link.resolution.document is not None
            else None
        ),
        "target_anchor": link.resolution.anchor,
        "target_rule_id": target_rule_id,
        "cited_target_match": cited_target_match,
        "source": _exact_span(
            raw_fragment, fragment, link.href.start, link.href.end
        ),
    }


def _occurrence_payload(
    *,
    artifact: SourceArtifact,
    fragment: RuleFragment,
    raw_fragment: bytes,
    characters: Sequence[VisibleChar],
    reference_kind: str,
    cited_rule_id: str | None,
    reference_text: str,
    span_start: int,
    span_end: int,
    focus_start: int,
    focus_end: int,
    link: Link | None,
    index: ExtractionIndex,
    context_characters: int,
) -> dict[str, Any]:
    target = _target_payload(
        cited_rule_id, link.resolution if link is not None else None, index
    )
    if link is not None:
        link.represented_targets.add(target["rule_id"])
    return {
        "occurrence_id": "",
        "source_document_id": artifact.document_id,
        "source_rule_id": fragment.rule_id,
        "reference_kind": reference_kind,
        "reference_text": reference_text,
        "cited_rule_id": cited_rule_id,
        "href": (
            _href_payload(link, cited_rule_id, raw_fragment, fragment)
            if link is not None
            else None
        ),
        "target": target,
        "context": _context_payload(
            characters, focus_start, focus_end, context_characters
        ),
        "source": _source_binding(
            artifact, fragment, raw_fragment, span_start, span_end
        ),
    }


def _finish_link(link: Link | None, end: int, links: list[Link]) -> None:
    if link is not None:
        link.end = max(link.opening_end, end)
        links.append(link)


def _extract_fragment_occurrences(
    artifact: SourceArtifact,
    fragment: RuleFragment,
    index: ExtractionIndex,
    context_characters: int,
) -> list[dict[str, Any]]:
    raw_fragment = artifact.raw[fragment.start : fragment.end]
    lexemes = list(_lex_html(raw_fragment))
    characters = _visible_characters(raw_fragment, lexemes)
    occurrences: list[dict[str, Any]] = []
    links: list[Link] = []
    current_link: Link | None = None
    link_ordinal = 0

    for lexeme in lexemes:
        if lexeme.kind == "tag":
            if lexeme.tag == "a":
                if lexeme.closing:
                    _finish_link(current_link, lexeme.end, links)
                    current_link = None
                else:
                    _finish_link(current_link, lexeme.start, links)
                    current_link = None
                    href = _attribute_value(raw_fragment, lexeme, "href")
                    if href is not None and href.value:
                        link_ordinal += 1
                        current_link = Link(
                            ordinal=link_ordinal,
                            start=lexeme.start,
                            opening_end=lexeme.end,
                            href=href,
                            resolution=_resolve_href(href.value, artifact, index),
                        )
            elif (
                current_link is not None
                and not lexeme.closing
                and lexeme.tag in IMPLICIT_LINK_CLOSE_TAGS
            ):
                _finish_link(current_link, lexeme.start, links)
                current_link = None
            continue
        if lexeme.kind != "text":
            continue

        for match in RULE_REFERENCE_RE.finditer(
            raw_fragment, lexeme.start, lexeme.end
        ):
            document_start = fragment.start + match.start(1)
            document_end = fragment.start + match.end(1)
            if (
                document_start == fragment.heading_start
                and document_end == fragment.heading_end
            ):
                continue
            cited_rule_id = _canonical_rule_id(match.group(1))
            occurrences.append(
                _occurrence_payload(
                    artifact=artifact,
                    fragment=fragment,
                    raw_fragment=raw_fragment,
                    characters=characters,
                    reference_kind="href" if current_link is not None else "text",
                    cited_rule_id=cited_rule_id,
                    reference_text=match.group(1).decode("ascii"),
                    span_start=match.start(1),
                    span_end=match.end(1),
                    focus_start=match.start(1),
                    focus_end=match.end(1),
                    link=current_link,
                    index=index,
                    context_characters=context_characters,
                )
            )

    _finish_link(current_link, len(raw_fragment), links)

    for link in links:
        heading_start = fragment.heading_start - fragment.start
        if link.start <= heading_start < (link.end or link.opening_end):
            continue
        target_fragment = _select_href_fragment(link.resolution, None)
        if target_fragment is None or target_fragment.rule_id in link.represented_targets:
            continue
        link_end = link.end if link.end is not None else link.opening_end
        label = _visible_text_for_span(characters, link.opening_end, link_end)
        occurrences.append(
            _occurrence_payload(
                artifact=artifact,
                fragment=fragment,
                raw_fragment=raw_fragment,
                characters=characters,
                reference_kind="href",
                cited_rule_id=None,
                reference_text=label or target_fragment.rule_id,
                span_start=link.href.start,
                span_end=link.href.end,
                focus_start=link.opening_end if label else link.href.start,
                focus_end=link_end if label else link.href.end,
                link=link,
                index=index,
                context_characters=context_characters,
            )
        )

    unique: dict[tuple[int, int, str], dict[str, Any]] = {}
    for occurrence in occurrences:
        span = occurrence["source"]["span"]
        key = (
            span["document_start_byte"],
            span["document_end_byte"],
            occurrence["target"]["rule_id"],
        )
        previous = unique.get(key)
        if previous is None:
            unique[key] = occurrence
        elif canonical_json_bytes(previous) != canonical_json_bytes(occurrence):
            raise ValueError(
                f"Conflicting duplicate reference occurrence in {fragment.rule_id} at "
                f"{span['document_start_byte']}:{span['document_end_byte']}"
            )
    return sorted(
        unique.values(),
        key=lambda item: (
            item["source"]["span"]["document_start_byte"],
            item["source"]["span"]["document_end_byte"],
            item["target"]["rule_id"],
            item["reference_kind"],
        ),
    )


def _artifact_payload(artifact: SourceArtifact) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "document_id": artifact.document_id,
        "cache_path": artifact.cache_path,
        "source_url": artifact.source_url,
        "source_encoding": "utf-8",
        "source_byte_count": len(artifact.raw),
        "source_sha256": artifact.source_sha256,
        "active_rule_fragment_count": len(artifact.fragments),
    }


def extract_reference_occurrences(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    document_ids: set[str] | None = None,
    *,
    chapter_files: Sequence[tuple[str, str]] = CHAPTER_FILES,
    context_characters: int = DEFAULT_CONTEXT_CHARACTERS,
) -> dict[str, Any]:
    """Extract byte-addressed P-rule reference occurrences from active HTML fragments."""
    if context_characters < 0:
        raise ValueError("context_characters must be nonnegative")
    index = _load_index(cache_dir, chapter_files)
    known_document_ids = {artifact.document_id for artifact in index.artifacts}
    if document_ids is None:
        selected_ids = known_document_ids
    else:
        unknown = document_ids - known_document_ids
        if unknown:
            raise ValueError(f"Unknown document ids: {', '.join(sorted(unknown))}")
        selected_ids = set(document_ids)

    source_artifacts = [_artifact_payload(artifact) for artifact in index.artifacts]
    selected_artifacts = [
        artifact for artifact in index.artifacts if artifact.document_id in selected_ids
    ]
    occurrences: list[dict[str, Any]] = []
    for artifact in selected_artifacts:
        for fragment in artifact.fragments:
            occurrences.extend(
                _extract_fragment_occurrences(
                    artifact, fragment, index, context_characters
                )
            )

    per_rule_ordinals: Counter[str] = Counter()
    for occurrence in occurrences:
        source_rule_id = occurrence["source_rule_id"]
        per_rule_ordinals[source_rule_id] += 1
        occurrence["occurrence_id"] = (
            f"{source_rule_id}:xref:{per_rule_ordinals[source_rule_id]:04d}"
        )

    kind_counts = Counter(item["reference_kind"] for item in occurrences)
    resolution_counts = Counter(item["target"]["resolution"] for item in occurrences)
    counters = {
        "source_artifact_count": len(index.artifacts),
        "source_document_count": len(selected_artifacts),
        "indexed_active_rule_fragment_count": sum(
            len(artifact.fragments) for artifact in index.artifacts
        ),
        "source_active_rule_fragment_count": sum(
            len(artifact.fragments) for artifact in selected_artifacts
        ),
        "reference_occurrence_count": len(occurrences),
        "reference_kind_counts": {
            "href": kind_counts["href"],
            "text": kind_counts["text"],
        },
        "target_resolution_counts": {
            "active_rule": resolution_counts["active_rule"],
            "document": resolution_counts["document"],
            "unresolved": resolution_counts["unresolved"],
        },
        "distinct_source_rule_count": len(
            {item["source_rule_id"] for item in occurrences}
        ),
        "distinct_target_rule_count": len(
            {item["target"]["rule_id"] for item in occurrences}
        ),
    }
    source_document_ids = [
        artifact.document_id for artifact in selected_artifacts
    ]
    artifact_manifest_sha256 = sha256_bytes(canonical_json_bytes(source_artifacts))
    digest_payload = {
        "context_characters": context_characters,
        "source_document_ids": source_document_ids,
        "source_artifact_manifest_sha256": artifact_manifest_sha256,
        "source_artifacts": source_artifacts,
        "counters": counters,
        "occurrences": occurrences,
    }
    return {
        "format": "iupac-bluebook-reference-occurrences",
        "version": "1.0.0",
        "source_scope": "P-rule references in active normative rule fragments of cached official chapter HTML",
        "context_characters": context_characters,
        "source_document_ids": source_document_ids,
        "source_artifact_manifest_sha256": artifact_manifest_sha256,
        "source_artifacts": source_artifacts,
        "corpus_sha256": sha256_bytes(canonical_json_bytes(digest_payload)),
        "counters": counters,
        "occurrences": occurrences,
    }


def extract_corpus(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    document_ids: set[str] | None = None,
    *,
    chapter_files: Sequence[tuple[str, str]] = CHAPTER_FILES,
    context_characters: int = DEFAULT_CONTEXT_CHARACTERS,
) -> dict[str, Any]:
    return extract_reference_occurrences(
        cache_dir,
        document_ids,
        chapter_files=chapter_files,
        context_characters=context_characters,
    )


def validate_corpus(
    corpus: dict[str, Any], schema_path: Path = DEFAULT_SCHEMA
) -> None:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as error:
        raise RuntimeError(
            "jsonschema is required for validation; install the conversion extra"
        ) from error
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(corpus), key=lambda item: list(item.absolute_path)
    )
    if errors:
        details = "\n".join(
            f"- /{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in errors[:25]
        )
        raise ValueError(f"Reference occurrence corpus failed schema validation:\n{details}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract occurrence-level P-rule cross-references with exact raw byte provenance."
        )
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--documents",
        nargs="+",
        choices=[document_id for document_id, _ in CHAPTER_FILES],
        help="Optional source documents; all documents remain indexed for target resolution.",
    )
    parser.add_argument(
        "--context-characters",
        type=int,
        default=DEFAULT_CONTEXT_CHARACTERS,
    )
    parser.add_argument(
        "--no-validate", action="store_true", help="Skip JSON Schema validation."
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    corpus = extract_reference_occurrences(
        args.cache_dir,
        set(args.documents) if args.documents else None,
        context_characters=args.context_characters,
    )
    if not args.no_validate:
        validate_corpus(corpus, args.schema)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(canonical_json_bytes(corpus))
    print(
        f"Wrote {corpus['counters']['reference_occurrence_count']} reference "
        f"occurrences to {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
