from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path


SOURCE_ROOT = "https://iupac.qmul.ac.uk/BlueBook/"
SOURCE_VERSION = "IUPAC Blue Book 2013 online version 3, posted 2023-12-06"

CHAPTERS = {
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
    "Appendix-1": "Papp1.html",
    "Appendix-2": "Papp2.html",
    "Appendix-3": "Papp3.html",
    "References": "refs.html",
    "Post-V3-Corrections": "changes2.html",
}

RULE_HEADING_RE = re.compile(
    r"^(?P<id>P-\d+(?:\.\d+)*(?:\([a-z0-9]+\))?)\s+(?P<title>[A-Z][^\n]{0,220})$"
)
CORRECTION_HEADING_RE = re.compile(
    r"^(?P<target>.+?)\.\s*\[(?P<kind>corrected|modified|changed|added)[^\d\]]*(?P<date>\d{1,2}(?:\s+[A-Za-z]+\s+|\.\d{1,2}\.)\d{4})\]",
    re.I,
)

CLAUSE_PATTERNS = (
    ("condition", re.compile(r"\b(if|when|where|provided that|in the case of|in cases where)\b", re.I)),
    ("exception", re.compile(r"\b(except|exception|however|unless|but not|not used when)\b", re.I)),
    ("preference", re.compile(r"\b(preference is given|preferred|preselected|retained|seniority|priority)\b", re.I)),
    ("prohibition", re.compile(r"\b(must not|shall not|not recommended|not used|is not)\b", re.I)),
    ("requirement", re.compile(r"\b(must|shall|is used|are used|is indicated|are indicated)\b", re.I)),
    ("definition", re.compile(r"\b(is called|is defined|means|denotes|refers to|consists of)\b", re.I)),
)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style"}:
            self._skip_depth += 1
        if tag.lower() in {"p", "br", "div", "tr", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
        if tag.lower() in {"p", "div", "tr", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


@dataclass
class LogicClause:
    clause_type: str
    source_text: str
    predicate_stub: str | None = None
    action_stub: str | None = None


@dataclass
class RuleSection:
    rule_id: str
    title: str
    chapter: str
    source_url: str
    source_version: str
    body: str
    logic_clauses: list[LogicClause] = field(default_factory=list)
    logical_form: dict[str, list[str]] = field(default_factory=dict)
    references: list[str] = field(default_factory=list)
    implementation_status: str = "uncompiled_prose"


@dataclass
class CorrectionRecord:
    target: str
    correction_type: str
    correction_date: str
    correction_dates: list[str]
    source_url: str
    change_text: str
    logical_form: dict[str, list[str]]


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Codex BlueBook rule converter"})
    with urllib.request.urlopen(request, timeout=60) as response:
        html = response.read().decode("utf-8", errors="replace")
    parser = TextExtractor()
    parser.feed(html)
    return parser.text()


def split_sections(chapter: str, url: str, text: str) -> list[RuleSection]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    starts: list[tuple[int, str, str]] = []
    for idx, line in enumerate(lines):
        match = RULE_HEADING_RE.match(line)
        if match:
            starts.append((idx, match.group("id"), clean_title(match.group("title"))))

    sections: list[RuleSection] = []
    for pos, (start_idx, rule_id, title) in enumerate(starts):
        end_idx = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        body_lines = lines[start_idx + 1 : end_idx]
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        logic_clauses = extract_logic_clauses(body)
        sections.append(
            RuleSection(
                rule_id=rule_id,
                title=title,
                chapter=chapter,
                source_url=url,
                source_version=SOURCE_VERSION,
                body=body,
                logic_clauses=logic_clauses,
                logical_form=build_logical_form(logic_clauses),
                references=sorted(set(re.findall(r"\bP-\d+(?:\.\d+)*\b", body))),
            )
        )
    return sections


def clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip(" .")


def extract_logic_clauses(body: str) -> list[LogicClause]:
    sentences = split_sentences(body)
    clauses: list[LogicClause] = []
    for sentence in sentences:
        for clause_type, pattern in CLAUSE_PATTERNS:
            if pattern.search(sentence):
                clauses.append(
                    LogicClause(
                        clause_type=clause_type,
                        source_text=sentence,
                        predicate_stub=predicate_stub(sentence) if clause_type in {"condition", "exception"} else None,
                        action_stub=action_stub(sentence) if clause_type in {"preference", "requirement", "prohibition"} else None,
                    )
                )
                break
    return clauses


def build_logical_form(clauses: list[LogicClause]) -> dict[str, list[str]]:
    form = {
        "if": [],
        "unless": [],
        "then": [],
        "prefer": [],
        "must_not": [],
        "definitions": [],
    }
    for clause in clauses:
        if clause.clause_type == "condition":
            form["if"].append(clause.source_text)
        elif clause.clause_type == "exception":
            form["unless"].append(clause.source_text)
        elif clause.clause_type == "preference":
            form["prefer"].append(clause.source_text)
        elif clause.clause_type == "prohibition":
            form["must_not"].append(clause.source_text)
        elif clause.clause_type == "definition":
            form["definitions"].append(clause.source_text)
        elif clause.clause_type == "requirement":
            form["then"].append(clause.source_text)
    return form


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text)
    rough = re.split(r"(?<=[.!?])\s+(?=(?:[A-Z]|\(|\[|‘|“))", text)
    return [s.strip() for s in rough if len(s.strip()) > 20]


def predicate_stub(sentence: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", sentence.lower()).strip("_")
    return f"predicate__{normalized[:80]}"


def action_stub(sentence: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", sentence.lower()).strip("_")
    return f"action__{normalized[:80]}"


def convert() -> list[RuleSection]:
    sections: list[RuleSection] = []
    for chapter, filename in CHAPTERS.items():
        url = SOURCE_ROOT + filename
        print(f"Fetching {chapter} {url}", file=sys.stderr)
        text = fetch_text(url)
        sections.extend(split_sections(chapter, url, text))
    return sections


def extract_corrections(url: str, text: str) -> list[CorrectionRecord]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    starts: list[tuple[int, re.Match[str]]] = []
    for idx, line in enumerate(lines):
        match = CORRECTION_HEADING_RE.match(line)
        if match:
            starts.append((idx, match))

    corrections: list[CorrectionRecord] = []
    for pos, (start_idx, match) in enumerate(starts):
        end_idx = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        change_text = "\n".join(lines[start_idx : end_idx]).strip()
        clauses = extract_logic_clauses(change_text)
        corrections.append(
            CorrectionRecord(
                target=re.sub(r"\s+", " ", match.group("target")).strip(),
                correction_type=match.group("kind").lower(),
                correction_date=match.group("date"),
                correction_dates=extract_dates(change_text),
                source_url=url,
                change_text=change_text,
                logical_form=build_logical_form(clauses),
            )
        )
    return corrections


def extract_dates(text: str) -> list[str]:
    return re.findall(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{1,2}\.\d{1,2}\.\d{4}", text)


def convert_with_corrections() -> tuple[list[RuleSection], list[CorrectionRecord]]:
    sections: list[RuleSection] = []
    corrections: list[CorrectionRecord] = []
    for chapter, filename in CHAPTERS.items():
        url = SOURCE_ROOT + filename
        print(f"Fetching {chapter} {url}", file=sys.stderr)
        text = fetch_text(url)
        sections.extend(split_sections(chapter, url, text))
        if "Correction" in chapter:
            corrections.extend(extract_corrections(url, text))
    return sections, corrections


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert official IUPAC Blue Book HTML into machine-readable rule sections")
    parser.add_argument("--out", type=Path, default=Path("data/bluebook_rules.json"))
    args = parser.parse_args()

    sections, corrections = convert_with_corrections()
    payload = {
        "source": SOURCE_ROOT,
        "source_version": SOURCE_VERSION,
        "record_count": len(sections),
        "correction_count": len(corrections),
        "records": [asdict(section) for section in sections],
        "corrections": [asdict(correction) for correction in corrections],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(sections)} rule sections to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
