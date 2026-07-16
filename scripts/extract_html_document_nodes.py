from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.parse
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from html.entities import name2codepoint
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from lxml import etree
from lxml import html as lxml_html


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = ROOT / ".cache" / "bluebook_html"
DEFAULT_OUTPUT = ROOT / "data" / "bluebook_v3" / "bluebook_v3_document_nodes.json"
DEFAULT_SCHEMA = ROOT / "data" / "bluebook_document_nodes.schema.json"

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
COMMENT_RE = re.compile(rb"<!--.*?-->", re.DOTALL)
FOOTER_RE = re.compile(rb"(?im)^[ \t]*<hr\b")
BODY_END_RE = re.compile(rb"(?i)</body\s*>")
LIST_MARKER_RE = re.compile(
    r"^\s*(?P<marker>\((?:\d+|[a-z]|[ivxlcdm]+)\)|"
    r"(?:\d+|[a-z]|[ivxlcdm]+)[.)])\s+",
    re.IGNORECASE,
)
UNORDERED_MARKER_RE = re.compile(r"^\s*(?P<marker>[*\u2022\u2013\u2014-])\s+")
EXAMPLE_LABEL_RE = re.compile(
    r"^\s*(?P<label>Examples?(?:\s+\d+)?(?:\s*\([^\n]*\))?"
    r"(?:[:.]|(?=\s*$)))(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
NOTE_LABEL_RE = re.compile(
    r"^\s*(?P<label>Notes?(?:\s+\d+)?(?:[:.]|(?=\s*$)))(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
NOTE_WITHIN_TEXT_RE = re.compile(
    r"(?<!\w)(?P<label>Notes?(?:\s+\d+)?[:.])\s*",
    re.IGNORECASE,
)
VISIBLE_TABLE_CAPTION_RE = re.compile(
    r"^\s*(?P<label>Table\s+\d+(?:\.\d+)*)(?P<punctuation>[.:])?(?=\s|$)",
    re.IGNORECASE,
)
VISIBLE_FIGURE_CAPTION_RE = re.compile(
    r"^\s*(?P<label>(?:Figure|Fig\.)\s+\d+(?:\.\d+)*)(?P<punctuation>\.)?(?=\s|$)",
    re.IGNORECASE,
)
FOOTNOTE_RE = re.compile(
    r"^\s*(?P<marker>\*{1,3}|[\u2020\u2021]+)\s*(?P<text>.+)$",
    re.DOTALL,
)

BLOCKQUOTE_TAGS = {"blockquote", "blocquote", "bockquote", "block"}
BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "main",
    "nav",
    "pre",
    "section",
}
NODE_KINDS = (
    "heading",
    "paragraph",
    "prose",
    "list_item",
    "table",
    "figure",
    "example_block",
    "note",
    "caption",
    "source_event",
    "footnote",
    "orphan_cell",
)

EXPECTED_FULL_CORPUS_METRICS = {
    "active_rule_fragment_count": 2554,
    "physical_table_occurrence_count": 567,
    "physical_row_occurrence_count": 3782,
    "physical_cell_occurrence_count": 9100,
    "physical_image_occurrence_count": 5371,
    "footnote_block_count": 7,
    "visible_table_caption_count": 40,
    "visible_figure_caption_count": 8,
    "correction_event_count": 190,
}

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
TAG_NAME_RE = re.compile(rb"</?\s*([A-Za-z][A-Za-z0-9:-]*)")
ENTITY_RE = re.compile(
    rb"&(?:#[0-9]{1,8}|#[xX][0-9A-Fa-f]{1,8}|[A-Za-z][A-Za-z0-9]{1,31});?"
)

CP1252_CONTROL_MAP = {
    code: bytes([code]).decode("cp1252")
    for code in range(0x80, 0xA0)
    if code not in {0x81, 0x8D, 0x8F, 0x90, 0x9D}
}


@dataclass(frozen=True)
class ParseContext:
    paragraph: bool = False
    quote_depth: int = 0
    list_depth: int = 0
    list_kind: str | None = None
    list_marker: str | None = None
    container_path: str = "/fragment"
    centered: bool = False
    caption_hint: str | None = None


@dataclass
class SourceElementRange:
    tag: str
    start: int
    start_tag_end: int
    end: int
    occurrence_id: str
    tag_ordinal: int
    parent: SourceElementRange | None = None
    children: list[SourceElementRange] = field(default_factory=list)


@dataclass(frozen=True)
class MappedChar:
    value: str
    fragment_start: int | None
    fragment_end: int | None
    transform: str = "decoded"


@dataclass
class FieldMap:
    value: str
    characters: tuple[MappedChar, ...]
    ownership: str = "primary"
    owner: FieldMap | None = None
    derivation: str | None = None


class SourceLocator:
    """Map recovered lxml nodes back to exact, comment-aware source byte ranges."""

    def __init__(self, raw_fragment: bytes, document_start: int, rule_id: str):
        self.raw = raw_fragment
        self.document_start = document_start
        self.rule_id = rule_id
        self.comment_ranges = [
            (match.start(), match.end()) for match in COMMENT_RE.finditer(raw_fragment)
        ]
        self.lexemes = list(self._lex(raw_fragment))
        self.source_elements = self._index_elements()
        self.all_source_elements = sorted(
            (item for values in self.source_elements.values() for item in values),
            key=lambda item: (item.start, item.start_tag_end),
        )
        self.visible_text, self.visible_spans = self._index_visible_text()
        self.text_cursor = 0
        self.element_ranges: dict[str, SourceElementRange] = {}
        self.element_paths: dict[int, str] = {}
        self.parsed_elements: dict[str, etree._Element] = {}

    @staticmethod
    def _lex(raw: bytes) -> Iterator[tuple[str, int, int, str | None, bool]]:
        def is_tag_start(position: int) -> bool:
            if position >= len(raw) or raw[position] != 60 or position + 1 >= len(raw):
                return False
            following = raw[position + 1]
            return following in {33, 47, 63} or 65 <= following <= 90 or 97 <= following <= 122

        index = 0
        while index < len(raw):
            if raw.startswith(b"<!--", index):
                end = raw.find(b"-->", index + 4)
                end = len(raw) if end < 0 else end + 3
                yield "comment", index, end, None, False
                index = end
                continue
            if not is_tag_start(index):
                end = index + 1
                while end < len(raw) and not is_tag_start(end):
                    end += 1
                yield "text", index, end, None, False
                index = end
                continue
            closing_bracket = raw.find(b">", index + 1)
            end = len(raw) if closing_bracket < 0 else closing_bracket + 1
            token = raw[index:end]
            name_match = TAG_NAME_RE.match(token)
            if name_match is None or token.startswith((b"<!", b"<?")):
                yield "markup", index, end, None, False
            else:
                tag = name_match.group(1).decode("ascii", errors="replace").lower()
                closing = token.lstrip().startswith(b"</")
                self_closing = token.rstrip().endswith(b"/>") or tag in VOID_TAGS
                yield "end_tag" if closing else "start_tag", index, end, tag, self_closing
            index = end

    def _index_elements(self) -> dict[str, list[SourceElementRange]]:
        elements: dict[str, list[SourceElementRange]] = defaultdict(list)
        stack: list[SourceElementRange] = []

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

        for kind, start, end, tag, self_closing in self.lexemes:
            if kind == "start_tag" and tag is not None:
                if tag == "p":
                    close_open({"p"}, start)
                elif tag == "tr":
                    close_open({"td", "th", "tr"}, start)
                elif tag in {"td", "th"}:
                    close_open({"td", "th"}, start)
                elif tag == "li":
                    close_open({"li"}, start)
                tag_ordinal = len(elements[tag]) + 1
                parent = stack[-1] if stack else None
                element = SourceElementRange(
                    tag=tag,
                    start=start,
                    start_tag_end=end,
                    end=end if self_closing else len(self.raw),
                    occurrence_id=f"{self.rule_id}:{tag}:{tag_ordinal:04d}",
                    tag_ordinal=tag_ordinal,
                    parent=parent,
                )
                if parent is not None:
                    parent.children.append(element)
                elements[tag].append(element)
                if not self_closing:
                    stack.append(element)
                continue
            if kind != "end_tag" or tag is None:
                continue
            match_index = next(
                (position for position in range(len(stack) - 1, -1, -1) if stack[position].tag == tag),
                None,
            )
            if match_index is None:
                continue
            for dangling in stack[match_index + 1 :]:
                dangling.end = start
            stack[match_index].end = end
            del stack[match_index:]
        return dict(elements)

    @staticmethod
    def _decode_plain(data: bytes, base: int) -> tuple[str, list[tuple[int, int]]]:
        text: list[str] = []
        spans: list[tuple[int, int]] = []
        index = 0
        while index < len(data):
            if data[index : index + 2] == b"\r\n":
                text.append("\n")
                spans.append((base + index, base + index + 2))
                index += 2
                continue
            if data[index] == 13:
                text.append("\n")
                spans.append((base + index, base + index + 1))
                index += 1
                continue
            lead = data[index]
            width = 1 if lead < 0x80 else 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
            chunk = data[index : index + width]
            try:
                decoded = chunk.decode("utf-8")
            except UnicodeDecodeError:
                width = 1
                decoded = "\ufffd"
            for character in decoded:
                text.append(character)
                spans.append((base + index, base + index + width))
            index += width
        return "".join(text), spans

    @classmethod
    def _decode_text_token(cls, data: bytes, base: int) -> tuple[str, list[tuple[int, int]]]:
        text: list[str] = []
        spans: list[tuple[int, int]] = []
        cursor = 0
        for match in ENTITY_RE.finditer(data):
            plain, plain_spans = cls._decode_plain(data[cursor : match.start()], base + cursor)
            text.append(plain)
            spans.extend(plain_spans)
            entity_source = match.group(0).decode("ascii", errors="replace")
            numeric = re.fullmatch(r"&#(?P<decimal>[0-9]+);?", entity_source)
            hexadecimal = re.fullmatch(r"&#[xX](?P<hex>[0-9A-Fa-f]+);?", entity_source)
            if numeric:
                decoded = chr(int(numeric.group("decimal")))
            elif hexadecimal:
                decoded = chr(int(hexadecimal.group("hex"), 16))
            else:
                entity_name = entity_source[1:].rstrip(";")
                decoded = (
                    chr(name2codepoint[entity_name])
                    if entity_name in name2codepoint
                    else entity_source
                )
            if decoded == entity_source:
                plain, plain_spans = cls._decode_plain(match.group(0), base + match.start())
                text.append(plain)
                spans.extend(plain_spans)
            else:
                text.append(decoded)
                spans.extend([(base + match.start(), base + match.end())] * len(decoded))
            cursor = match.end()
        plain, plain_spans = cls._decode_plain(data[cursor:], base + cursor)
        text.append(plain)
        spans.extend(plain_spans)
        return "".join(text), spans

    def _index_visible_text(self) -> tuple[str, list[tuple[int, int]]]:
        text: list[str] = []
        spans: list[tuple[int, int]] = []
        for kind, start, end, _, _ in self.lexemes:
            if kind != "text":
                continue
            decoded, decoded_spans = self._decode_text_token(self.raw[start:end], start)
            text.append(decoded)
            spans.extend(decoded_spans)
        return "".join(text), spans

    def bind_root(self, root: etree._Element) -> None:
        parsed_by_tag: dict[str, list[etree._Element]] = defaultdict(list)
        for element in root.iterdescendants():
            if isinstance(element.tag, str):
                parsed_by_tag[element.tag.lower()].append(element)
        for tag, parsed_elements in parsed_by_tag.items():
            source_elements = self.source_elements.get(tag, [])
            if len(parsed_elements) != len(source_elements):
                if tag in {"table", "tr", "td", "th", "caption", "img", "br"}:
                    raise ValueError(
                        f"Cannot prove source ranges for <{tag}>: parsed {len(parsed_elements)}, "
                        f"source {len(source_elements)}"
                    )
                continue
            for parsed, source in zip(parsed_elements, source_elements):
                path = _stable_path(parsed, root)
                self.element_ranges[path] = source
                self.element_paths[id(source)] = path
                self.parsed_elements[source.occurrence_id] = parsed

    @staticmethod
    def source_path(source: SourceElementRange) -> str:
        return f"/fragment/source/{source.tag}[{source.tag_ordinal}]"

    def element_range(
        self, element: etree._Element, root: etree._Element
    ) -> SourceElementRange:
        path = _stable_path(element, root)
        source = self.element_ranges.get(path)
        if source is None:
            raise ValueError(f"No exact source element for {path}")
        return source

    def range_parts(
        self,
        source: SourceElementRange,
        part_kind: str = "element",
        dom_path: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._range_parts(
            source.start,
            source.end,
            dom_path or self.element_paths.get(id(source)) or self.source_path(source),
            part_kind,
        )

    @staticmethod
    def nearest_ancestor(
        source: SourceElementRange, tags: set[str]
    ) -> SourceElementRange | None:
        current = source.parent
        while current is not None:
            if current.tag in tags:
                return current
            current = current.parent
        return None

    @staticmethod
    def ancestor_count(source: SourceElementRange, tag: str) -> int:
        count = 0
        current = source.parent
        while current is not None:
            count += current.tag == tag
            current = current.parent
        return count

    def descendants(
        self, source: SourceElementRange, tag: str
    ) -> list[SourceElementRange]:
        return [
            candidate
            for candidate in self.source_elements.get(tag, [])
            if source.start < candidate.start < source.end
        ]

    def physical_counts(self) -> dict[str, int]:
        return {
            "table": len(self.source_elements.get("table", [])),
            "row": len(self.source_elements.get("tr", [])),
            "cell": len(self.source_elements.get("td", []))
            + len(self.source_elements.get("th", [])),
            "image": len(self.source_elements.get("img", [])),
        }

    def _range_parts(
        self, start: int, end: int, dom_path: str, part_kind: str
    ) -> list[dict[str, Any]]:
        ranges = [(start, end)]
        for comment_start, comment_end in self.comment_ranges:
            updated: list[tuple[int, int]] = []
            for left, right in ranges:
                if comment_end <= left or comment_start >= right:
                    updated.append((left, right))
                    continue
                if left < comment_start:
                    updated.append((left, comment_start))
                if comment_end < right:
                    updated.append((comment_end, right))
            ranges = updated
        return [self._part(left, right, dom_path, part_kind) for left, right in ranges if left < right]

    def _part(self, start: int, end: int, dom_path: str, part_kind: str) -> dict[str, Any]:
        return {
            "dom_path": dom_path,
            "part_kind": part_kind,
            "fragment_start_byte": start,
            "fragment_end_byte": end,
            "document_start_byte": self.document_start + start,
            "document_end_byte": self.document_start + end,
            "raw_sha256": sha256_bytes(self.raw[start:end]),
        }

    def element_parts(
        self, element: etree._Element, root: etree._Element
    ) -> list[dict[str, Any]]:
        path = _stable_path(element, root)
        return self.range_parts(self.element_range(element, root), "element", path)

    def text_mapping(
        self, dom_path: str, value: str, part_kind: str
    ) -> tuple[list[dict[str, Any]], tuple[MappedChar, ...]]:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        start = self.visible_text.find(value, self.text_cursor)
        if start < 0:
            excerpt = value[:80].replace("\n", "\\n")
            raise ValueError(f"Cannot align exact source text after character {self.text_cursor}: {excerpt!r}")
        end = start + len(value)
        self.text_cursor = end
        spans = self.visible_spans[start:end]
        if not spans:
            return [], ()
        ranges: list[tuple[int, int]] = []
        for left, right in spans:
            if ranges and left <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], right))
            else:
                ranges.append((left, right))
        parts = [self._part(left, right, dom_path, part_kind) for left, right in ranges]
        characters = tuple(
            MappedChar(character, span[0], span[1])
            for character, span in zip(value, spans)
        )
        return parts, characters

    def text_parts(self, dom_path: str, value: str, part_kind: str) -> list[dict[str, Any]]:
        parts, _ = self.text_mapping(dom_path, value, part_kind)
        return parts

    def element_character(
        self, element: etree._Element, root: etree._Element, value: str, transform: str
    ) -> MappedChar:
        source = self.element_range(element, root)
        return MappedChar(value, source.start, source.end, transform)

    def render_element_field(
        self,
        element: etree._Element,
        root: etree._Element,
        *,
        exclude_descendant_tags: set[str] | None = None,
    ) -> FieldMap:
        source = self.element_range(element, root)
        excluded = [
            self.element_range(descendant, root)
            for descendant in element.iterdescendants()
            if exclude_descendant_tags
            and isinstance(descendant.tag, str)
            and descendant.tag.lower() in exclude_descendant_tags
        ]

        def is_excluded(start: int, end: int) -> bool:
            return any(
                excluded_source.start <= start
                and end <= excluded_source.end
                for excluded_source in excluded
            )

        characters: list[MappedChar] = []
        for kind, start, end, tag, _ in self.lexemes:
            if end <= source.start_tag_end or start >= source.end:
                continue
            if is_excluded(start, end):
                continue
            if kind == "text":
                decoded, spans = self._decode_text_token(self.raw[start:end], start)
                characters.extend(
                    MappedChar(character, left, right)
                    for character, (left, right) in zip(decoded, spans)
                )
            elif kind == "start_tag" and tag == "br":
                characters.append(MappedChar("\n", start, end, "line_break"))
            elif kind == "start_tag" and tag == "p" and characters:
                characters.append(MappedChar("\n", start, end, "line_break"))
        value, normalized = _clean_mapped_characters(characters)
        return FieldMap(value, normalized)

    def attribute_field(
        self,
        element: etree._Element,
        root: etree._Element,
        attribute: str,
    ) -> FieldMap | None:
        source = self.element_range(element, root)
        start_tag = self.raw[source.start : source.start_tag_end]
        pattern = re.compile(
            rb"(?i)(?:^|\s)"
            + re.escape(attribute.encode("ascii"))
            + rb"\s*=\s*(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'|(?P<bare>[^\s>]+))"
        )
        match = pattern.search(start_tag)
        if match is None:
            return None
        group_name = next(
            name for name in ("double", "single", "bare") if match.group(name) is not None
        )
        value_start = source.start + match.start(group_name)
        value_bytes = match.group(group_name)
        decoded, spans = self._decode_text_token(value_bytes, value_start)
        decoded = decoded.translate(CP1252_CONTROL_MAP).replace("\u00a0", " ")
        characters = tuple(
            MappedChar(character, left, right)
            for character, (left, right) in zip(decoded, spans)
        )
        return FieldMap(decoded, characters)

    def attribute_source(
        self, element: etree._Element, root: etree._Element, attribute: str
    ) -> dict[str, Any] | None:
        field_map = self.attribute_field(element, root, attribute)
        if field_map is None or not field_map.characters:
            return None
        return _provenance(
            (
                self._part(
                    character.fragment_start,
                    character.fragment_end,
                    _stable_path(element, root) + f"/@{attribute}",
                    "attribute",
                )
                for character in field_map.characters
                if character.fragment_start is not None
                and character.fragment_end is not None
            ),
            ownership="primary",
            owner_ref=self.element_range(element, root).occurrence_id,
        )

    def skip_element_text(self, element: etree._Element, root: etree._Element) -> None:
        source = self.element_ranges.get(_stable_path(element, root))
        if source is None:
            return
        while (
            self.text_cursor < len(self.visible_spans)
            and self.visible_spans[self.text_cursor][0] < source.end
        ):
            self.text_cursor += 1


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _mask_comments(raw: bytes) -> bytes:
    """Blank comments without changing any source byte offset."""
    masked = bytearray(raw)
    for match in COMMENT_RE.finditer(raw):
        for index in range(match.start(), match.end()):
            if masked[index] not in {10, 13}:
                masked[index] = 32
    return bytes(masked)


