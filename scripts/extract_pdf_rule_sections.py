from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import re
import sys
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import pdfplumber
import lxml
from lxml import html as lxml_html
from pdfminer.pdftypes import PDFObjRef, PDFStream
from pdfminer.psparser import PSLiteral, literal_name


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = Path(r"C:\Users\MSI Gaming PC\Downloads\BlueBookV3.pdf")
DEFAULT_OUT_DIR = ROOT / "data" / "bluebook_v3"
DEFAULT_CACHE_DIR = ROOT / ".cache" / "bluebook_html"
PDF_URL = "https://iupac.qmul.ac.uk/BlueBook/PDF/BlueBookV3.pdf"
SOURCE_ROOT = "https://iupac.qmul.ac.uk/BlueBook/"

HTML_SOURCES = {
    "P-1": "P1.html",
    "P-2": "P2.html",
    "P-3": "P3.html",
    "P-4": "P4.html",
    "P-5": "P5.html",
    "P-6a": "P6.html",
    "P-6b": "P6a.html",
    "P-7": "P7.html",
    "P-8": "P8.html",
    "P-9": "P9.html",
    "P-10": "P10.html",
    "appendix-1": "Papp1.html",
    "appendix-2": "Papp2.html",
    "appendix-3": "Papp3.html",
    "references": "refs.html",
    "corrections": "changes2.html",
}

DOCUMENT_REGIONS = (
    ("contents", "front_matter", 1, 36),
    ("membership", "front_matter", 37, 38),
    ("preface_changes_acknowledgements", "front_matter", 39, 44),
    ("glossary", "glossary", 45, 47),
    ("chapters", "normative_chapters", 48, 1066),
    ("references", "references", 1067, 1069),
    ("appendix_1", "table", 1070, 1072),
    ("appendix_2", "table", 1073, 1120),
    ("appendix_3", "structure_registry", 1121, 1149),
)

RULE_ID = r"P-\d+(?:\.\d+)*(?:\([a-z0-9]+\))?"
RULE_AT_LINE_START_RE = re.compile(rf"^(?P<rule_id>{RULE_ID})[.;:]?(?:\s+|$)", re.I)
RULE_REF_RE = re.compile(rf"\b{RULE_ID}\b", re.I)
HTML_RULE_ANCHOR_RE = re.compile(
    rf"<a\s+name=[\"'](?P<anchor>[^\"']+)[\"'][^>]*>\s*"
    rf"(?:<[^>]+>\s*)*(?P<rule_id>{RULE_ID})\b",
    re.I,
)
CHAPTER_RE = re.compile(r"^Chapter\s+(P-(?:10|[1-9]))\b", re.I)

# The current corrected HTML restores a missing subheading whose operative text is
# printed under P-65.1.2 in the PDF. The PDF TOC and P-65.1.2.2 both refer to it.
KNOWN_STRUCTURAL_ALIGNMENTS = {
    "P-65.1.2.1": {
        "pdf_printed_label": "P-65.1.2",
        "pdf_page": 572,
        "parent_full_source_line_count": 2,
        "splice_line_id": "p0572:l025",
        "splice_text": "describe chain are named by replacing the final ‘e’ of the name of the corresponding hydrocarbon by the suffix ‘oic",
        "splice_text_start_for_child": 9,
        "reason": (
            "HTML/TOC/internal-reference subheading omitted from the printed PDF body; "
            "the adjacent PDF prose also merges and omits text present in the corrected HTML"
        ),
    }
}

KNOWN_PDF_LAYOUT_REORDERINGS = {
    "P-33.1": {
        "source_line_ids": [
            *[f"p0341:l{line:03d}" for line in range(3, 32)],
            *[f"p0341:l{line:03d}" for line in range(33, 51)],
        ],
        "reason": (
            "The P-33.2 printed label floats beside the P-33.1 example table; "
            "the intervening example rows remain part of P-33.1"
        ),
    },
    "P-33.2": {
        "source_line_ids": ["p0341:l032", "p0341:l051"],
        "source_kind": "section_heading",
        "reason": (
            "The P-33.2 printed label and the FUNCTIONAL SUFFIXES title are "
            "noncontiguous around the preceding section's example table"
        ),
    },
}

KNOWN_PDF_TEXT_OMISSIONS = {
    "P-25.4.3.4.2": {
        "after_line": "p0247:l007",
        "before_line": "p0247:l008",
        "reason": (
            "Normative names, preferred-name status, explanation, and a cross-reference "
            "present in the corrected official HTML are absent from the printed PDF "
            "between the labelled structures and criterion (e)"
        ),
    }
}

KNOWN_CHAPTER_MASTHEAD_REASSIGNMENTS = [
    ("P-16.9.6", "p0138:l020", 139, "P-2"),
    ("P-29.6.3", "p0313:l035", 314, "P-3"),
    ("P-35.5.1", "p0354:l063", 355, "P-4"),
    ("P-46.3.2", "p0426:l014", 427, "P-5"),
    ("P-59.2.5", "p0493:l014", 494, "P-6"),
    ("P-65.7.7.3", "p0636:l008", 637, "P-6"),
    ("P-69.5.3", "p0783:l015", 784, "P-7"),
    ("P-77.3.2", "p0843:l005", 844, "P-8"),
    ("P-84", "p0862:l018", 863, "P-9"),
    ("P-94.3.2.6", "p0953:l001", 954, "P-10"),
]

