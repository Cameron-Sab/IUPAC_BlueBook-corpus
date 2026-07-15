from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = PROJECT_ROOT / "data" / "bluebook_semantic_rules.json"
DEFAULT_GRAPH_PATH = PROJECT_ROOT / "data" / "bluebook_rule_dependency_graph.json"


def normalize_token(value: str) -> str:
    value = value.lower()
    value = re.sub(r"^source_(?:condition|requirement|preference|prohibition)_", "", value)
    value = re.sub(r"^(?:predicate|action):", "", value)
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


@dataclass(frozen=True)
class SemanticRule:
    rule_id: str
    title: str
    source_chapter: str
    source_url: str
    rule_type: str
    applies_if: tuple[str, ...]
    unless: tuple[str, ...]
    then: tuple[str, ...]
    prefer: tuple[str, ...]
    reject: tuple[str, ...]
    compare_by: tuple[dict[str, Any], ...]
    depends_on: tuple[str, ...]
    exceptions: tuple[dict[str, str], ...]
    examples: tuple[str, ...]
    tables_or_terms: tuple[str, ...]
    unresolved_semantics: tuple[str, ...]
    source_quote: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SemanticRule":
        return cls(
            rule_id=str(payload["rule_id"]),
            title=str(payload["title"]),
            source_chapter=str(payload["source_chapter"]),
            source_url=str(payload["source_url"]),
            rule_type=str(payload["rule_type"]),
            applies_if=tuple(payload.get("applies_if", [])),
            unless=tuple(payload.get("unless", [])),
            then=tuple(payload.get("then", [])),
            prefer=tuple(payload.get("prefer", [])),
            reject=tuple(payload.get("reject", [])),
            compare_by=tuple(payload.get("compare_by", [])),
            depends_on=tuple(payload.get("depends_on", [])),
            exceptions=tuple(payload.get("exceptions", [])),
            examples=tuple(payload.get("examples", [])),
            tables_or_terms=tuple(payload.get("tables_or_terms", [])),
            unresolved_semantics=tuple(payload.get("unresolved_semantics", [])),
            source_quote=str(payload.get("source_quote", "")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "source_chapter": self.source_chapter,
            "source_url": self.source_url,
            "rule_type": self.rule_type,
            "applies_if": list(self.applies_if),
            "unless": list(self.unless),
            "then": list(self.then),
            "prefer": list(self.prefer),
            "reject": list(self.reject),
            "compare_by": list(self.compare_by),
            "depends_on": list(self.depends_on),
            "exceptions": list(self.exceptions),
            "examples": list(self.examples),
            "tables_or_terms": list(self.tables_or_terms),
            "unresolved_semantics": list(self.unresolved_semantics),
            "source_quote": self.source_quote,
        }


class BlueBookRuleEngine:
    def __init__(
        self,
        rules_path: Path | str = DEFAULT_RULES_PATH,
        graph_path: Path | str = DEFAULT_GRAPH_PATH,
    ) -> None:
        self.rules_path = Path(rules_path)
        self.graph_path = Path(graph_path)
        self.metadata: dict[str, Any] = {}
        self.rules: list[SemanticRule] = []
        self.by_id: dict[str, list[SemanticRule]] = {}
        self.graph: dict[str, Any] = {}
        self.outgoing: dict[str, list[dict[str, Any]]] = {}
        self.incoming: dict[str, list[dict[str, Any]]] = {}
        self.load()

    def load(self) -> None:
        payload = json.loads(self.rules_path.read_text(encoding="utf-8-sig"))
        self.metadata = {k: v for k, v in payload.items() if k != "records"}
        self.rules = [SemanticRule.from_dict(record) for record in payload["records"]]
        self.by_id = {}
        for rule in self.rules:
            self.by_id.setdefault(rule.rule_id, []).append(rule)

        if self.graph_path.exists():
            self.graph = json.loads(self.graph_path.read_text(encoding="utf-8-sig"))
            for edge in self.graph.get("edges", []):
                self.outgoing.setdefault(edge["source"], []).append(edge)
                self.incoming.setdefault(edge["target"], []).append(edge)

    def stats(self) -> dict[str, Any]:
        chapters: dict[str, int] = {}
        types: dict[str, int] = {}
        for rule in self.rules:
            chapters[rule.source_chapter] = chapters.get(rule.source_chapter, 0) + 1
            types[rule.rule_type] = types.get(rule.rule_type, 0) + 1
        return {
            "record_count": len(self.rules),
            "chapter_count": len(chapters),
            "chapters": dict(sorted(chapters.items())),
            "rule_types": dict(sorted(types.items())),
            "graph_nodes": self.graph.get("node_count", 0),
            "graph_edges": self.graph.get("edge_count", 0),
            "missing_graph_targets": self.graph.get("missing_target_count", 0),
        }

    def get(self, rule_id: str) -> list[SemanticRule]:
        return self.by_id.get(rule_id, [])

    def search(
        self,
        query: str | None = None,
        *,
        chapter: str | None = None,
        rule_type: str | None = None,
        limit: int = 20,
    ) -> list[SemanticRule]:
        query_norm = normalize_token(query or "")
        matches: list[SemanticRule] = []
        for rule in self.rules:
            if chapter and rule.source_chapter != chapter:
                continue
            if rule_type and rule.rule_type != rule_type:
                continue
            haystack = normalize_token(
                " ".join(
                    [
                        rule.rule_id,
                        rule.title,
                        rule.source_chapter,
                        rule.rule_type,
                        rule.source_quote,
                        *rule.applies_if,
                        *rule.then,
                        *rule.prefer,
                        *rule.depends_on,
                    ]
                )
            )
            if query_norm and query_norm not in haystack:
                continue
            matches.append(rule)
            if len(matches) >= limit:
                break
        return matches

    def dependencies(self, rule_id: str, *, depth: int = 1, reverse: bool = False) -> dict[str, Any]:
        seen: set[str] = set()
        edges: list[dict[str, Any]] = []

        def walk(current: str, remaining: int) -> None:
            if remaining < 1:
                return
            next_edges = self.incoming.get(current, []) if reverse else self.outgoing.get(current, [])
            for edge in next_edges:
                key = f"{edge['source']}->{edge['target']}"
                if key in seen:
                    continue
                seen.add(key)
                edges.append(edge)
                walk(edge["source"] if reverse else edge["target"], remaining - 1)

        walk(rule_id, depth)
        node_ids = sorted({rule_id, *(edge["source"] for edge in edges), *(edge["target"] for edge in edges)})
        return {
            "root": rule_id,
            "direction": "incoming" if reverse else "outgoing",
            "depth": depth,
            "nodes": node_ids,
            "edges": edges,
        }

    def evaluate(
        self,
        facts: Iterable[str],
        *,
        chapter: str | None = None,
        rule_type: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        fact_tokens = {normalize_token(fact) for fact in facts}
        activated: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []

        for rule in self.search(None, chapter=chapter, rule_type=rule_type, limit=len(self.rules)):
            positive = [_condition_match(condition, fact_tokens) for condition in rule.applies_if]
            negative = [_condition_match(condition, fact_tokens) for condition in rule.unless]
            positive_score = sum(1 for match in positive if match)
            negative_score = sum(1 for match in negative if match)

            if negative_score:
                blocked.append(
                    {
                        "rule_id": rule.rule_id,
                        "title": rule.title,
                        "blocked_by": [rule.unless[idx] for idx, match in enumerate(negative) if match],
                    }
                )
                continue

            if not rule.applies_if or positive_score:
                activated.append(
                    {
                        "rule_id": rule.rule_id,
                        "title": rule.title,
                        "source_chapter": rule.source_chapter,
                        "rule_type": rule.rule_type,
                        "matched_conditions": [
                            rule.applies_if[idx] for idx, match in enumerate(positive) if match
                        ],
                        "condition_score": positive_score,
                        "actions": list(rule.then),
                        "preferences": list(rule.prefer),
                        "compare_by": list(rule.compare_by),
                        "depends_on": list(rule.depends_on),
                    }
                )

        activated.sort(key=lambda item: (-item["condition_score"], item["source_chapter"], item["rule_id"]))
        return {
            "facts": sorted(fact_tokens),
            "activated_count": len(activated),
            "blocked_count": len(blocked),
            "activated": activated[:limit],
            "blocked": blocked[:limit],
        }


def _condition_match(condition: str, fact_tokens: set[str]) -> bool:
    condition_token = normalize_token(condition)
    if not condition_token:
        return False
    for fact in fact_tokens:
        if not fact:
            continue
        if fact == condition_token or fact in condition_token or condition_token in fact:
            return True
    return False