def _strip_comments(raw: bytes) -> bytes:
    return COMMENT_RE.sub(b"", raw)


def _line_start(raw: bytes, offset: int) -> int:
    return raw.rfind(b"\n", 0, offset) + 1


def _last_fragment_end(masked: bytes, anchor_end: int) -> int:
    footer = FOOTER_RE.search(masked, anchor_end)
    body_end = BODY_END_RE.search(masked, anchor_end)
    candidates = [match.start() for match in (footer, body_end) if match is not None]
    return min(candidates) if candidates else len(masked)


def _clean_text(value: str) -> str:
    value = value.translate(CP1252_CONTROL_MAP)
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _merged_span(characters: Sequence[MappedChar]) -> tuple[int | None, int | None]:
    starts = [item.fragment_start for item in characters if item.fragment_start is not None]
    ends = [item.fragment_end for item in characters if item.fragment_end is not None]
    return (min(starts), max(ends)) if starts and ends else (None, None)


def _clean_mapped_characters(
    characters: Sequence[MappedChar],
) -> tuple[str, tuple[MappedChar, ...]]:
    translated: list[MappedChar] = []
    for item in characters:
        value = item.value.translate(CP1252_CONTROL_MAP)
        value = value.replace("\r", "\n").replace("\u00a0", " ")
        translated.extend(
            MappedChar(character, item.fragment_start, item.fragment_end, item.transform)
            for character in value
        )

    horizontal: list[MappedChar] = []
    index = 0
    while index < len(translated):
        if translated[index].value not in " \t\f\v":
            horizontal.append(translated[index])
            index += 1
            continue
        end = index + 1
        while end < len(translated) and translated[end].value in " \t\f\v":
            end += 1
        run = translated[index:end]
        start_byte, end_byte = _merged_span(run)
        transform = (
            run[0].transform
            if len(run) == 1 and run[0].value == " "
            else "space_fold"
        )
        horizontal.append(MappedChar(" ", start_byte, end_byte, transform))
        index = end

    around_newlines: list[MappedChar] = []
    index = 0
    while index < len(horizontal):
        item = horizontal[index]
        if item.value != "\n":
            around_newlines.append(item)
            index += 1
            continue
        consumed: list[MappedChar] = []
        while around_newlines and around_newlines[-1].value == " ":
            consumed.insert(0, around_newlines.pop())
        consumed.append(item)
        index += 1
        while index < len(horizontal) and horizontal[index].value == " ":
            consumed.append(horizontal[index])
            index += 1
        start_byte, end_byte = _merged_span(consumed)
        transform = item.transform if len(consumed) == 1 else "newline_fold"
        around_newlines.append(MappedChar("\n", start_byte, end_byte, transform))

    collapsed_newlines: list[MappedChar] = []
    index = 0
    while index < len(around_newlines):
        if around_newlines[index].value != "\n":
            collapsed_newlines.append(around_newlines[index])
            index += 1
            continue
        end = index + 1
        while end < len(around_newlines) and around_newlines[end].value == "\n":
            end += 1
        run = around_newlines[index:end]
        if len(run) <= 2:
            collapsed_newlines.extend(run)
        else:
            collapsed_newlines.append(run[0])
            start_byte, end_byte = _merged_span(run[1:])
            collapsed_newlines.append(
                MappedChar("\n", start_byte, end_byte, "newline_fold")
            )
        index = end

    start = 0
    end = len(collapsed_newlines)
    while start < end and collapsed_newlines[start].value.isspace():
        start += 1
    while end > start and collapsed_newlines[end - 1].value.isspace():
        end -= 1
    normalized = tuple(collapsed_newlines[start:end])
    return "".join(item.value for item in normalized), normalized