# These duplicate name attributes are present in the corrected official HTML.
# Preserve them as source defects so consumers do not mistake an invented anchor
# for source evidence.
KNOWN_HTML_ANCHOR_COLLISIONS = {
    "https://iupac.qmul.ac.uk/BlueBook/P2.html#25040501": [
        "P-25.4.5.1",
        "P-25.4.5.2",
    ],
    "https://iupac.qmul.ac.uk/BlueBook/P6a.html#6801010303": [
        "P-68.1.1.3.3",
        "P-68.1.1.3.4",
    ],
}

CP1252_CONTROL_MAP = {
    code: bytes([code]).decode("cp1252")
    for code in range(0x80, 0xA0)
    if code not in {0x81, 0x8D, 0x8F, 0x90, 0x9D}
}


@dataclass(frozen=True)
class SourceLine:
    uid: str
    page: int
    line: int
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    starts_rule_heading: bool
    rule_id: str | None
    starts_chapter: bool
    chapter_id: str | None
    runs: list[dict[str, Any]]


@dataclass(frozen=True)
class HtmlRuleSection:
    rule_id: str
    chapter: str
    url: str
    anchor: str
    text: str
    fragment_sha256: str
    references: list[str]
    image_urls: list[str]
    table_count: int


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def clean_text(value: str) -> str:
    value = value.replace("\r", "\n")
    value = value.translate(CP1252_CONTROL_MAP).replace("\u00a0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", clean_text(value)).strip()


def region_for_page(page: int) -> tuple[str, str]:
    for region_id, kind, start, end in DOCUMENT_REGIONS:
        if start <= page <= end:
            return region_id, kind
    raise ValueError(f"Page {page} is outside the classified document regions")


def _heading_metadata(line: dict[str, Any]) -> tuple[bool, str | None]:
    match = RULE_AT_LINE_START_RE.match(line["text"])
    if not match or float(line["x0"]) >= 100:
        return False, None
    rule_id = match.group("rule_id")
    chars = [char for char in line.get("chars", []) if not char["text"].isspace()]
    id_chars = [char for char in rule_id if not char.isspace()]
    if len(chars) < len(id_chars):
        return False, None
    fonts = {str(char.get("fontname", "")) for char in chars[: len(id_chars)]}
    if not fonts or not all("bold" in font.lower() for font in fonts):
        return False, None
    return True, rule_id


def _chapter_metadata(line: dict[str, Any]) -> tuple[bool, str | None]:
    match = CHAPTER_RE.match(str(line["text"]))
    if not match:
        return False, None
    probe = match.group(0)
    chars = [char for char in line.get("chars", []) if not char["text"].isspace()]
    probe_chars = [char for char in probe if not char.isspace()]
    if len(chars) < len(probe_chars):
        return False, None
    fonts = {str(char.get("fontname", "")) for char in chars[: len(probe_chars)]}
    if not fonts or not all("bold" in font.lower() for font in fonts):
        return False, None
    return True, match.group(1).upper()


