from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "bluebook_rules.json"
OUT = ROOT / "data" / "semantic_rules"
SOURCE_VERSION = "IUPAC Blue Book 2013 online version plus post-V3 web corrections"


def main() -> int:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)

    chapters = sorted({record["chapter"] for record in payload["records"]})
    for chapter in chapters:
        records = [to_semantic(record) for record in payload["records"] if record["chapter"] == chapter]
        write_chapter(chapter, records)

    corrections = [correction_to_semantic(correction) for correction in payload.get("corrections", [])]
    write_chapter("Post-V3-Corrections", corrections)
    print(f"Wrote draft semantic files for {len(chapters)} chapters plus corrections")
    return 0


def write_chapter(chapter: str, records: list[dict[str, object]]) -> None:
    (OUT / f"{chapter}.json").write_text(
        json.dumps(
            {
                "source": "https://iupac.qmul.ac.uk/BlueBook/",
                "source_version": SOURCE_VERSION,
                "chapter": chapter,
                "conversion_status": "draft_semantic",
                "records": records,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def to_semantic(record: dict[str, object]) -> dict[str, object]:
    body = str(record.get("body", ""))
    logical = record.get("logical_form", {})
    title = str(record.get("title", ""))
    rule_id = str(record.get("rule_id", ""))
    chapter = str(record.get("chapter", ""))
    return {
        "rule_id": rule_id,
        "title": title,
        "source_chapter": chapter,
        "source_url": str(record.get("source_url", "")),
        "rule_type": infer_rule_type(rule_id, title, body, chapter),
        "applies_if": normalize_predicates(logical.get("if", [])),
        "unless": normalize_predicates(logical.get("unless", [])),
        "then": normalize_actions(logical.get("then", [])),
        "prefer": normalize_actions(logical.get("prefer", [])),
        "reject": infer_rejections(body),
        "compare_by": infer_compare_by(body),
        "depends_on": sorted(set(record.get("references", []))),
        "exceptions": infer_exceptions(logical.get("unless", []), body),
        "examples": extract_examples(body),
        "tables_or_terms": extract_tables_or_terms(body),
        "unresolved_semantics": infer_unresolved(body),
        "source_quote": quote(body),
    }


def correction_to_semantic(correction: dict[str, object]) -> dict[str, object]:
    text = str(correction.get("change_text", ""))
    target = str(correction.get("target", ""))
    logical = correction.get("logical_form", {})
    return {
        "rule_id": f"CORRECTION::{target}",
        "title": f"{correction.get('correction_type', 'correction')} to {target}",
        "source_chapter": "Post-V3-Corrections",
        "source_url": str(correction.get("source_url", "")),
        "rule_type": "correction",
        "applies_if": [f"target_rule_is('{target}')"],
        "unless": normalize_predicates(logical.get("unless", [])),
        "then": normalize_actions(logical.get("then", [])) + correction_actions(text),
        "prefer": normalize_actions(logical.get("prefer", [])),
        "reject": infer_rejections(text),
        "compare_by": infer_compare_by(text),
        "depends_on": sorted(set(re.findall(r"\bP-\d+(?:\.\d+)*\b", text))),
        "exceptions": infer_exceptions(logical.get("unless", []), text),
        "examples": extract_examples(text),
        "tables_or_terms": extract_tables_or_terms(text),
        "unresolved_semantics": infer_unresolved(text),
        "source_quote": quote(text),
    }


def infer_rule_type(rule_id: str, title: str, body: str, chapter: str) -> str:
    text = f"{rule_id} {title} {body}".lower()
    if chapter == "P-9" or "stereo" in text or "configuration" in text:
        return "stereochemistry"
    if chapter == "P-8" or "isotop" in text:
        return "isotope"
    if "parent" in text and ("select" in text or "choice" in text or "seniority" in text):
        return "parent_selection"
    if "locant" in text or "number" in text or "numbering" in text:
        return "numbering"
    if "preferred" in text or "preference" in text or "seniority" in text:
        return "preference"
    if "suffix" in text or "prefix" in text:
        return "suffix_prefix"
    if "retained" in text:
        return "retained_name"
    if "punctuation" in text or "hyphen" in text or "italic" in text or "capital" in text:
        return "formatting"
    if "defined" in text or "definition" in text or "called" in text:
        return "definition"
    if chapter.startswith("P-6"):
        return "class_specific"
    if "operation" in text:
        return "operation"
    return "other"


def normalize_predicates(items: list[str]) -> list[str]:
    return [as_predicate(item) for item in items]


def normalize_actions(items: list[str]) -> list[str]:
    return [as_action(item) for item in items]


def as_predicate(text: str) -> str:
    return "predicate:" + compact(text)


def as_action(text: str) -> str:
    return "action:" + compact(text)


def compact(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def infer_rejections(body: str) -> list[str]:
    rejects = []
    for sentence in split_sentences(body):
        if re.search(r"\bnot\s+(?:used|recommended|allowed|permitted)|\bmust not\b|\bshall not\b", sentence, re.I):
            rejects.append(as_action(sentence))
    return rejects


def infer_compare_by(body: str) -> list[dict[str, object]]:
    criteria = []
    priority = 1
    for sentence in split_sentences(body):
        lower = sentence.lower()
        direction = None
        if "lowest" in lower or "lower" in lower or "minimum" in lower:
            direction = "lowest"
        elif "highest" in lower or "maximum" in lower or "largest" in lower:
            direction = "highest"
        elif "first point of difference" in lower:
            direction = "first_point_of_difference"
        elif "alphabet" in lower:
            direction = "alphabetical"
        elif "seniority" in lower or "priority" in lower:
            direction = "seniority_order"
        elif "order of citation" in lower or "order given" in lower:
            direction = "specified_order"
        if direction and re.search(r"\b(preference|preferred|seniority|criterion|criteria|choice|chosen|selected|priority|lowest|highest|maximum|minimum)\b", lower):
            criteria.append({"priority": priority, "criterion": compact(sentence), "direction": direction})
            priority += 1
    return criteria


def infer_exceptions(unless_items: list[str], body: str) -> list[dict[str, str]]:
    exceptions = [{"condition": as_predicate(item), "effect": "override_or_block_primary_rule"} for item in unless_items]
    for sentence in split_sentences(body):
        if re.search(r"\bexcept(?:ion)?\b|\bunless\b|\bhowever\b", sentence, re.I):
            exceptions.append({"condition": as_predicate(sentence), "effect": "apply_exception_behavior_described_in_source"})
    return exceptions


def correction_actions(text: str) -> list[str]:
    actions = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(("for ", "read ", "add ", "delete ", "replace ")):
            actions.append(as_action(stripped))
    return actions


def extract_examples(body: str) -> list[str]:
    examples = []
    for sentence in split_sentences(body):
        if "example" in sentence.lower() or "(pin" in sentence.lower() or "(not " in sentence.lower():
            examples.append(compact(sentence))
    return examples[:20]


def extract_tables_or_terms(body: str) -> list[str]:
    terms = []
    for line in body.splitlines():
        if re.search(r"\b(table|seniority|prefix|suffix|parent hydride|retained name)\b", line, re.I):
            terms.append(compact(line))
    return terms[:30]


def infer_unresolved(body: str) -> list[str]:
    unresolved = []
    if re.search(r"\bshown below\b|\bfollowing structure\b|\bsee (?:Table|Fig\.|Figure)\b", body, re.I):
        unresolved.append("Requires table/figure/structure extraction from source material.")
    if re.search(r"\bappropriate\b|\busual way\b|\bwhere necessary\b|\bas needed\b", body, re.I):
        unresolved.append("Contains judgment-dependent prose requiring manual predicate refinement.")
    if len(body) > 4000:
        unresolved.append("Large source section; may need decomposition into multiple executable subrules.")
    return unresolved


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=(?:[A-Z]|\(|\[|‘|“))", text) if len(s.strip()) > 20]


def quote(body: str) -> str:
    return compact(body)[:800]


if __name__ == "__main__":
    raise SystemExit(main())