def _compact_field(field_map: FieldMap) -> FieldMap:
    compacted: list[MappedChar] = []
    index = 0
    characters = field_map.characters
    while index < len(characters):
        if not characters[index].value.isspace():
            compacted.append(characters[index])
            index += 1
            continue
        end = index + 1
        while end < len(characters) and characters[end].value.isspace():
            end += 1
        start_byte, end_byte = _merged_span(characters[index:end])
        compacted.append(MappedChar(" ", start_byte, end_byte, "space_fold"))
        index = end
    while compacted and compacted[0].value == " ":
        compacted.pop(0)
    while compacted and compacted[-1].value == " ":
        compacted.pop()
    value = "".join(item.value for item in compacted)
    return FieldMap(
        value,
        tuple(compacted),
        ownership="alias",
        owner=field_map.owner if field_map.owner is not None else field_map,
        derivation="whitespace_compaction",
    )


def _slice_field(
    field_map: FieldMap,
    start: int,
    end: int,
    *,
    ownership: str = "alias",
    owner: FieldMap | None = None,
    derivation: str = "substring",
) -> FieldMap:
    return FieldMap(
        field_map.value[start:end],
        field_map.characters[start:end],
        ownership=ownership,
        owner=owner if owner is not None else field_map if ownership == "alias" else None,
        derivation=derivation,
    )


def _synthetic_field(
    value: str, derivation: str, owner: FieldMap | None = None
) -> FieldMap:
    return FieldMap(
        value,
        tuple(MappedChar(character, None, None, "synthetic") for character in value),
        ownership="synthetic",
        owner=owner,
        derivation=derivation,
    )


def _renormalize_field(
    field_map: FieldMap,
    *,
    ownership: str,
    derivation: str,
    owner: FieldMap | None = None,
) -> FieldMap:
    value, characters = _clean_mapped_characters(field_map.characters)
    return FieldMap(
        value,
        characters,
        ownership=ownership,
        owner=owner,
        derivation=derivation,
    )


def _field_from_match(
    field_map: FieldMap,
    match: re.Match[str],
    group: str,
    *,
    ownership: str,
    derivation: str,
    owner: FieldMap | None = None,
) -> FieldMap:
    sliced = _slice_field(
        field_map,
        match.start(group),
        match.end(group),
        ownership=ownership,
        owner=owner,
        derivation=derivation,
    )
    return _renormalize_field(
        sliced,
        ownership=ownership,
        owner=sliced.owner,
        derivation=derivation,
    )


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", _clean_text(value)).strip()


def _stable_path(element: etree._Element, root: etree._Element) -> str:
    if element is root:
        return "/fragment"
    parts: list[str] = []
    current = element
    while current is not root:
        parent = current.getparent()
        if parent is None:
            raise ValueError("DOM node is detached from its fragment root")
        tag = str(current.tag).lower()
        siblings = [child for child in parent if str(child.tag).lower() == tag]
        parts.append(f"{tag}[{siblings.index(current) + 1}]")
        current = parent
    return "/fragment/" + "/".join(reversed(parts))


def _provenance(
    parts: Iterable[dict[str, Any]],
    *,
    ownership: str = "primary",
    owner_ref: str | None = None,
) -> dict[str, Any]:
    ordered: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for part in parts:
        key = (
            part["document_start_byte"],
            part["document_end_byte"],
            part["raw_sha256"],
        )
        if key not in seen:
            seen.add(key)
            ordered.append(part)
    if not ordered:
        raise ValueError("A document node cannot have empty provenance")
    ordered.sort(
        key=lambda part: (
            part["document_start_byte"],
            part["document_end_byte"],
            part["dom_path"],
        )
    )
    return {
        "parts": ordered,
        "manifest_sha256": sha256_bytes(canonical_json_bytes(ordered)),
        "ownership": {
            "kind": ownership,
            "owner_ref": owner_ref,
        },
    }


def _merge_provenance(*values: dict[str, Any] | None) -> dict[str, Any]:
    return _provenance(
        [
            part
            for value in values
            if value is not None
            for part in value.get("parts", [])
        ],
        ownership="aggregate",
    )


def _alias_provenance(value: dict[str, Any], owner_ref: str) -> dict[str, Any]:
    return _provenance(
        value["parts"], ownership="alias", owner_ref=owner_ref
    )


def _field_mapping_payload(
    field_map: FieldMap, locator: SourceLocator
) -> list[dict[str, Any]]:
    if len(field_map.value) != len(field_map.characters):
        raise ValueError(
            f"Field map length mismatch: {field_map.value[:40]!r} has "
            f"{len(field_map.value)} characters and {len(field_map.characters)} mappings"
        )
    payload: list[dict[str, Any]] = []
    index = 0
    while index < len(field_map.characters):
        first = field_map.characters[index]
        end = index + 1
        while end < len(field_map.characters):
            previous = field_map.characters[end - 1]
            candidate = field_map.characters[end]
            if candidate.transform != first.transform:
                break
            if first.transform == "synthetic":
                end += 1
                continue
            if (
                previous.fragment_end is None
                or candidate.fragment_start is None
                or previous.fragment_end != candidate.fragment_start
            ):
                break
            end += 1
        run = field_map.characters[index:end]
        entry: dict[str, Any] = {
            "field_start": index,
            "field_end": end,
            "transform": first.transform,
            "output_sha256": sha256_bytes(
                field_map.value[index:end].encode("utf-8")
            ),
        }
        if first.fragment_start is not None and run[-1].fragment_end is not None:
            fragment_start = first.fragment_start
            fragment_end = run[-1].fragment_end
            entry.update(
                {
                    "fragment_start_byte": fragment_start,
                    "fragment_end_byte": fragment_end,
                    "document_start_byte": locator.document_start + fragment_start,
                    "document_end_byte": locator.document_start + fragment_end,
                    "raw_sha256": sha256_bytes(locator.raw[fragment_start:fragment_end]),
                }
            )
        payload.append(entry)
        index = end
    return payload


def _field_map_id(
    field_map: FieldMap,
    locator: SourceLocator,
    active: set[int] | None = None,
) -> str:
    active = set() if active is None else active
    identity = id(field_map)
    if identity in active:
        raise ValueError("Field-source ownership contains a cycle")
    active.add(identity)
    payload = {
        "value": field_map.value,
        "mapping": _field_mapping_payload(field_map, locator),
        "ownership": field_map.ownership,
        "derivation": field_map.derivation,
        "owner_ref": (
            _field_map_id(field_map.owner, locator, active)
            if field_map.owner is not None
            else None
        ),
    }
    active.remove(identity)
    return "field:" + sha256_bytes(canonical_json_bytes(payload))[:24]


def _serialize_field_map(field_map: FieldMap, locator: SourceLocator) -> dict[str, Any]:
    mapping = _field_mapping_payload(field_map, locator)
    owner_ref = (
        _field_map_id(field_map.owner, locator) if field_map.owner is not None else None
    )
    return {
        "field_source_id": _field_map_id(field_map, locator),
        "value_sha256": sha256_bytes(field_map.value.encode("utf-8")),
        "character_count": len(field_map.value),
        "ownership": {
            "kind": field_map.ownership,
            "owner_ref": owner_ref,
        },
        "derivation": field_map.derivation,
        "mapping": mapping,
    }


def _finalize_field_maps(value: Any, locator: SourceLocator) -> None:
    if isinstance(value, dict):
        field_maps = value.pop("_field_maps", None)
        if field_maps:
            value["field_sources"] = {
                name: _serialize_field_map(field_map, locator)
                for name, field_map in sorted(field_maps.items())
            }
        for child in value.values():
            _finalize_field_maps(child, locator)
    elif isinstance(value, list):
        for child in value:
            _finalize_field_maps(child, locator)


def replay_field_mapping_entry(
    raw_fragment: bytes, mapping: dict[str, Any]
) -> str | None:
    transform = mapping["transform"]
    if transform == "synthetic":
        return None
    start = mapping["fragment_start_byte"]
    end = mapping["fragment_end_byte"]
    source = raw_fragment[start:end]
    if sha256_bytes(source) != mapping["raw_sha256"]:
        raise ValueError("Field mapping raw digest mismatch")
    output_length = mapping["field_end"] - mapping["field_start"]
    if transform == "decoded":
        decoded, _ = SourceLocator._decode_text_token(source, start)
        return decoded.translate(CP1252_CONTROL_MAP).replace("\u00a0", " ")
    if transform == "space_fold":
        return " " * output_length
    if transform in {"newline_fold", "line_break"}:
        return "\n" * output_length
    raise ValueError(f"Unknown field mapping transform: {transform}")


def validate_fragment_field_sources(
    fragment: dict[str, Any], raw_fragment: bytes
) -> dict[str, int]:
    fragment_start = fragment["source"]["start_byte"]
    field_ids: list[str] = []
    owner_refs: list[str] = []
    ownership_counts: Counter[str] = Counter()
    field_count = 0
    mapping_count = 0

    def walk(value: Any) -> None:
        nonlocal field_count, mapping_count
        if isinstance(value, dict):
            field_sources = value.get("field_sources", {})
            for field_name, field_source in field_sources.items():
                field_count += 1
                field_value = value.get(field_name)
                if not isinstance(field_value, str):
                    raise ValueError(
                        f"Field source {field_name!r} does not name a string field"
                    )
                if field_source["character_count"] != len(field_value):
                    raise ValueError("Field-source character count mismatch")
                if field_source["value_sha256"] != sha256_bytes(
                    field_value.encode("utf-8")
                ):
                    raise ValueError("Field-source value digest mismatch")
                ownership = field_source["ownership"]
                ownership_counts[ownership["kind"]] += 1
                owner_ref = ownership.get("owner_ref")
                if owner_ref:
                    owner_refs.append(owner_ref)
                cursor = 0
                for mapping in field_source["mapping"]:
                    mapping_count += 1
                    start = mapping["field_start"]
                    end = mapping["field_end"]
                    if start != cursor or not start < end <= len(field_value):
                        raise ValueError("Field mappings do not exactly partition the value")
                    cursor = end
                    expected = field_value[start:end]
                    if mapping["output_sha256"] != sha256_bytes(
                        expected.encode("utf-8")
                    ):
                        raise ValueError("Field mapping output digest mismatch")
                    replayed = replay_field_mapping_entry(raw_fragment, mapping)
                    if replayed is not None and replayed != expected:
                        raise ValueError(
                            f"Field mapping replay mismatch: {replayed!r} != {expected!r}"
                        )
                    if replayed is not None:
                        fragment_mapping_start = mapping["fragment_start_byte"]
                        fragment_mapping_end = mapping["fragment_end_byte"]
                        if not (
                            0
                            <= fragment_mapping_start
                            < fragment_mapping_end
                            <= len(raw_fragment)
                            and mapping["document_start_byte"]
                            == fragment_start + fragment_mapping_start
                            and mapping["document_end_byte"]
                            == fragment_start + fragment_mapping_end
                        ):
                            raise ValueError("Field mapping byte range is inconsistent")
                if cursor != len(field_value):
                    raise ValueError("Field mappings omit trailing characters")
                identity_payload = {
                    "value": field_value,
                    "mapping": field_source["mapping"],
                    "ownership": ownership["kind"],
                    "derivation": field_source["derivation"],
                    "owner_ref": owner_ref,
                }
                expected_id = "field:" + sha256_bytes(
                    canonical_json_bytes(identity_payload)
                )[:24]
                if field_source["field_source_id"] != expected_id:
                    raise ValueError("Field-source identity digest mismatch")
                field_ids.append(expected_id)
            for key, child in value.items():
                if key != "field_sources":
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(fragment.get("nodes", []))
    available_ids = set(field_ids)
    missing_owners = sorted(
        {owner for owner in owner_refs if owner.startswith("field:")}
        - available_ids
    )
    if missing_owners:
        raise ValueError(
            f"Field-source owners are unresolved: {', '.join(missing_owners[:5])}"
        )
    return {
        "field_source_count": field_count,
        "field_mapping_count": mapping_count,
        **{
            f"{kind}_field_source_count": ownership_counts[kind]
            for kind in ("primary", "alias", "aggregate", "synthetic")
        },
    }