def style_runs(chars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for char in chars:
        key = (
            str(char.get("fontname", "")),
            round(float(char.get("size", 0)), 4),
            round(float(char.get("top", 0)), 4),
            round(float(char.get("bottom", 0)), 4),
        )
        if runs and runs[-1]["style_key"] == key:
            runs[-1]["text"] += str(char["text"])
            runs[-1]["x1"] = round(float(char["x1"]), 4)
            continue
        fontname, size, top, bottom = key
        runs.append(
            {
                "style_key": key,
                "text": str(char["text"]),
                "fontname": fontname,
                "size": size,
                "x0": round(float(char["x0"]), 4),
                "top": top,
                "x1": round(float(char["x1"]), 4),
                "bottom": bottom,
                "bold": "bold" in fontname.lower(),
                "italic": "italic" in fontname.lower(),
            }
        )
    for run in runs:
        run.pop("style_key")
    return runs


def binary_value(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("latin-1", errors="replace")
    return bytes(value)


def pdf_image_object_id(object_id: int, generation: int = 0) -> str:
    return f"pdf-image-object:{object_id:08d}:{generation:05d}"


def canonical_pdf_value(value: Any) -> Any:
    if isinstance(value, PDFObjRef):
        return {
            "kind": "object_reference",
            "object_id": int(value.objid),
            "generation": int(getattr(value, "genno", None) or 0),
        }
    if isinstance(value, PSLiteral):
        return {"kind": "name", "value": literal_name(value)}
    if isinstance(value, bytes):
        return {
            "kind": "bytes",
            "byte_count": len(value),
            "sha256": sha256_bytes(value),
        }
    if isinstance(value, dict):
        return {
            str(key): canonical_pdf_value(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [canonical_pdf_value(child) for child in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"Unsupported PDF dictionary value: {type(value).__name__}")


def object_reference(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, PDFObjRef):
        return None
    return int(value.objid), int(getattr(value, "genno", None) or 0)


def image_placements(page: Any, page_number: int) -> list[dict[str, Any]]:
    placements = []
    for index, image in enumerate(page.images, start=1):
        stream = image.get("stream")
        object_id = getattr(stream, "objid", None)
        generation = int(getattr(stream, "genno", None) or 0)
        if object_id is None:
            raise ValueError(f"Image placement lacks an indirect object on page {page_number}")
        placements.append(
            {
                "placement_id": f"p{page_number:04d}:image:{index:03d}",
                "page": page_number,
                "object_id": int(object_id),
                "generation": generation,
                "image_object_id": pdf_image_object_id(int(object_id), generation),
                "resource_name": str(image.get("name", "")),
                "bbox": [
                    round(float(image.get("x0", 0)), 4),
                    round(float(image.get("top", 0)), 4),
                    round(float(image.get("x1", 0)), 4),
                    round(float(image.get("bottom", 0)), 4),
                ],
                "source_size": [int(value) for value in image.get("srcsize", (0, 0))],
                "mcid": image.get("mcid"),
                "tag": image.get("tag"),
            }
        )
    return placements


def extract_pdf_image_objects(
    pdf: Any,
    images_by_page: dict[int, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    object_ids = sorted(
        {
            int(object_id)
            for xref in pdf.doc.xrefs
            for object_id in xref.get_objids()
        }
    )
    streams: dict[tuple[int, int], PDFStream] = {}
    for object_id in object_ids:
        value = pdf.doc.getobj(object_id)
        if not isinstance(value, PDFStream):
            continue
        subtype = value.attrs.get("Subtype")
        if not isinstance(subtype, PSLiteral) or literal_name(subtype) != "Image":
            continue
        generation = int(getattr(value, "genno", None) or 0)
        streams[(int(object_id), generation)] = value

    primary = {
        (int(image["object_id"]), int(image["generation"]))
        for placements in images_by_page.values()
        for image in placements
    }
    soft_masks = {
        reference
        for stream in streams.values()
        if (reference := object_reference(stream.attrs.get("SMask"))) is not None
    }
    explicit_masks = {
        reference
        for stream in streams.values()
        if (reference := object_reference(stream.attrs.get("Mask"))) is not None
    }
    referenced = primary | soft_masks | explicit_masks
    unknown_references = referenced.difference(streams)
    orphan_objects = set(streams).difference(referenced)
    if unknown_references or orphan_objects:
        raise ValueError(
            "Image object closure is incomplete: "
            f"unknown={sorted(unknown_references)}, orphan={sorted(orphan_objects)}"
        )

    objects = []
    for key, stream in sorted(streams.items()):
        object_id, generation = key
        raw = binary_value(stream.get_rawdata())
        if not raw:
            raise ValueError(f"Image object {object_id} has no encoded source bytes")
        dictionary = canonical_pdf_value(stream.attrs)
        decoded = binary_value(stream.get_data())
        if not decoded:
            raise ValueError(f"Image object {object_id} has no decoded payload")
        roles = []
        if key in primary:
            roles.append("primary")
        if key in soft_masks:
            roles.append("soft_mask")
        if key in explicit_masks:
            roles.append("explicit_mask")
        dependencies = []
        for relation, attr in (("soft_mask", "SMask"), ("explicit_mask", "Mask")):
            target = object_reference(stream.attrs.get(attr))
            if target is not None:
                dependencies.append(
                    {
                        "relation": relation,
                        "target_image_object_id": pdf_image_object_id(*target),
                    }
                )
        objects.append(
            {
                "image_object_id": pdf_image_object_id(object_id, generation),
                "object_id": object_id,
                "generation": generation,
                "roles": roles,
                "dictionary": dictionary,
                "raw_sha256": sha256_bytes(raw),
                "raw_byte_count": len(raw),
                "decoded_sha256": sha256_bytes(decoded),
                "decoded_byte_count": len(decoded),
                "dependencies": dependencies,
            }
        )

    page_object_pairs = {
        (image["page"], image["object_id"], image["generation"])
        for placements in images_by_page.values()
        for image in placements
    }
    decoded_payloads = {item["decoded_sha256"] for item in objects}
    return objects, {
        "placement_count": sum(len(items) for items in images_by_page.values()),
        "page_primary_object_count": len(page_object_pairs),
        "primary_object_count": len(primary),
        "soft_mask_object_count": len(soft_masks),
        "explicit_mask_object_count": len(explicit_masks),
        "image_object_count": len(objects),
        "unique_decoded_payload_count": len(decoded_payloads),
    }


def extract_pdf_lines(
    pdf_path: Path,
) -> tuple[
    list[SourceLine],
    dict[str, Any],
    dict[int, list[dict[str, Any]]],
    list[dict[str, Any]],
    dict[str, int],
]:
    lines: list[SourceLine] = []
    metadata: dict[str, Any] = {}
    images_by_page: dict[int, list[dict[str, Any]]] = {}
    image_objects: list[dict[str, Any]] = []
    image_metrics: dict[str, int] = {}
    with pdfplumber.open(str(pdf_path)) as pdf:
        metadata = {str(key): str(value) for key, value in (pdf.metadata or {}).items()}
        for page_number, page in enumerate(pdf.pages, start=1):
            images_by_page[page_number] = image_placements(page, page_number)
            extracted = page.extract_text_lines(layout=False, strip=True, return_chars=True)
            for line_number, line in enumerate(extracted, start=1):
                text = clean_text(str(line["text"]))
                if not text:
                    continue
                is_heading, rule_id = _heading_metadata(line)
                starts_chapter, chapter_id = _chapter_metadata(line)
                lines.append(
                    SourceLine(
                        uid=f"p{page_number:04d}:l{line_number:03d}",
                        page=page_number,
                        line=line_number,
                        text=text,
                        x0=round(float(line["x0"]), 4),
                        top=round(float(line["top"]), 4),
                        x1=round(float(line["x1"]), 4),
                        bottom=round(float(line["bottom"]), 4),
                        starts_rule_heading=is_heading and 48 <= page_number <= 1066,
                        rule_id=rule_id if is_heading and 48 <= page_number <= 1066 else None,
                        starts_chapter=starts_chapter and 48 <= page_number <= 1066,
                        chapter_id=chapter_id if starts_chapter and 48 <= page_number <= 1066 else None,
                        runs=style_runs(line.get("chars", [])),
                    )
                )
        image_objects, image_metrics = extract_pdf_image_objects(pdf, images_by_page)
    return lines, metadata, images_by_page, image_objects, image_metrics


def fetch_source(url: str, cache_path: Path, offline: bool) -> bytes:
    if cache_path.exists():
        return cache_path.read_bytes()
    if offline:
        raise FileNotFoundError(f"Missing cached source in offline mode: {cache_path}")
    request = urllib.request.Request(url, headers={"User-Agent": "Codex BlueBook source converter"})
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(raw)
    return raw


def remove_html_comments(raw: bytes) -> tuple[bytes, Any]:
    document = lxml_html.fromstring(raw.decode("utf-8", errors="replace"))
    for comment in document.xpath("//comment()"):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
    serialized = lxml_html.tostring(document, encoding="utf-8")
    return serialized, document


def fragment_text(fragment: bytes) -> tuple[str, list[str], list[str], int]:
    fragment_source = fragment.decode("utf-8", errors="replace")
    try:
        node = lxml_html.fragment_fromstring(fragment_source, create_parent="div")
    except (ValueError, TypeError):
        node = lxml_html.fromstring("<div>" + fragment_source + "</div>")
    text = clean_text(html_lib.unescape(node.text_content()))
    references = sorted(set(RULE_REF_RE.findall(text)))
    images = []
    for image in node.xpath(".//img[@src]"):
        src = str(image.get("src"))
        images.append(urllib.request.urljoin(SOURCE_ROOT, src))
    return text, references, sorted(set(images)), len(node.xpath(".//table"))


def extract_html_rules(
    chapter: str,
    filename: str,
    raw: bytes,
) -> tuple[list[HtmlRuleSection], dict[str, Any]]:
    url = SOURCE_ROOT + filename
    serialized, _ = remove_html_comments(raw)
    serialized_text = serialized.decode("utf-8", errors="replace")
    matches = list(HTML_RULE_ANCHOR_RE.finditer(serialized_text))
    sections: list[HtmlRuleSection] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(serialized_text)
        fragment = serialized_text[start:end].encode("utf-8")
        text, references, images, table_count = fragment_text(fragment)
        rule_id = match.group("rule_id")
        text = re.sub(rf"^{re.escape(rule_id)}\s*", "", text, count=1, flags=re.I)
        sections.append(
            HtmlRuleSection(
                rule_id=rule_id,
                chapter=chapter,
                url=url,
                anchor=match.group("anchor"),
                text=text,
                fragment_sha256=sha256_bytes(fragment),
                references=[reference for reference in references if reference != rule_id],
                image_urls=images,
                table_count=table_count,
            )
        )
    return sections, {
        "chapter": chapter,
        "url": url,
        "sha256": sha256_bytes(raw),
        "byte_count": len(raw),
        "active_rule_anchor_count": len(sections),
    }


def chapter_boundaries(lines: list[SourceLine]) -> list[tuple[int, str]]:
    first_index_by_page: dict[int, int] = {}
    for index, line in enumerate(lines):
        first_index_by_page.setdefault(line.page, index)
    return [
        (first_index_by_page[line.page], line.chapter_id or "")
        for line in lines
        if line.starts_chapter
    ]


def chapter_for_line(index: int, boundaries: list[tuple[int, str]]) -> str:
    chapter = ""
    for boundary_index, chapter_id in boundaries:
        if boundary_index > index:
            break
        chapter = chapter_id
    return chapter


def _source_text(section_lines: Iterable[SourceLine], rule_id: str) -> str:
    values = [line.text for line in section_lines]
    if values:
        values[0] = re.sub(
            rf"^{re.escape(rule_id)}[.;:]?\s*", "", values[0], count=1, flags=re.I
        )
    return clean_text("\n".join(values))


def full_source_spans(
    source_line_ids: list[str],
    lines_by_id: dict[str, SourceLine],
) -> list[dict[str, Any]]:
    return [
        {
            "line_id": line_id,
            "text_start": 0,
            "text_end": len(lines_by_id[line_id].text),
            "role": "heading" if index == 0 else "body",
        }
        for index, line_id in enumerate(source_line_ids)
    ]


def source_line_ids_from_spans(spans: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(str(span["line_id"]) for span in spans))


def source_text_from_spans(
    spans: list[dict[str, Any]],
    lines_by_id: dict[str, SourceLine],
    printed_rule_id: str,
    strip_printed_heading: bool,
) -> str:
    values = [
        lines_by_id[span["line_id"]].text[span["text_start"] : span["text_end"]]
        for span in spans
    ]
    if values and strip_printed_heading:
        values[0] = re.sub(
            rf"^{re.escape(printed_rule_id)}[.;:]?\s*",
            "",
            values[0],
            count=1,
            flags=re.I,
        )
    return clean_text("\n".join(values))


def extract_pdf_rule_sections(lines: list[SourceLine]) -> tuple[list[dict[str, Any]], set[str]]:
    lines_by_id = {line.uid: line for line in lines}
    body_indices = [index for index, line in enumerate(lines) if 48 <= line.page <= 1066]
    if not body_indices:
        raise ValueError("No normative chapter lines were extracted")
    body_start, body_end = body_indices[0], body_indices[-1] + 1
    headings = [index for index in body_indices if lines[index].starts_rule_heading]
    boundaries = chapter_boundaries(lines)
    chapter_starts = [index for index, _ in boundaries]
    records: list[dict[str, Any]] = []
    owned: set[str] = set()
    for position, start in enumerate(headings):
        next_heading = headings[position + 1] if position + 1 < len(headings) else body_end
        next_chapter = min((item for item in chapter_starts if start < item < next_heading), default=next_heading)
        end = min(next_heading, next_chapter)
        section_lines = lines[start:end]
        rule_id = lines[start].rule_id
        if not rule_id:
            raise ValueError(f"Heading line lacks a rule id: {lines[start].uid}")
        line_ids = [line.uid for line in section_lines]
        overlap = owned.intersection(line_ids)
        if overlap:
            raise ValueError(f"PDF source lines assigned more than once: {sorted(overlap)[:5]}")
        owned.update(line_ids)
        pages = sorted({line.page for line in section_lines})
        text = _source_text(section_lines, rule_id)
        records.append(
            {
                "record_id": f"bluebook-v3:{rule_id}",
                "source_rule_id": rule_id,
                "chapter": chapter_for_line(start, boundaries),
                "source_kind": "rule_section",
                "pdf": {
                    "printed_rule_id": rule_id,
                    "pages": pages,
                    "start_line": lines[start].uid,
                    "end_line": section_lines[-1].uid,
                    "source_line_ids": line_ids,
                    "source_spans": full_source_spans(line_ids, lines_by_id),
                    "text": text,
                    "text_sha256": sha256_text(text),
                },
            }
        )
    return records, owned


def apply_pdf_layout_reorderings(
    records: list[dict[str, Any]],
    lines_by_id: dict[str, SourceLine],
) -> None:
    records_by_id = {record["source_rule_id"]: record for record in records}
    for rule_id, repair in KNOWN_PDF_LAYOUT_REORDERINGS.items():
        record = records_by_id[rule_id]
        source_line_ids = repair["source_line_ids"]
        source_lines = [lines_by_id[line_id] for line_id in source_line_ids]
        source_spans = full_source_spans(source_line_ids, lines_by_id)
        if rule_id == "P-33.2":
            source_spans[0]["role"] = "heading_label"
            source_spans[1]["role"] = "heading_title"
        record["source_kind"] = repair.get("source_kind", record["source_kind"])
        record["pdf"].update(
            {
                "pages": sorted({line.page for line in source_lines}),
                "start_line": source_line_ids[0],
                "end_line": source_line_ids[-1],
                "source_line_ids": source_line_ids,
                "source_spans": source_spans,
                "text": source_text_from_spans(
                    source_spans, lines_by_id, rule_id, strip_printed_heading=True
                ),
            }
        )
        record["pdf"]["text_sha256"] = sha256_text(record["pdf"]["text"])
        record["_alignment_override"] = {
            "kind": "rule_id_exact_with_pdf_layout_reordering",
            "pdf_rule_id": rule_id,
            "html_rule_id": rule_id,
            "reason": repair["reason"],
        }


def non_rule_blocks(lines: list[SourceLine], owned: set[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: list[SourceLine] = []
    current_key: tuple[str, str, str] | None = None

    def flush() -> None:
        nonlocal current
        if not current or current_key is None:
            current = []
            return
        region_id, kind, chapter = current_key
        text = clean_text("\n".join(line.text for line in current))
        blocks.append(
            {
                "block_id": f"bluebook-v3:block:{len(blocks) + 1:04d}",
                "source_kind": kind,
                "region_id": region_id,
                "chapter": chapter or None,
                "pages": sorted({line.page for line in current}),
                "source_line_ids": [line.uid for line in current],
                "text": text,
                "text_sha256": sha256_text(text),
                "operative": kind in {"glossary", "table", "structure_registry"},
            }
        )
        current = []

    boundaries = chapter_boundaries(lines)
    for index, line in enumerate(lines):
        if line.uid in owned:
            flush()
            current_key = None
            continue
        region_id, region_kind = region_for_page(line.page)
        chapter = chapter_for_line(index, boundaries) if region_id == "chapters" else ""
        block_kind = "chapter_front_matter" if region_id == "chapters" else region_kind
        key = (region_id, block_kind, chapter)
        if current_key != key:
            flush()
            current_key = key
        current.append(line)
    flush()
    return blocks


def chapter_masthead_reassignments(
    records: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records_by_id = {record["source_rule_id"]: record for record in records}
    blocks_by_line = {
        line_id: block
        for block in blocks
        for line_id in block["source_line_ids"]
    }
    result: list[dict[str, Any]] = []
    for previous_rule_id, previous_end_line, page, chapter in KNOWN_CHAPTER_MASTHEAD_REASSIGNMENTS:
        source_line_ids = [f"p{page:04d}:l{line:03d}" for line in range(1, 7)]
        previous = records_by_id[previous_rule_id]
        if previous["pdf"]["end_line"] != previous_end_line:
            raise ValueError(
                f"Pinned terminal boundary changed for {previous_rule_id}: "
                f'{previous["pdf"]["end_line"]}'
            )
        owning_blocks = {blocks_by_line.get(line_id, {}).get("block_id") for line_id in source_line_ids}
        if len(owning_blocks) != 1 or None in owning_blocks:
            raise ValueError(
                f"Chapter masthead lines do not share one non-rule block: {source_line_ids}"
            )
        block_id = next(iter(owning_blocks))
        block = next(item for item in blocks if item["block_id"] == block_id)
        if block["source_kind"] != "chapter_front_matter" or block["chapter"] != chapter:
            raise ValueError(
                f"Chapter masthead assigned to the wrong block for page {page}: {block_id}"
            )
        result.append(
            {
                "previous_rule_id": previous_rule_id,
                "previous_rule_end_line": previous_end_line,
                "chapter": chapter,
                "page": page,
                "source_line_ids": source_line_ids,
                "block_id": block_id,
            }
        )
    return result


def verify_character_ownership(
    records: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    lines_by_id: dict[str, SourceLine],
) -> dict[str, int | bool]:
    ownership: dict[str, list[tuple[int, int, str]]] = {
        line_id: [] for line_id in lines_by_id
    }
    rule_characters = 0
    non_rule_characters = 0
    for record in records:
        for span in record["pdf"]["source_spans"]:
            line_id = span["line_id"]
            start = int(span["text_start"])
            end = int(span["text_end"])
            ownership[line_id].append((start, end, record["source_rule_id"]))
            rule_characters += end - start
    for block in blocks:
        for line_id in block["source_line_ids"]:
            end = len(lines_by_id[line_id].text)
            ownership[line_id].append((0, end, block["block_id"]))
            non_rule_characters += end

    for line_id, line in lines_by_id.items():
        intervals = sorted(ownership[line_id])
        cursor = 0
        for start, end, owner in intervals:
            if start != cursor or end <= start or end > len(line.text):
                raise ValueError(
                    f"Invalid character ownership at {line_id}: "
                    f"cursor={cursor}, span=({start}, {end}, {owner})"
                )
            cursor = end
        if cursor != len(line.text):
            raise ValueError(
                f"Incomplete character ownership at {line_id}: {cursor}/{len(line.text)}"
            )
    source_characters = sum(len(line.text) for line in lines_by_id.values())
    if rule_characters + non_rule_characters != source_characters:
        raise ValueError("Character ownership counters do not sum to the source")
    return {
        "source_character_count": source_characters,
        "owned_rule_source_character_count": rule_characters,
        "owned_non_rule_source_character_count": non_rule_characters,
        "character_ownership_complete": True,
    }


def merge_sources(
    pdf_records: list[dict[str, Any]],
    html_records: list[HtmlRuleSection],
    lines_by_id: dict[str, SourceLine],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pdf_by_id = {record["source_rule_id"]: record for record in pdf_records}
    html_by_id = {record.rule_id: record for record in html_records}
    if len(pdf_by_id) != len(pdf_records):
        raise ValueError("PDF rule ids are not unique after typography-aware extraction")
    if len(html_by_id) != len(html_records):
        raise ValueError("HTML rule ids are not unique after comment removal")

    pdf_only = sorted(set(pdf_by_id).difference(html_by_id))
    html_only = sorted(set(html_by_id).difference(pdf_by_id))
    unexpected_html_only = sorted(set(html_only).difference(KNOWN_STRUCTURAL_ALIGNMENTS))
    if pdf_only or unexpected_html_only:
        raise ValueError(
            f"Unreconciled rule ids: pdf_only={pdf_only}, html_only={unexpected_html_only}"
        )

    restored_by_printed = {
        value["pdf_printed_label"]: rule_id for rule_id, value in KNOWN_STRUCTURAL_ALIGNMENTS.items()
    }
    merged: list[dict[str, Any]] = []
    for rule_id, html_record in html_by_id.items():
        if rule_id in pdf_by_id:
            record = json.loads(json.dumps(pdf_by_id[rule_id], ensure_ascii=False))
            if rule_id in restored_by_printed:
                restored_rule_id = restored_by_printed[rule_id]
                known = KNOWN_STRUCTURAL_ALIGNMENTS[restored_rule_id]
                parent_line_count = int(known["parent_full_source_line_count"])
                parent_line_ids = record["pdf"]["source_line_ids"][:parent_line_count]
                splice_line_id = str(known["splice_line_id"])
                splice_line = lines_by_id[splice_line_id]
                if splice_line.text != known["splice_text"]:
                    raise ValueError(
                        f"Pinned printed-text splice changed at {splice_line_id}: "
                        f"{splice_line.text!r}"
                    )
                split_index = int(known["splice_text_start_for_child"])
                parent_spans = full_source_spans(parent_line_ids, lines_by_id)
                parent_spans.append(
                    {
                        "line_id": splice_line_id,
                        "text_start": 0,
                        "text_end": split_index,
                        "role": "printed_text_splice_parent",
                    }
                )
                parent_line_ids = source_line_ids_from_spans(parent_spans)
                parent_text = source_text_from_spans(
                    parent_spans, lines_by_id, rule_id, strip_printed_heading=True
                )
                record["pdf"].update(
                    {
                        "pages": sorted({lines_by_id[line_id].page for line_id in parent_line_ids}),
                        "start_line": parent_line_ids[0],
                        "end_line": parent_line_ids[-1],
                        "source_line_ids": parent_line_ids,
                        "source_spans": parent_spans,
                        "text": parent_text,
                        "text_sha256": sha256_text(parent_text),
                    }
                )
                alignment = {
                    "kind": "rule_id_exact_with_pdf_text_omission",
                    "pdf_rule_id": rule_id,
                    "html_rule_id": rule_id,
                    "reason": known["reason"],
                    "defect": {
                        "kind": "printed_text_splice",
                        "line_id": splice_line_id,
                        "split_index": split_index,
                    },
                }
            elif rule_id in KNOWN_PDF_TEXT_OMISSIONS:
                known = KNOWN_PDF_TEXT_OMISSIONS[rule_id]
                alignment = {
                    "kind": "rule_id_exact_with_pdf_text_omission",
                    "pdf_rule_id": rule_id,
                    "html_rule_id": rule_id,
                    "reason": known["reason"],
                    "defect": {
                        "kind": "source_gap_restoration",
                        "after_line": known["after_line"],
                        "before_line": known["before_line"],
                        "authoritative_source": "official_corrected_html",
                    },
                }
            else:
                alignment = record.pop(
                    "_alignment_override",
                    {
                        "kind": "rule_id_exact",
                        "pdf_rule_id": rule_id,
                        "html_rule_id": rule_id,
                    },
                )
        else:
            known = KNOWN_STRUCTURAL_ALIGNMENTS[rule_id]
            printed = known["pdf_printed_label"]
            parent = pdf_by_id[printed]
            parent_line_count = int(known["parent_full_source_line_count"])
            splice_line_id = str(known["splice_line_id"])
            split_index = int(known["splice_text_start_for_child"])
            original_line_ids = parent["pdf"]["source_line_ids"]
            splice_index = original_line_ids.index(splice_line_id)
            child_spans = [
                {
                    "line_id": splice_line_id,
                    "text_start": split_index,
                    "text_end": len(lines_by_id[splice_line_id].text),
                    "role": "printed_text_splice_child",
                },
                *[
                    {
                        "line_id": line_id,
                        "text_start": 0,
                        "text_end": len(lines_by_id[line_id].text),
                        "role": "body",
                    }
                    for line_id in original_line_ids[splice_index + 1 :]
                ],
            ]
            child_line_ids = source_line_ids_from_spans(child_spans)
            child_text = source_text_from_spans(
                child_spans, lines_by_id, printed, strip_printed_heading=False
            )
            record = {
                "record_id": f"bluebook-v3:{rule_id}",
                "source_rule_id": rule_id,
                "chapter": html_record.chapter.rstrip("ab"),
                "source_kind": "rule_section",
                "pdf": {
                    "printed_rule_id": printed,
                    "pages": sorted({lines_by_id[line_id].page for line_id in child_line_ids}),
                    "start_line": child_line_ids[0],
                    "end_line": parent["pdf"]["end_line"],
                    "source_line_ids": child_line_ids,
                    "source_spans": child_spans,
                    "text": child_text,
                    "text_sha256": sha256_text(child_text),
                },
            }
            alignment = {
                "kind": "structural_heading_restored",
                "pdf_rule_id": printed,
                "html_rule_id": rule_id,
                "reason": known["reason"],
            }
        record["html"] = {
            "url": html_record.url,
            "anchor": html_record.anchor,
            "text": html_record.text,
            "text_sha256": sha256_text(html_record.text),
            "fragment_sha256": html_record.fragment_sha256,
            "references": html_record.references,
            "image_urls": html_record.image_urls,
            "table_count": html_record.table_count,
        }
        record["alignment"] = alignment
        merged.append(record)

    order = {record["source_rule_id"]: index for index, record in enumerate(pdf_records)}
    restored_after = {"P-65.1.2.1": order["P-65.1.2"] + 0.5}

    def source_order(record: dict[str, Any]) -> float:
        rule_id = record["source_rule_id"]
        if rule_id in order:
            return float(order[rule_id])
        return float(restored_after[rule_id])

    merged.sort(key=source_order)
    anchors: dict[str, list[str]] = {}
    for record in merged:
        href = f'{record["html"]["url"]}#{record["html"]["anchor"]}'
        anchors.setdefault(href, []).append(record["source_rule_id"])
    duplicate_anchor_groups = {
        href: sorted(rule_ids)
        for href, rule_ids in sorted(anchors.items())
        if len(rule_ids) > 1
    }
    if duplicate_anchor_groups != KNOWN_HTML_ANCHOR_COLLISIONS:
        raise ValueError(
            "Official HTML anchor collisions differ from the audited source: "
            f"{duplicate_anchor_groups}"
        )
    audited_source_anomalies = [
        {
            "rule_id": record["source_rule_id"],
            "kind": record["alignment"]["kind"],
            "reason": record["alignment"]["reason"],
            **(
                {"defect": record["alignment"]["defect"]}
                if "defect" in record["alignment"]
                else {}
            ),
        }
        for record in merged
        if record["alignment"]["kind"] != "rule_id_exact"
    ]
    return merged, {
        "pdf_rule_count": len(pdf_records),
        "html_rule_count": len(html_records),
        "merged_rule_count": len(merged),
        "pdf_only_rule_ids": pdf_only,
        "html_only_structurally_aligned_rule_ids": html_only,
        "source_html_anchor_collisions": [
            {"href": href, "rule_ids": rule_ids}
            for href, rule_ids in duplicate_anchor_groups.items()
        ],
        "audited_source_anomalies": audited_source_anomalies,
    }


def page_payload(
    lines: list[SourceLine],
    page_count: int,
    images_by_page: dict[int, list[dict[str, Any]]],
    image_objects: list[dict[str, Any]],
    image_metrics: dict[str, int],
) -> dict[str, Any]:
    grouped: dict[int, list[SourceLine]] = {page: [] for page in range(1, page_count + 1)}
    for line in lines:
        grouped[line.page].append(line)
    return {
        "source_pdf": PDF_URL,
        "page_count": page_count,
        "image_metrics": image_metrics,
        "image_objects": image_objects,
        "pages": [
            {
                "page": page,
                "region_id": region_for_page(page)[0],
                "source_kind": region_for_page(page)[1],
                "text": clean_text("\n".join(line.text for line in grouped[page])),
                "lines": [asdict(line) for line in grouped[page]],
                "images": images_by_page[page],
            }
            for page in range(1, page_count + 1)
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the lossless Blue Book V3 source corpus")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--offline", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    pdf_bytes = pdf_path.read_bytes()
    lines, pdf_metadata, images_by_page, image_objects, image_metrics = extract_pdf_lines(
        pdf_path
    )
    page_count = max(line.page for line in lines)
    if page_count != 1149:
        raise ValueError(f"Expected 1149 PDF pages, found {page_count}")

    html_rules: list[HtmlRuleSection] = []
    html_manifest = []
    auxiliary_html = []
    for chapter, filename in HTML_SOURCES.items():
        url = SOURCE_ROOT + filename
        raw = fetch_source(url, args.cache_dir / filename, args.offline)
        if chapter.startswith("P-"):
            sections, manifest = extract_html_rules(chapter, filename, raw)
            html_rules.extend(sections)
            html_manifest.append(manifest)
        else:
            auxiliary_html.append(
                {
                    "source_kind": chapter,
                    "url": url,
                    "sha256": sha256_bytes(raw),
                    "byte_count": len(raw),
                }
            )

    pdf_records, owned = extract_pdf_rule_sections(lines)
    apply_pdf_layout_reorderings(pdf_records, {line.uid: line for line in lines})
    blocks = non_rule_blocks(lines, owned)
    merged_records, reconciliation = merge_sources(
        pdf_records, html_rules, {line.uid: line for line in lines}
    )
    reconciliation["chapter_masthead_reassignments"] = chapter_masthead_reassignments(
        merged_records,
        blocks,
    )
    character_metrics = verify_character_ownership(
        merged_records,
        blocks,
        {line.uid: line for line in lines},
    )

    all_line_ids = {line.uid for line in lines}
    block_line_ids = {line_id for block in blocks for line_id in block["source_line_ids"]}
    if owned.intersection(block_line_ids):
        raise ValueError("Rule and non-rule source ownership overlaps")
    if owned.union(block_line_ids) != all_line_ids:
        missing = sorted(all_line_ids.difference(owned, block_line_ids))
        raise ValueError(f"Unowned PDF source lines: {missing[:10]}")

    pages = page_payload(
        lines,
        page_count,
        images_by_page,
        image_objects,
        image_metrics,
    )
    source_corpus = {
        "format_version": "2.0.0",
        "conversion_stage": "lossless_source",
        "source_document": {
            "title": "Nomenclature of Organic Chemistry. IUPAC Recommendations and Preferred Names 2013",
            "source_pdf_url": PDF_URL,
            "local_pdf_sha256": sha256_bytes(pdf_bytes),
            "local_pdf_byte_count": len(pdf_bytes),
            "page_count": page_count,
            "pdf_metadata": pdf_metadata,
            "toolchain": {
                "python": sys.version.split()[0],
                "pdfplumber": pdfplumber.__version__,
                "lxml": lxml.__version__,
            },
            "document_regions": [
                {
                    "region_id": region_id,
                    "source_kind": kind,
                    "page_start": start,
                    "page_end": end,
                }
                for region_id, kind, start, end in DOCUMENT_REGIONS
            ],
            "html_sources": html_manifest,
            "auxiliary_html_sources": auxiliary_html,
        },
        "reconciliation": reconciliation,
        "rule_record_count": len(merged_records),
        "non_rule_block_count": len(blocks),
        "source_line_count": len(lines),
        "owned_rule_source_line_count": len(owned),
        "owned_non_rule_source_line_count": len(block_line_ids),
        "source_character_count": character_metrics["source_character_count"],
        "owned_rule_source_character_count": character_metrics[
            "owned_rule_source_character_count"
        ],
        "owned_non_rule_source_character_count": character_metrics[
            "owned_non_rule_source_character_count"
        ],
        "pdf_image_placement_count": image_metrics["placement_count"],
        "pdf_page_primary_image_object_count": image_metrics["page_primary_object_count"],
        "pdf_primary_image_object_count": image_metrics["primary_object_count"],
        "pdf_soft_mask_image_object_count": image_metrics["soft_mask_object_count"],
        "pdf_explicit_mask_image_object_count": image_metrics["explicit_mask_object_count"],
        "pdf_image_object_count": image_metrics["image_object_count"],
        "pdf_unique_decoded_image_payload_count": image_metrics[
            "unique_decoded_payload_count"
        ],
        "records": merged_records,
        "non_rule_blocks": blocks,
    }
    write_json(args.out_dir / "bluebook_v3_source_pages.json", pages)
    write_json(args.out_dir / "bluebook_v3_source_corpus.json", source_corpus)

    report = {
        "pdf_sha256": source_corpus["source_document"]["local_pdf_sha256"],
        "page_count": page_count,
        "source_line_count": len(lines),
        "rule_record_count": len(merged_records),
        "pdf_rule_heading_count": reconciliation["pdf_rule_count"],
        "html_active_rule_anchor_count": reconciliation["html_rule_count"],
        "structurally_restored_rule_count": len(
            reconciliation["html_only_structurally_aligned_rule_ids"]
        ),
        "non_rule_block_count": len(blocks),
        "pdf_image_placement_count": image_metrics["placement_count"],
        "pdf_page_primary_image_object_count": image_metrics["page_primary_object_count"],
        "pdf_primary_image_object_count": image_metrics["primary_object_count"],
        "pdf_soft_mask_image_object_count": image_metrics["soft_mask_object_count"],
        "pdf_explicit_mask_image_object_count": image_metrics["explicit_mask_object_count"],
        "pdf_image_object_count": image_metrics["image_object_count"],
        "pdf_unique_decoded_image_payload_count": image_metrics[
            "unique_decoded_payload_count"
        ],
        "html_rule_image_reference_count": sum(
            len(record.image_urls) for record in html_rules
        ),
        "line_ownership_complete": owned.union(block_line_ids) == all_line_ids,
        "character_ownership_complete": character_metrics[
            "character_ownership_complete"
        ],
        "duplicate_pdf_rule_id_count": sum(
            count > 1 for count in Counter(record["source_rule_id"] for record in pdf_records).values()
        ),
        "duplicate_html_rule_id_count": sum(
            count > 1 for count in Counter(record.rule_id for record in html_rules).values()
        ),
        "source_html_anchor_collision_count": len(
            reconciliation["source_html_anchor_collisions"]
        ),
        "pdf_only_rule_ids": reconciliation["pdf_only_rule_ids"],
        "html_only_structurally_aligned_rule_ids": reconciliation[
            "html_only_structurally_aligned_rule_ids"
        ],
    }
    write_json(args.out_dir / "bluebook_v3_source_extraction_report.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