def _source_url(document: etree._Element, filename: str) -> str:
    base = document.xpath("string(//base/@href)").strip()
    return base or f"https://iupac.qmul.ac.uk/BlueBook/{filename}"


def _resolved_url(base_url: str, value: str | None) -> str | None:
    return urllib.parse.urljoin(base_url, value) if value else None


def _image_asset(
    element: etree._Element,
    root: etree._Element,
    base_url: str,
    locator: SourceLocator,
) -> dict[str, Any]:
    link_element = next(
        (
            ancestor
            for ancestor in element.iterancestors("a")
            if ancestor.get("href")
        ),
        None,
    )
    source_range = locator.element_range(element, root)
    source_src_field = locator.attribute_field(element, root, "src") or _synthetic_field(
        element.get("src", ""), "missing_src_attribute_mapping"
    )
    source_src = source_src_field.value
    url = _resolved_url(base_url, source_src) or ""
    field_maps: dict[str, FieldMap] = {
        "source_src": source_src_field,
        "url": _synthetic_field(url, "url_resolution", source_src_field),
    }
    attributes: dict[str, str | None] = {}
    for attribute in ("alt", "title", "width", "height"):
        attribute_field = locator.attribute_field(element, root, attribute)
        attributes[attribute] = attribute_field.value if attribute_field else None
        if attribute_field is not None:
            field_maps[attribute] = attribute_field
    link_field = (
        locator.attribute_field(link_element, root, "href")
        if link_element is not None
        else None
    )
    link_url = (
        _resolved_url(base_url, link_field.value) if link_field is not None else None
    )
    if link_url is not None:
        field_maps["link_href"] = link_field
        field_maps["link_url"] = _synthetic_field(
            link_url, "url_resolution", link_field
        )
    return {
        "occurrence_id": source_range.occurrence_id,
        "source_src": source_src,
        "url": url,
        "alt": attributes["alt"],
        "title": attributes["title"],
        "width": attributes["width"],
        "height": attributes["height"],
        "link_href": link_field.value if link_field is not None else None,
        "link_url": link_url,
        "link_url_source": (
            locator.attribute_source(link_element, root, "href")
            if link_element is not None
            else None
        ),
        "source": _provenance(
            locator.element_parts(element, root),
            ownership="primary",
            owner_ref=source_range.occurrence_id,
        ),
        "_field_maps": field_maps,
    }


def _is_correction_asset(asset: dict[str, Any]) -> bool:
    target = urllib.parse.urlparse(asset.get("link_url") or "").path.lower()
    icon = asset.get("source_src", "").replace("\\", "/").lower()
    return target.endswith(("/changes.html", "/changes2.html")) and icon.endswith(
        "/alter.gif"
    )


def _source_event(asset: dict[str, Any], container_path: str | None = None) -> dict[str, Any]:
    target_field = asset["_field_maps"].get("link_url")
    description_source = asset["_field_maps"].get("title") or asset["_field_maps"].get(
        "alt"
    )
    event = {
        "kind": "source_event",
        "occurrence_id": asset["occurrence_id"],
        "event_kind": "correction",
        "target_url": asset["link_url"],
        "target_url_source": (
            _alias_provenance(
                asset["link_url_source"], f"{asset['occurrence_id']}:link_url"
            )
            if asset["link_url_source"] is not None
            else None
        ),
        "description": asset.get("title") or asset.get("alt"),
        "icon": asset,
        "source": _alias_provenance(asset["source"], asset["occurrence_id"]),
        "_field_maps": {
            "target_url": FieldMap(
                asset["link_url"],
                target_field.characters if target_field else (),
                ownership="alias",
                owner=target_field,
                derivation="correction_href_alias",
            ),
            **(
                {
                    "description": FieldMap(
                        asset.get("title") or asset.get("alt"),
                        description_source.characters,
                        ownership="alias",
                        owner=description_source,
                        derivation="correction_description_alias",
                    )
                }
                if description_source is not None
                else {}
            ),
        },
    }
    if container_path is not None:
        event["_container_path"] = container_path
    return event


def _rendered_text(element: etree._Element) -> str:
    pieces: list[str] = []

    def walk(node: etree._Element) -> None:
        if node.text:
            pieces.append(node.text)
        for child in node:
            tag = str(child.tag).lower()
            if tag == "br":
                pieces.append("\n")
            elif tag != "img":
                walk(child)
            if child.tail:
                pieces.append(child.tail)
            if tag in {"p", "tr"}:
                pieces.append("\n")

    walk(element)
    return _clean_text("".join(pieces))


def _footnote(
    field_map: FieldMap,
    source: dict[str, Any],
    *,
    footnote_id: str | None = None,
) -> dict[str, Any] | None:
    match = FOOTNOTE_RE.match(field_map.value)
    if not match:
        return None
    marker_field = _field_from_match(
        field_map,
        match,
        "marker",
        ownership="primary",
        derivation="footnote_marker",
    )
    text_field = _field_from_match(
        field_map,
        match,
        "text",
        ownership="primary",
        derivation="footnote_body",
    )
    return {
        **({"footnote_id": footnote_id} if footnote_id else {}),
        "marker": marker_field.value,
        "text": text_field.value,
        "source": _provenance(source["parts"], ownership="aggregate"),
        "_field_maps": {"marker": marker_field, "text": text_field},
    }


def _visible_caption_metadata(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    table_match = VISIBLE_TABLE_CAPTION_RE.match(text)
    if table_match:
        return "table", table_match.group("label") + (table_match.group("punctuation") or "")
    figure_match = VISIBLE_FIGURE_CAPTION_RE.match(text)
    if figure_match:
        return "figure", figure_match.group("label") + (
            figure_match.group("punctuation") or ""
        )
    return None, None


def _nearest_ancestor(
    element: etree._Element, tags: set[str]
) -> etree._Element | None:
    return next(
        (
            ancestor
            for ancestor in element.iterancestors()
            if isinstance(ancestor.tag, str) and ancestor.tag.lower() in tags
        ),
        None,
    )


def _positive_int(value: str | None) -> int:
    try:
        parsed = int(value or "1")
    except ValueError:
        return 1
    return max(1, parsed)


def _parse_cell(
    cell_element: etree._Element,
    table_element: etree._Element | None,
    root: etree._Element,
    base_url: str,
    locator: SourceLocator,
    ordinal: int,
) -> dict[str, Any]:
    source_range = locator.element_range(cell_element, root)
    text_field = locator.render_element_field(
        cell_element,
        root,
        exclude_descendant_tags={"table"},
    )
    image_elements = [
        image
        for image in cell_element.xpath(".//img[@src]")
        if _nearest_ancestor(image, {"td", "th"}) is cell_element
        and _nearest_ancestor(image, {"table"}) is table_element
    ]
    assets = [
        _image_asset(image, root, base_url, locator) for image in image_elements
    ]
    return {
        "occurrence_id": source_range.occurrence_id,
        "ordinal": ordinal,
        "cell_kind": (
            "header" if str(cell_element.tag).lower() == "th" else "data"
        ),
        "text": text_field.value,
        "rowspan": _positive_int(cell_element.get("rowspan")),
        "colspan": _positive_int(cell_element.get("colspan")),
        "images": [asset for asset in assets if not _is_correction_asset(asset)],
        "source_events": [
            _source_event(asset) for asset in assets if _is_correction_asset(asset)
        ],
        "source": _provenance(
            locator.element_parts(cell_element, root),
            ownership="primary",
            owner_ref=source_range.occurrence_id,
        ),
        "_field_maps": ({"text": text_field} if text_field.value else {}),
    }


def _parse_orphan_cell_node(
    element: etree._Element,
    root: etree._Element,
    base_url: str,
    locator: SourceLocator,
    container_path: str,
) -> dict[str, Any]:
    cell = _parse_cell(element, None, root, base_url, locator, 1)
    return {
        "kind": "orphan_cell",
        **cell,
        "_container_path": container_path,
    }


def _parse_uncontained_images(
    container: etree._Element,
    table_element: etree._Element,
    root: etree._Element,
    base_url: str,
    locator: SourceLocator,
    *,
    row_element: etree._Element | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assets: list[dict[str, Any]] = []
    for image in container.xpath(".//img[@src]"):
        if _nearest_ancestor(image, {"table"}) is not table_element:
            continue
        nearest_row = _nearest_ancestor(image, {"tr"})
        if nearest_row is not row_element:
            continue
        if _nearest_ancestor(image, {"td", "th"}) is not None:
            continue
        assets.append(_image_asset(image, root, base_url, locator))
    return (
        [asset for asset in assets if not _is_correction_asset(asset)],
        [_source_event(asset) for asset in assets if _is_correction_asset(asset)],
    )


def _parse_table(
    element: etree._Element,
    root: etree._Element,
    base_url: str,
    locator: SourceLocator,
) -> dict[str, Any]:
    table_range = locator.element_range(element, root)
    table_source = _provenance(
        locator.element_parts(element, root),
        ownership="primary",
        owner_ref=table_range.occurrence_id,
    )
    caption_elements = element.xpath("./caption")
    caption_field = (
        locator.render_element_field(caption_elements[0], root)
        if caption_elements
        else None
    )
    caption = caption_field.value if caption_field is not None else None
    caption_source = (
        _provenance(
            locator.element_parts(caption_elements[0], root),
            ownership="primary",
            owner_ref=locator.element_range(caption_elements[0], root).occurrence_id,
        )
        if caption_elements
        else None
    )
    rows: list[dict[str, Any]] = []
    footnotes: list[dict[str, Any]] = []
    for row_element in element.xpath(".//tr"):
        nearest_table = next(row_element.iterancestors("table"), None)
        if nearest_table is not element:
            continue
        row_range = locator.element_range(row_element, root)
        cell_elements = [
            child for child in row_element if str(child.tag).lower() in {"td", "th"}
        ]
        cells = [
            _parse_cell(
                cell_element,
                element,
                root,
                base_url,
                locator,
                cell_ordinal,
            )
            for cell_ordinal, cell_element in enumerate(cell_elements, start=1)
        ]
        row_images, row_events = _parse_uncontained_images(
            row_element,
            element,
            root,
            base_url,
            locator,
            row_element=row_element,
        )
        row_source = _provenance(
            locator.element_parts(row_element, root),
            ownership="primary",
            owner_ref=row_range.occurrence_id,
        )
        row_text = _compact_text(" ".join(cell["text"] for cell in cells))
        in_tfoot = any(
            str(ancestor.tag).lower() == "tfoot"
            for ancestor in row_element.iterancestors()
        )
        parsed_footnote = None
        if len(cells) == 1 and cells[0].get("_field_maps", {}).get("text"):
            parsed_footnote = _footnote(
                cells[0]["_field_maps"]["text"],
                row_source,
                footnote_id=f"{row_range.occurrence_id}:footnote",
            )
        is_footnote = parsed_footnote is not None and (in_tfoot or len(cells) == 1)
        if is_footnote:
            cells[0]["_field_maps"]["text"].ownership = "aggregate"
            cells[0]["_field_maps"]["text"].owner = None
            cells[0]["_field_maps"]["text"].derivation = "footnote_row_aggregate"
            footnotes.append(parsed_footnote)
        rows.append(
            {
                "occurrence_id": row_range.occurrence_id,
                "ordinal": len(rows) + 1,
                "row_role": "footnote" if is_footnote else "data",
                "cells": cells,
                "images": row_images,
                "source_events": row_events,
                "footnote_id": (
                    parsed_footnote.get("footnote_id") if parsed_footnote else None
                ),
                "source": row_source,
            }
        )

    orphan_cell_elements = [
        cell
        for cell in element.xpath(".//td | .//th")
        if _nearest_ancestor(cell, {"table"}) is element
        and _nearest_ancestor(cell, {"tr"}) is None
    ]
    orphan_cells = [
        _parse_cell(cell, element, root, base_url, locator, ordinal)
        for ordinal, cell in enumerate(orphan_cell_elements, start=1)
    ]
    table_images, table_events = _parse_uncontained_images(
        element,
        element,
        root,
        base_url,
        locator,
        row_element=None,
    )
    child_tables = [
        _parse_table(child, root, base_url, locator)
        for child in element.xpath(".//table")
        if _nearest_ancestor(child, {"table"}) is element
    ]
    caption_kind, caption_label = _visible_caption_metadata(caption)
    first_cell = next(
        (
            cell
            for row in rows
            for cell in row["cells"]
        ),
        orphan_cells[0] if orphan_cells else None,
    )
    if caption is None and first_cell is not None:
        candidate_kind, candidate_label = _visible_caption_metadata(first_cell["text"])
        if candidate_kind is not None:
            caption = first_cell["text"]
            caption_field = FieldMap(
                caption,
                first_cell["_field_maps"]["text"].characters,
                ownership="alias",
                owner=first_cell["_field_maps"]["text"],
                derivation="first_cell_caption",
            )
            caption_source = _alias_provenance(
                first_cell["source"], first_cell["occurrence_id"]
            )
            caption_kind = candidate_kind
            caption_label = candidate_label
    image_count = (
        sum(len(cell["images"]) for row in rows for cell in row["cells"])
        + sum(len(cell["images"]) for cell in orphan_cells)
        + sum(len(row["images"]) for row in rows)
        + len(table_images)
    )
    semantic_rows = [
        row
        for row in rows
        if row["cells"] or row["images"] or row["source_events"]
    ]
    one_cell = len(semantic_rows) <= 1 and all(
        len(row["cells"]) <= 1 for row in semantic_rows
    )
    bordered = element.get("border") not in {None, "", "0"}
    provisional_role = (
        "captioned_semantic"
        if caption_kind == "table"
        else "figure_layout"
        if caption_kind == "figure"
        else "callout"
        if bordered and one_cell and image_count == 0
        else "layout"
    )
    return {
        "kind": "table",
        "occurrence_id": table_range.occurrence_id,
        "caption": caption or None,
        "caption_kind": caption_kind,
        "caption_label": caption_label,
        "caption_source": caption_source,
        "table_role": provisional_role,
        "rows": rows,
        "orphan_cells": orphan_cells,
        "images": table_images,
        "source_events": table_events,
        "footnotes": footnotes,
        "children": child_tables,
        "source": table_source,
        "_field_maps": ({"caption": caption_field} if caption_field else {}),
        "_container_path": _stable_path(
            element.getparent() if element.getparent() is not None else root, root
        ),
    }


def _boundary(tokens: list[dict[str, Any]], context: ParseContext) -> None:
    tokens.append({"token_kind": "boundary", "context": context})


def _walk_mixed(
    element: etree._Element,
    root: etree._Element,
    base_url: str,
    context: ParseContext,
    tokens: list[dict[str, Any]],
    locator: SourceLocator,
) -> None:
    if element.text:
        dom_path = _stable_path(element, root) + "/text()[1]"
        source_parts, characters = locator.text_mapping(
            dom_path, element.text, "text"
        )
        tokens.append(
            {
                "token_kind": "text",
                "text": element.text,
                "source_parts": source_parts,
                "characters": characters,
                "context": context,
            }
        )
    for child in element:
        _walk_element(child, root, base_url, context, tokens, locator)
        if child.tail:
            dom_path = _stable_path(child, root) + "/tail()[1]"
            source_parts, characters = locator.text_mapping(
                dom_path, child.tail, "tail"
            )
            tokens.append(
                {
                    "token_kind": "text",
                    "text": child.tail,
                    "source_parts": source_parts,
                    "characters": characters,
                    "context": context,
                }
            )


def _list_marker(element: etree._Element, list_kind: str) -> str:
    if list_kind == "unordered":
        return "\u2022"
    value = element.get("value")
    if value:
        return f"{value}."
    parent = element.getparent()
    start = int(parent.get("start", "1") or "1") if parent is not None else 1
    siblings = (
        [child for child in parent if str(child.tag).lower() == "li"]
        if parent is not None
        else [element]
    )
    return f"{start + siblings.index(element)}."


def _walk_element(
    element: etree._Element,
    root: etree._Element,
    base_url: str,
    context: ParseContext,
    tokens: list[dict[str, Any]],
    locator: SourceLocator,
) -> None:
    tag = str(element.tag).lower()
    path = _stable_path(element, root)
    if tag == "table":
        _boundary(tokens, context)
        tokens.append(
            {
                "token_kind": "table",
                "node": _parse_table(element, root, base_url, locator),
                "context": context,
            }
        )
        locator.skip_element_text(element, root)
        _boundary(tokens, context)
        return
    if tag in {"td", "th"}:
        _boundary(tokens, context)
        tokens.append(
            {
                "token_kind": "orphan_cell",
                "node": _parse_orphan_cell_node(
                    element,
                    root,
                    base_url,
                    locator,
                    context.container_path,
                ),
                "context": context,
            }
        )
        locator.skip_element_text(element, root)
        _boundary(tokens, context)
        return
    if tag == "img":
        asset = _image_asset(element, root, base_url, locator)
        tokens.append(
            {
                "token_kind": "source_event" if _is_correction_asset(asset) else "image",
                "asset": asset,
                "context": context,
            }
        )
        return
    if tag == "br":
        tokens.append(
            {
                "token_kind": "line_break",
                "source_parts": locator.element_parts(element, root),
                "characters": (
                    locator.element_character(element, root, "\n", "line_break"),
                ),
                "context": context,
            }
        )
        return
    if tag == "hr":
        _boundary(tokens, context)
        return
    if tag == "p":
        _boundary(tokens, context)
        paragraph_context = replace(context, paragraph=True)
        _walk_mixed(element, root, base_url, paragraph_context, tokens, locator)
        _boundary(tokens, paragraph_context)
        return
    if tag in BLOCKQUOTE_TAGS:
        _boundary(tokens, context)
        child_context = replace(
            context,
            quote_depth=context.quote_depth + 1,
            container_path=path,
        )
        _walk_mixed(element, root, base_url, child_context, tokens, locator)
        _boundary(tokens, child_context)
        return
    if tag in {"center", "figure"}:
        _boundary(tokens, context)
        child_context = replace(context, centered=True, container_path=path)
        _walk_mixed(element, root, base_url, child_context, tokens, locator)
        _boundary(tokens, child_context)
        return
    if tag in {"ol", "ul"}:
        _boundary(tokens, context)
        list_kind = "ordered" if tag == "ol" else "unordered"
        list_context = replace(
            context,
            list_depth=context.list_depth + 1,
            list_kind=list_kind,
            container_path=path,
        )
        _walk_mixed(element, root, base_url, list_context, tokens, locator)
        _boundary(tokens, list_context)
        return
    if tag == "li":
        _boundary(tokens, context)
        item_context = replace(
            context,
            paragraph=True,
            list_marker=_list_marker(element, context.list_kind or "unordered"),
            container_path=path,
        )
        _walk_mixed(element, root, base_url, item_context, tokens, locator)
        _boundary(tokens, item_context)
        return
    if tag in BLOCK_TAGS:
        _boundary(tokens, context)
        block_context = replace(context, paragraph=True, container_path=path)
        _walk_mixed(element, root, base_url, block_context, tokens, locator)
        _boundary(tokens, block_context)
        return
    if tag in {"a", "b", "strong"}:
        caption_kind, _ = _visible_caption_metadata(_rendered_text(element))
        inline_context = (
            replace(context, caption_hint=caption_kind) if caption_kind else context
        )
        _walk_mixed(element, root, base_url, inline_context, tokens, locator)
        return
    _walk_mixed(element, root, base_url, context, tokens, locator)


def _draft_text_node(buffer: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    characters: list[MappedChar] = []
    parts: list[dict[str, Any]] = []
    contexts: list[ParseContext] = []
    for token in buffer:
        characters.extend(token["characters"])
        parts.extend(token["source_parts"])
        if token["token_kind"] != "line_break":
            if token["text"].strip():
                contexts.append(token["context"])
    text, normalized_characters = _clean_mapped_characters(characters)
    if not text or not parts:
        return None
    text_field = FieldMap(text, normalized_characters)
    context = contexts[0] if contexts else buffer[0]["context"]
    node: dict[str, Any] = {
        "kind": "paragraph" if any(item.paragraph for item in contexts) else "prose",
        "text": text,
        "source": _provenance(parts, ownership="aggregate"),
        "_field_maps": {"text": text_field},
        "_container_path": context.container_path,
        "_caption_hint": next(
            (item.caption_hint for item in contexts if item.caption_hint), None
        ),
    }
    marker = context.list_marker
    list_kind = context.list_kind
    marker_start: int | None = None
    if marker is None:
        ordered_match = LIST_MARKER_RE.match(text)
        unordered_match = UNORDERED_MARKER_RE.match(text)
        if ordered_match:
            marker = ordered_match.group("marker")
            marker_start = ordered_match.start("marker")
            list_kind = "ordered"
        elif unordered_match:
            marker = unordered_match.group("marker")
            marker_start = unordered_match.start("marker")
            list_kind = "unordered"
    else:
        marker_start = text.find(marker)
    footnote_match = FOOTNOTE_RE.match(text)
    if footnote_match:
        marker_start = footnote_match.start("marker")
        marker_end = footnote_match.end("marker")
        body_start = footnote_match.start("text")
        body_end = footnote_match.end("text")
        marker_field = _slice_field(
            text_field, marker_start, marker_end, ownership="primary"
        )
        body_field = _slice_field(
            text_field, body_start, body_end, ownership="primary"
        )
        node.update(
            {
                "kind": "footnote",
                "marker": marker_field.value,
                "text": body_field.value,
                "_field_maps": {
                    "marker": marker_field,
                    "text": body_field,
                },
            }
        )
        return node
    if marker is not None:
        marker_start = marker_start if marker_start is not None and marker_start >= 0 else 0
        node.update(
            {
                "kind": "list_item",
                "list_kind": list_kind or "unordered",
                "marker": marker,
                "nesting": (
                    max(0, context.list_depth - 1)
                    if context.list_depth
                    else max(0, context.quote_depth - 1)
                ),
            }
        )
        if text[marker_start : marker_start + len(marker)] == marker:
            node["_field_maps"]["marker"] = _slice_field(
                text_field,
                marker_start,
                marker_start + len(marker),
                ownership="alias",
                derivation="list_marker",
            )
        else:
            node["_field_maps"]["marker"] = _synthetic_field(
                marker, "html_list_marker", text_field
            )
    note_match = NOTE_LABEL_RE.match(text)
    if note_match:
        label_start = note_match.start("label")
        label_end = note_match.end("label")
        node.update(
            {
                "kind": "note",
                "label": note_match.group("label").strip(),
                "children": [],
            }
        )
        node["_field_maps"]["label"] = _slice_field(
            text_field,
            label_start,
            label_end,
            ownership="alias",
            derivation="note_label",
        )
        for key in ("list_kind", "marker", "nesting"):
            node.pop(key, None)
    return node


def _attach_caption(
    figure: dict[str, Any], draft: dict[str, Any]
) -> dict[str, Any] | None:
    text = draft["text"]
    text_field = draft["_field_maps"]["text"]
    note_match = NOTE_WITHIN_TEXT_RE.search(text)
    note: dict[str, Any] | None = None
    if note_match:
        caption_field = _renormalize_field(
            _slice_field(
                text_field,
                0,
                note_match.start(),
                ownership="primary",
                derivation="figure_caption",
            ),
            ownership="primary",
            derivation="figure_caption",
        )
        note_field = _renormalize_field(
            _slice_field(
                text_field,
                note_match.start(),
                len(text),
                ownership="primary",
                derivation="figure_note",
            ),
            ownership="primary",
            derivation="figure_note",
        )
        caption = caption_field.value
        if note_field.value:
            label_start = note_match.start("label")
            label_end = note_match.end("label")
            label_field = _renormalize_field(
                _slice_field(
                    text_field,
                    label_start,
                    label_end,
                    ownership="alias",
                    owner=note_field,
                    derivation="note_label",
                ),
                ownership="alias",
                owner=note_field,
                derivation="note_label",
            )
            note = {
                "kind": "note",
                "label": label_field.value,
                "text": note_field.value,
                "children": [],
                "source": draft["source"],
                "_field_maps": {"label": label_field, "text": note_field},
                "_container_path": draft["_container_path"],
            }
    else:
        caption = text
        caption_field = text_field
    figure["caption"] = caption or None
    figure["caption_kind"], figure["caption_label"] = _visible_caption_metadata(
        figure["caption"]
    )
    figure["caption_source"] = draft["source"] if caption else None
    figure["source"] = _merge_provenance(figure["source"], draft["source"])
    if caption:
        figure.setdefault("_field_maps", {})["caption"] = caption_field
    return note


def _tokens_to_nodes(tokens: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    buffer: list[dict[str, Any]] = []
    caption_target: dict[str, Any] | None = None
    caption_enabled = False

    def flush() -> None:
        nonlocal buffer, caption_target, caption_enabled
        draft = _draft_text_node(buffer)
        buffer = []
        if draft is None:
            return
        if caption_target is not None and caption_enabled:
            note = _attach_caption(caption_target, draft)
            if note is not None:
                nodes.append(note)
            caption_target = None
            caption_enabled = False
            return
        caption_target = None
        caption_enabled = False
        nodes.append(draft)

    for token in tokens:
        token_kind = token["token_kind"]
        if token_kind == "text":
            buffer.append(token)
            continue
        if token_kind == "line_break":
            if caption_target is not None and not any(
                item.get("text", "").strip() for item in buffer if item["token_kind"] == "text"
            ):
                buffer = []
                caption_enabled = True
            else:
                buffer.append(token)
            continue
        if token_kind == "boundary":
            flush()
            caption_target = None
            caption_enabled = False
            continue
        if token_kind == "table":
            flush()
            nodes.append(token["node"])
            caption_target = None
            caption_enabled = False
            continue
        if token_kind == "orphan_cell":
            flush()
            nodes.append(token["node"])
            caption_target = None
            caption_enabled = False
            continue
        if token_kind == "image":
            flush()
            asset = token["asset"]
            figure = {
                "kind": "figure",
                "caption": None,
                "caption_kind": None,
                "caption_label": None,
                "caption_source": None,
                "images": [asset],
                "source": _provenance(
                    asset["source"]["parts"], ownership="aggregate"
                ),
                "_field_maps": {},
                "_container_path": token["context"].container_path,
            }
            nodes.append(figure)
            caption_target = figure
            caption_enabled = False
            continue
        if token_kind == "source_event":
            flush()
            nodes.append(
                _source_event(token["asset"], token["context"].container_path)
            )
            caption_target = None
            caption_enabled = False
            continue
        raise ValueError(f"Unknown token kind: {token_kind}")
    flush()
    return nodes


def _attach_table_metadata(nodes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(nodes):
        node = nodes[index]
        if node["kind"] == "table" and node.get("children"):
            node["children"] = _attach_table_metadata(node["children"])
        if node["kind"] == "table" and result:
            previous = result[-1]
            caption_kind, caption_label = _visible_caption_metadata(previous.get("text"))
            if (
                previous["kind"] in {"paragraph", "prose"}
                and previous.get("_caption_hint") == "table"
                and caption_kind == "table"
            ):
                result.pop()
                node["caption"] = previous["text"]
                node["caption_kind"] = caption_kind
                node["caption_label"] = caption_label
                node["caption_source"] = previous["source"]
                node["table_role"] = "captioned_semantic"
                node.setdefault("_field_maps", {})["caption"] = previous[
                    "_field_maps"
                ]["text"]
        if node["kind"] == "table" and index + 1 < len(nodes):
            following = nodes[index + 1]
            parsed: dict[str, Any] | None = None
            if following["kind"] == "footnote":
                parsed = {
                    "footnote_id": (
                        f"{node['occurrence_id']}:footnote:{len(node['footnotes']) + 1:02d}"
                    ),
                    "marker": following["marker"],
                    "text": following["text"],
                    "source": following["source"],
                    "_field_maps": following["_field_maps"],
                }
            elif following["kind"] in {"paragraph", "prose"}:
                text_field = following.get("_field_maps", {}).get("text")
                if text_field is not None:
                    parsed = _footnote(
                        text_field,
                        following["source"],
                        footnote_id=(
                            f"{node['occurrence_id']}:footnote:"
                            f"{len(node['footnotes']) + 1:02d}"
                        ),
                    )
            if parsed is not None:
                node["footnotes"].append(parsed)
                index += 1
        result.append(node)
        index += 1
    return result


def _attach_visible_figure_captions(
    nodes: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes:
        if node["kind"] == "table" and node.get("children"):
            node["children"] = _attach_visible_figure_captions(node["children"])
        if node["kind"] in {"table", "figure"}:
            kind, label = _visible_caption_metadata(node.get("caption"))
            if kind is not None:
                node["caption_kind"] = kind
                node["caption_label"] = label
                if node["kind"] == "table" and kind == "figure":
                    node["table_role"] = "figure_layout"
        caption_kind, caption_label = _visible_caption_metadata(node.get("text"))
        if (
            caption_kind == "figure"
            and node["kind"] in {"paragraph", "prose"}
            and node.get("_caption_hint") == "figure"
            and result
            and result[-1]["kind"] in {"table", "figure"}
        ):
            visual = result[-1]
            visual["caption"] = node["text"]
            visual["caption_kind"] = "figure"
            visual["caption_label"] = caption_label
            visual["caption_source"] = node["source"]
            visual["source"] = _merge_provenance(visual["source"], node["source"])
            visual.setdefault("_field_maps", {})["caption"] = node[
                "_field_maps"
            ]["text"]
            if visual["kind"] == "table":
                visual["table_role"] = "figure_layout"
            continue
        result.append(node)
    return result


def _classify_standalone_captions(
    nodes: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    for node in nodes:
        if node["kind"] not in {"paragraph", "prose"}:
            continue
        caption_kind, caption_label = _visible_caption_metadata(node.get("text"))
        if caption_kind is None or node.get("_caption_hint") != caption_kind:
            continue
        node["kind"] = "caption"
        node["caption_kind"] = caption_kind
        node["caption_label"] = caption_label
    return list(nodes)


def _example_label_match(node: dict[str, Any]) -> re.Match[str] | None:
    if node["kind"] not in {"paragraph", "prose"}:
        return None
    return EXAMPLE_LABEL_RE.match(node.get("text", ""))


def _group_examples(nodes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(nodes):
        label_node = nodes[index]
        match = _example_label_match(label_node)
        if match is None:
            result.append(label_node)
            index += 1
            continue

        label = match.group("label").strip()
        numbered = re.match(r"(?i)^Example\s+\d+\b", label) is not None
        children: list[dict[str, Any]] = []
        source_field = label_node["_field_maps"]["text"]
        label_field = _field_from_match(
            source_field,
            match,
            "label",
            ownership="primary",
            derivation="example_label",
        )
        remainder_field = _field_from_match(
            source_field,
            match,
            "rest",
            ownership="primary",
            derivation="example_inline_remainder",
        )
        if remainder_field.value:
            children.append(
                {
                    "kind": label_node["kind"],
                    "text": remainder_field.value,
                    "source": _provenance(
                        label_node["source"]["parts"], ownership="aggregate"
                    ),
                    "_field_maps": {"text": remainder_field},
                    "_container_path": label_node.get("_container_path", "/fragment"),
                }
            )

        next_index = index + 1
        first_container: str | None = None
        while next_index < len(nodes):
            candidate = nodes[next_index]
            if (
                candidate["kind"] in {"heading", "note", "list_item"}
                or _example_label_match(candidate) is not None
            ):
                break
            container = candidate.get("_container_path", "/fragment")
            if first_container is None:
                first_container = container
            if not numbered:
                if first_container != "/fragment" and container != first_container:
                    break
                if first_container == "/fragment" and children and candidate["kind"] not in {
                    "figure",
                    "table",
                    "source_event",
                }:
                    break
            children.append(candidate)
            next_index += 1

        source = _merge_provenance(
            label_node["source"], *(child["source"] for child in children)
        )
        result.append(
            {
                "kind": "example_block",
                "label": label,
                "number": (
                    int(re.search(r"\d+", label).group(0)) if numbered else None
                ),
                "children": children,
                "source": source,
                "_field_maps": {"label": label_field},
                "_container_path": label_node.get("_container_path", "/fragment"),
            }
        )
        index = next_index
    return result


def _scope_notes(nodes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(nodes):
        node = nodes[index]
        if node["kind"] == "example_block":
            node["children"] = _scope_notes(node["children"])
        if node["kind"] != "note":
            result.append(node)
            index += 1
            continue
        node.setdefault("children", [])
        match = NOTE_LABEL_RE.match(node["text"])
        label_only = bool(match and not _clean_text(match.group("rest")))
        next_index = index + 1
        container = node.get("_container_path", "/fragment")
        while label_only and next_index < len(nodes):
            candidate = nodes[next_index]
            if (
                candidate["kind"] in {"heading", "note", "list_item", "example_block"}
                or _example_label_match(candidate) is not None
            ):
                break
            if (
                container != "/fragment"
                and candidate.get("_container_path", "/fragment") != container
            ):
                break
            node["children"].append(candidate)
            next_index += 1
            if container == "/fragment" and candidate["kind"] not in {
                "figure",
                "table",
                "source_event",
            }:
                break
        if node["children"]:
            node["source"] = _merge_provenance(
                node["source"], *(child["source"] for child in node["children"])
            )
        result.append(node)
        index = next_index
    return result


def _marker_core(marker: str) -> str:
    return marker.strip().strip("().")


def _list_family(marker: str, peer_markers: Sequence[str], list_kind: str) -> str:
    if list_kind == "unordered":
        return "unordered"
    core = _marker_core(marker)
    if core.isdigit():
        return "arabic"
    peers = [_marker_core(value) for value in peer_markers]
    roman = all(value and all(character.lower() in "ivxlcdm" for character in value) for value in peers)
    if roman and any(len(value) > 1 for value in peers):
        return "upper_roman" if core.isupper() else "lower_roman"
    return "upper_alpha" if core.isupper() else "lower_alpha"


def _cue_kind(text: str | None) -> str:
    if not text:
        return "unspecified"
    compact = _compact_text(text).lower()
    if re.search(r"\b(?:in (?:the following )?order|in order|decreasing order|increasing order)\b", compact):
        return "explicit_order"
    if re.search(r"\bcriteri(?:a|on)\b", compact):
        return "criteria"
    if re.search(r"\b(?:steps?|procedure)\b", compact):
        return "procedure"
    if re.search(r"\b(?:alternatives?|either|ways?|methods?)\b", compact):
        return "alternatives"
    if re.search(r"\b(?:following|listed|types?|kinds?)\b", compact):
        return "enumeration"
    return "unspecified"


def _annotate_lists(nodes: Sequence[dict[str, Any]]) -> None:
    markers_by_nesting: dict[int, list[str]] = defaultdict(list)
    for node in nodes:
        if node["kind"] == "list_item":
            markers_by_nesting[node["nesting"]].append(node["marker"])
    for index, node in enumerate(nodes):
        if node["kind"] in {"example_block", "note"}:
            _annotate_lists(node.get("children", []))
        if node["kind"] != "list_item":
            continue
        cue_node = next(
            (
                previous
                for previous in reversed(nodes[:index])
                if previous["kind"] in {"heading", "paragraph", "prose"}
            ),
            None,
        )
        cue_text = cue_node.get("text") if cue_node else None
        cue_field = (
            cue_node.get("_field_maps", {}).get("text") if cue_node else None
        )
        node["list_family"] = _list_family(
            node["marker"], markers_by_nesting[node["nesting"]], node["list_kind"]
        )
        node["semantics_cue"] = {
            "kind": _cue_kind(cue_text),
            "text": cue_text,
            "source": (
                _provenance(cue_node["source"]["parts"], ownership="aggregate")
                if cue_node
                else None
            ),
            "_owner_node": cue_node,
            "_field_maps": (
                {
                    "text": FieldMap(
                        cue_text,
                        cue_field.characters,
                        ownership="alias",
                        owner=cue_field,
                        derivation="list_semantics_cue",
                    )
                }
                if cue_field is not None
                else {}
            ),
        }


def _classify_table_roles(nodes: Sequence[dict[str, Any]], in_example: bool = False) -> None:
    for node in nodes:
        if node["kind"] == "example_block":
            _classify_table_roles(node["children"], True)
            continue
        if node["kind"] == "note":
            _classify_table_roles(node.get("children", []), in_example)
            continue
        if node["kind"] != "table":
            continue
        if node["caption_kind"] == "table":
            node["table_role"] = "captioned_semantic"
        elif node["caption_kind"] == "figure":
            node["table_role"] = "figure_layout"
        elif node["table_role"] == "callout":
            pass
        elif in_example:
            node["table_role"] = "example_layout"
        else:
            cells = [
                *[cell for row in node["rows"] for cell in row["cells"]],
                *node.get("orphan_cells", []),
            ]
            image_count = (
                sum(len(cell["images"]) for cell in cells)
                + sum(len(row.get("images", [])) for row in node["rows"])
                + len(node.get("images", []))
            )
            semantic_rows = [
                row
                for row in node["rows"]
                if row["cells"] or row.get("images") or row.get("source_events")
            ]
            if image_count:
                node["table_role"] = "figure_layout"
            elif len(semantic_rows) > 1 and max(
                (len(row["cells"]) for row in semantic_rows), default=0
            ) > 1:
                node["table_role"] = "uncaptioned_mapping"
            else:
                node["table_role"] = "layout"
        _classify_table_roles(node.get("children", []), in_example)


def _replace_heading(nodes: list[dict[str, Any]], rule_id: str) -> None:
    for index, original in enumerate(nodes):
        if original["kind"] not in {"paragraph", "prose", "list_item", "note"}:
            continue
        match = re.match(
            rf"^\s*(?P<label>{re.escape(rule_id)})[.;:]?\s*(?P<title>.*)$",
            original["text"],
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            continue
        text_field = original["_field_maps"]["text"]
        label_field = _field_from_match(
            text_field,
            match,
            "label",
            ownership="alias",
            derivation="heading_label",
        )
        title_slice = _slice_field(
            text_field,
            match.start("title"),
            match.end("title"),
            ownership="alias",
            derivation="heading_title",
        )
        title_field = _compact_field(title_slice)
        title_field.derivation = "heading_title_whitespace_compaction"
        nodes[index] = {
            "kind": "heading",
            "label": label_field.value,
            "title": title_field.value,
            "text": original["text"],
            "source": original["source"],
            "_field_maps": {
                "label": label_field,
                "title": title_field,
                "text": text_field,
            },
            "_container_path": original.get("_container_path", "/fragment"),
        }
        return
    raise ValueError(f"Rule fragment {rule_id} did not yield its heading node")


def _iter_nodes(nodes: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for node in nodes:
        yield node
        yield from _iter_nodes(node.get("children", []))


def _finalize_nodes(
    nodes: list[dict[str, Any]], rule_id: str, locator: SourceLocator
) -> tuple[int, Counter[str]]:
    counts: Counter[str] = Counter()
    flat_nodes = list(_iter_nodes(nodes))
    for ordinal, node in enumerate(flat_nodes, start=1):
        node["ordinal"] = ordinal
        node["node_id"] = f"{rule_id}:node:{ordinal:04d}"
        if node["kind"] == "footnote":
            node["footnote_id"] = f"{node['node_id']}:footnote"
        node.pop("_container_path", None)
        node.pop("_caption_hint", None)
        counts[node["kind"]] += 1
    for node in flat_nodes:
        semantics_cue = node.get("semantics_cue")
        if not semantics_cue:
            continue
        owner_node = semantics_cue.pop("_owner_node", None)
        if owner_node is not None:
            semantics_cue["source"] = _alias_provenance(
                owner_node["source"], owner_node["node_id"]
            )
    _finalize_field_maps(nodes, locator)
    return len(flat_nodes), counts


def _node_source_metrics(nodes: Iterable[dict[str, Any]]) -> dict[str, Any]:
    table_occurrences: list[str] = []
    row_occurrences: list[str] = []
    cell_occurrences: list[str] = []
    image_occurrences: list[str] = []
    correction_occurrences: list[str] = []
    footnote_count = 0
    table_captions: list[str] = []
    figure_captions: list[str] = []
    for node in _iter_nodes(nodes):
        if node["kind"] == "figure":
            image_occurrences.extend(image["occurrence_id"] for image in node["images"])
        elif node["kind"] == "source_event":
            image_occurrences.append(node["icon"]["occurrence_id"])
            if node["event_kind"] == "correction":
                correction_occurrences.append(node["occurrence_id"])
        elif node["kind"] == "footnote":
            footnote_count += 1
        elif node["kind"] == "orphan_cell":
            cell_occurrences.append(node["occurrence_id"])
            image_occurrences.extend(
                image["occurrence_id"] for image in node["images"]
            )
            image_occurrences.extend(
                event["icon"]["occurrence_id"] for event in node["source_events"]
            )
            correction_occurrences.extend(
                event["occurrence_id"]
                for event in node["source_events"]
                if event["event_kind"] == "correction"
            )
        elif node["kind"] == "table":
            table_occurrences.append(node["occurrence_id"])
            footnote_count += len(node["footnotes"])
            image_occurrences.extend(
                image["occurrence_id"] for image in node.get("images", [])
            )
            image_occurrences.extend(
                event["icon"]["occurrence_id"]
                for event in node.get("source_events", [])
            )
            correction_occurrences.extend(
                event["occurrence_id"]
                for event in node.get("source_events", [])
                if event["event_kind"] == "correction"
            )
            for row in node["rows"]:
                row_occurrences.append(row["occurrence_id"])
                image_occurrences.extend(
                    image["occurrence_id"] for image in row.get("images", [])
                )
                image_occurrences.extend(
                    event["icon"]["occurrence_id"]
                    for event in row.get("source_events", [])
                )
                correction_occurrences.extend(
                    event["occurrence_id"]
                    for event in row.get("source_events", [])
                    if event["event_kind"] == "correction"
                )
                for cell in row["cells"]:
                    cell_occurrences.append(cell["occurrence_id"])
                    image_occurrences.extend(
                        image["occurrence_id"] for image in cell["images"]
                    )
                    image_occurrences.extend(
                        event["icon"]["occurrence_id"]
                        for event in cell["source_events"]
                    )
                    correction_occurrences.extend(
                        event["occurrence_id"]
                        for event in cell["source_events"]
                        if event["event_kind"] == "correction"
                    )
            for cell in node.get("orphan_cells", []):
                cell_occurrences.append(cell["occurrence_id"])
                image_occurrences.extend(
                    image["occurrence_id"] for image in cell["images"]
                )
                image_occurrences.extend(
                    event["icon"]["occurrence_id"]
                    for event in cell["source_events"]
                )
                correction_occurrences.extend(
                    event["occurrence_id"]
                    for event in cell["source_events"]
                    if event["event_kind"] == "correction"
                )
        if node["kind"] in {"table", "figure", "caption"} and node.get("caption_label"):
            if node["caption_kind"] == "table":
                table_captions.append(node["caption_label"])
            elif node["caption_kind"] == "figure":
                figure_captions.append(node["caption_label"])
    return {
        "physical_table_occurrence_count": len(table_occurrences),
        "physical_row_occurrence_count": len(row_occurrences),
        "physical_cell_occurrence_count": len(cell_occurrences),
        "physical_image_occurrence_count": len(image_occurrences),
        "correction_event_count": len(correction_occurrences),
        "footnote_block_count": footnote_count,
        "table_occurrence_ids": table_occurrences,
        "row_occurrence_ids": row_occurrences,
        "cell_occurrence_ids": cell_occurrences,
        "image_occurrence_ids": image_occurrences,
        "correction_occurrence_ids": correction_occurrences,
        "visible_table_captions": table_captions,
        "visible_figure_captions": figure_captions,
    }


def _parse_fragment(
    raw_fragment: bytes,
    rule_id: str,
    base_url: str,
    document_start: int,
) -> tuple[list[dict[str, Any]], int, Counter[str], dict[str, int]]:
    active_fragment = _strip_comments(raw_fragment)
    # The official P2 source contains one empty malformed closing tag (</>) that
    # lxml otherwise recovers as an invented literal ">" text character.
    parse_fragment = active_fragment.replace(b"</>", b"")
    source = parse_fragment.decode("utf-8", errors="replace")
    try:
        root = lxml_html.fragment_fromstring(source, create_parent="fragment")
    except (etree.ParserError, TypeError, ValueError) as error:
        raise ValueError(f"Could not parse HTML fragment for {rule_id}: {error}") from error
    for comment in root.xpath("//comment()"):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
    locator = SourceLocator(raw_fragment, document_start, rule_id)
    try:
        locator.bind_root(root)
    except ValueError as error:
        raise ValueError(f"Source element alignment failed for {rule_id}: {error}") from error
    tokens: list[dict[str, Any]] = []
    try:
        _walk_mixed(root, root, base_url, ParseContext(), tokens, locator)
    except ValueError as error:
        raise ValueError(f"Source alignment failed for {rule_id}: {error}") from error
    nodes = _tokens_to_nodes(tokens)
    nodes = _attach_table_metadata(nodes)
    nodes = _attach_visible_figure_captions(nodes)
    nodes = _classify_standalone_captions(nodes)
    _replace_heading(nodes, rule_id)
    nodes = _group_examples(nodes)
    nodes = _scope_notes(nodes)
    _annotate_lists(nodes)
    _classify_table_roles(nodes)
    node_count, kind_counts = _finalize_nodes(nodes, rule_id, locator)
    return nodes, node_count, kind_counts, locator.physical_counts()


def extract_document(cache_path: Path, document_id: str) -> dict[str, Any]:
    raw = cache_path.read_bytes()
    masked = _mask_comments(raw)
    document = lxml_html.fromstring(_strip_comments(raw).decode("utf-8", errors="replace"))
    base_url = _source_url(document, cache_path.name)
    matches = list(ACTIVE_RULE_ANCHOR_RE.finditer(masked))
    if not matches:
        raise ValueError(f"No active rule anchors found in {cache_path}")
    fragment_starts = [_line_start(raw, match.start()) for match in matches]
    if len(fragment_starts) != len(set(fragment_starts)):
        raise ValueError(f"Two active rule anchors share a source line in {cache_path}")

    fragments: list[dict[str, Any]] = []
    document_kind_counts: Counter[str] = Counter()
    document_node_count = 0
    physical_counts: Counter[str] = Counter()
    seen_rule_ids: set[str] = set()
    for index, match in enumerate(matches):
        rule_id = match.group(2).decode("ascii")
        if rule_id in seen_rule_ids:
            raise ValueError(f"Duplicate active rule id {rule_id} in {cache_path}")
        seen_rule_ids.add(rule_id)
        start = fragment_starts[index]
        end = (
            fragment_starts[index + 1]
            if index + 1 < len(fragment_starts)
            else _last_fragment_end(masked, match.end())
        )
        raw_fragment = raw[start:end]
        active_fragment = _strip_comments(raw_fragment)
        nodes, node_count, kind_counts, fragment_physical_counts = _parse_fragment(
            raw_fragment, rule_id, base_url, start
        )
        document_node_count += node_count
        document_kind_counts.update(kind_counts)
        physical_counts.update(fragment_physical_counts)
        fragment = {
            "rule_id": rule_id,
            "anchor": match.group(1).decode("ascii", errors="replace"),
            "ordinal": index + 1,
            "source": {
                "offset_unit": "byte",
                "start_byte": start,
                "end_byte": end,
                "anchor_start_byte": match.start(),
                "raw_sha256": sha256_bytes(raw_fragment),
                "active_sha256": sha256_bytes(active_fragment),
            },
            "node_count": node_count,
            "nodes": nodes,
        }
        fragment["field_source_metrics"] = validate_fragment_field_sources(
            fragment, raw_fragment
        )
        fragments.append(fragment)
    source_metrics = _node_source_metrics(
        node for fragment in fragments for node in fragment["nodes"]
    )
    represented_counts = {
        "table": source_metrics["physical_table_occurrence_count"],
        "row": source_metrics["physical_row_occurrence_count"],
        "cell": source_metrics["physical_cell_occurrence_count"],
        "image": source_metrics["physical_image_occurrence_count"],
    }
    if represented_counts != dict(physical_counts):
        raise ValueError(
            f"Physical occurrence coverage failed for {document_id}: "
            f"raw={dict(physical_counts)}, represented={represented_counts}"
        )
    return {
        "document_id": document_id,
        "cache_path": cache_path.name,
        "source_url": base_url,
        "source_encoding": "utf-8",
        "source_sha256": sha256_bytes(raw),
        "source_byte_count": len(raw),
        "active_rule_fragment_count": len(fragments),
        "document_node_count": document_node_count,
        "node_kind_counts": {kind: document_kind_counts[kind] for kind in NODE_KINDS},
        "source_metrics": {
            "physical_table_occurrence_count": source_metrics[
                "physical_table_occurrence_count"
            ],
            "physical_row_occurrence_count": source_metrics[
                "physical_row_occurrence_count"
            ],
            "physical_cell_occurrence_count": source_metrics[
                "physical_cell_occurrence_count"
            ],
            "physical_image_occurrence_count": source_metrics[
                "physical_image_occurrence_count"
            ],
            "correction_event_count": source_metrics["correction_event_count"],
            "footnote_block_count": source_metrics["footnote_block_count"],
            "visible_table_caption_count": len(source_metrics["visible_table_captions"]),
            "visible_figure_caption_count": len(source_metrics["visible_figure_captions"]),
        },
        "fragments": fragments,
    }


def extract_corpus(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    document_ids: set[str] | None = None,
) -> dict[str, Any]:
    selected = [
        (document_id, filename)
        for document_id, filename in CHAPTER_FILES
        if document_ids is None or document_id in document_ids
    ]
    if document_ids is not None:
        unknown = document_ids - {document_id for document_id, _ in CHAPTER_FILES}
        if unknown:
            raise ValueError(f"Unknown document ids: {', '.join(sorted(unknown))}")
    documents = [
        extract_document(cache_dir / filename, document_id)
        for document_id, filename in selected
    ]
    kind_counts: Counter[str] = Counter()
    for document in documents:
        kind_counts.update(document["node_kind_counts"])
    all_nodes = [
        node
        for document in documents
        for fragment in document["fragments"]
        for node in fragment["nodes"]
    ]
    source_metrics = _node_source_metrics(all_nodes)
    table_caption_keys = {
        label.rstrip(".") for label in source_metrics["visible_table_captions"]
    }
    figure_caption_keys = {
        label.rstrip(".") for label in source_metrics["visible_figure_captions"]
    }
    for occurrence_kind in ("table", "row", "cell", "image", "correction"):
        occurrence_ids = source_metrics[f"{occurrence_kind}_occurrence_ids"]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError(
                f"Duplicate physical {occurrence_kind} occurrence ids in corpus"
            )
    metrics = {
        "physical_table_occurrence_count": source_metrics[
            "physical_table_occurrence_count"
        ],
        "physical_row_occurrence_count": source_metrics[
            "physical_row_occurrence_count"
        ],
        "physical_cell_occurrence_count": source_metrics[
            "physical_cell_occurrence_count"
        ],
        "physical_image_occurrence_count": source_metrics[
            "physical_image_occurrence_count"
        ],
        "correction_event_count": source_metrics["correction_event_count"],
        "footnote_block_count": source_metrics["footnote_block_count"],
        "visible_table_caption_count": len(source_metrics["visible_table_captions"]),
        "distinct_visible_table_caption_count": len(table_caption_keys),
        "visible_figure_caption_count": len(source_metrics["visible_figure_captions"]),
        "distinct_visible_figure_caption_count": len(figure_caption_keys),
    }
    counters = {
        "document_count": len(documents),
        "active_rule_fragment_count": sum(
            document["active_rule_fragment_count"] for document in documents
        ),
        "document_node_count": sum(document["document_node_count"] for document in documents),
        "node_kind_counts": {kind: kind_counts[kind] for kind in NODE_KINDS},
    }
    if document_ids is None:
        observed = {
            "active_rule_fragment_count": counters["active_rule_fragment_count"],
            "physical_table_occurrence_count": metrics[
                "physical_table_occurrence_count"
            ],
            "physical_row_occurrence_count": metrics[
                "physical_row_occurrence_count"
            ],
            "physical_cell_occurrence_count": metrics[
                "physical_cell_occurrence_count"
            ],
            "physical_image_occurrence_count": metrics[
                "physical_image_occurrence_count"
            ],
            "footnote_block_count": metrics["footnote_block_count"],
            "visible_table_caption_count": metrics[
                "distinct_visible_table_caption_count"
            ],
            "visible_figure_caption_count": metrics[
                "distinct_visible_figure_caption_count"
            ],
            "correction_event_count": metrics["correction_event_count"],
        }
        if observed != EXPECTED_FULL_CORPUS_METRICS:
            raise ValueError(
                f"Full-corpus source invariants failed: expected "
                f"{EXPECTED_FULL_CORPUS_METRICS}, observed {observed}"
            )
    digest_payload = {"documents": documents, "counters": counters, "metrics": metrics}
    return {
        "format": "iupac-bluebook-document-nodes",
        "version": "2.0.0",
        "source_scope": "active normative rule fragments in cached official chapter HTML",
        "corpus_sha256": sha256_bytes(canonical_json_bytes(digest_payload)),
        "counters": counters,
        "metrics": metrics,
        "documents": documents,
    }


def validate_corpus(corpus: dict[str, Any], schema_path: Path = DEFAULT_SCHEMA) -> None:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as error:
        raise RuntimeError(
            "jsonschema is required for validation; install the conversion extra"
        ) from error
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(corpus), key=lambda item: list(item.absolute_path))
    if errors:
        details = "\n".join(
            f"- /{'/'.join(map(str, error.absolute_path))}: {error.message}"
            for error in errors[:25]
        )
        raise ValueError(f"Document-node corpus failed schema validation:\n{details}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract source-faithful typed document nodes from cached Blue Book HTML."
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--documents",
        nargs="+",
        choices=[document_id for document_id, _ in CHAPTER_FILES],
        help="Optional subset of chapter source documents to extract.",
    )
    parser.add_argument(
        "--no-validate", action="store_true", help="Skip JSON Schema validation."
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    corpus = extract_corpus(args.cache_dir, set(args.documents) if args.documents else None)
    if not args.no_validate:
        validate_corpus(corpus, args.schema)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(canonical_json_bytes(corpus))
    counters = corpus["counters"]
    print(
        f"Wrote {counters['active_rule_fragment_count']} active rule fragments and "
        f"{counters['document_node_count']} typed nodes to {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
