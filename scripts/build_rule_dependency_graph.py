from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NORMALIZED = ROOT / "data" / "bluebook_semantic_rules.normalized.json"
SEMANTIC = ROOT / "data" / "bluebook_semantic_rules.json"
OUT_JSON = ROOT / "data" / "bluebook_rule_dependency_graph.json"
OUT_DOT = ROOT / "data" / "bluebook_rule_dependency_graph.dot"
OUT_MMD = ROOT / "data" / "bluebook_rule_dependency_graph.mmd"


REF_RE = re.compile(r"\bP-\d+(?:\.\d+)*\b")


def main() -> int:
    semantic_path = NORMALIZED if NORMALIZED.exists() else SEMANTIC
    payload = json.loads(semantic_path.read_text(encoding="utf-8-sig"))
    records = payload.get("records", [])
    rule_ids = [record["rule_id"] for record in records]
    rule_id_set = set(rule_ids)

    nodes = []
    edges = []
    edge_keys = set()
    for record in records:
        source = record.get("normalized_id", record["rule_id"])
        rule_id = record["rule_id"]
        nodes.append(
            {
                "id": source,
                "rule_id": rule_id,
                "rule_instance": record.get("rule_instance", 1),
                "title": record.get("title", ""),
                "chapter": record.get("source_chapter", ""),
                "rule_type": record.get("rule_type", "other"),
                "implementation_status": record.get("implementation_status", payload.get("conversion_status", "")),
            }
        )

        dependency_context = record.get("dependency_context", {})
        if dependency_context:
            add_edges(edges, edge_keys, source, rule_id, dependency_context.get("declared", []), "declared_dependency", rule_id_set)
            add_edges(edges, edge_keys, source, rule_id, dependency_context.get("explicit_references", []), "explicit_reference", rule_id_set)
            add_edges(edges, edge_keys, source, rule_id, dependency_context.get("hierarchy", []), "hierarchical_parent", rule_id_set)
            add_edges(edges, edge_keys, source, rule_id, dependency_context.get("exception_references", []), "exception_reference", rule_id_set)
            add_edges(edges, edge_keys, source, rule_id, dependency_context.get("example_references", []), "example_reference", rule_id_set)
            add_edges(edges, edge_keys, source, rule_id, dependency_context.get("source_table_references", []), "source_table_requirement", rule_id_set)
        else:
            dependencies = set(record.get("depends_on", []))
            for field in ("source_quote", "title"):
                dependencies.update(REF_RE.findall(str(record.get(field, ""))))
            for field in ("applies_if", "unless", "then", "prefer", "reject"):
                for item in record.get(field, []):
                    dependencies.update(REF_RE.findall(str(item)))
            for exception in record.get("exceptions", []):
                dependencies.update(REF_RE.findall(str(exception.get("condition", ""))))
                dependencies.update(REF_RE.findall(str(exception.get("effect", ""))))
            add_edges(edges, edge_keys, source, rule_id, dependencies, "depends_on", rule_id_set)

        for requirement in record.get("implementation_requirements", []):
            target = requirement.get("kind", "semantic_compilation_requirement")
            add_edges(edges, edge_keys, source, rule_id, [target], "implementation_requirement", rule_id_set)

    graph = {
        "source": payload.get("source"),
        "built_from": str(semantic_path.relative_to(ROOT)),
        "source_version": payload.get("source_version"),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "missing_target_count": sum(1 for edge in edges if not edge["target_in_corpus"]),
        "relation_counts": Counter(edge["relation"] for edge in edges),
        "nodes": nodes,
        "edges": edges,
        "chapter_edge_counts": Counter(node["chapter"] for node in nodes),
    }
    OUT_JSON.write_text(json.dumps(graph, indent=2, ensure_ascii=False, default=dict), encoding="utf-8")
    OUT_DOT.write_text(to_dot(nodes, edges), encoding="utf-8")
    OUT_MMD.write_text(to_mermaid(edges), encoding="utf-8")
    print(f"Wrote graph with {len(nodes)} nodes and {len(edges)} edges")
    return 0


def add_edges(
    edges: list[dict[str, object]],
    edge_keys: set[tuple[str, str, str]],
    source: str,
    source_rule_id: str,
    targets: object,
    relation: str,
    rule_id_set: set[str],
) -> None:
    if isinstance(targets, str):
        targets = [targets]
    for target in sorted({str(target) for target in targets if str(target).strip()}):
        if target == source_rule_id or target == source:
            continue
        key = (source, target, relation)
        if key in edge_keys:
            continue
        edge_keys.add(key)
        target_is_rule = bool(REF_RE.fullmatch(target))
        edges.append(
            {
                "source": source,
                "source_rule_id": source_rule_id,
                "target": target,
                "relation": relation,
                "target_kind": "rule" if target_is_rule else "requirement_or_table",
                "target_in_corpus": target in rule_id_set if target_is_rule else False,
            }
        )


def to_dot(nodes: list[dict[str, object]], edges: list[dict[str, object]]) -> str:
    lines = ["digraph BlueBookRules {", "  rankdir=LR;"]
    for node in nodes:
        node_id = dot_id(str(node["id"]))
        label = str(node["id"]).replace('"', '\\"')
        lines.append(f'  {node_id} [label="{label}"];')
    for edge in edges:
        style = "solid" if edge["target_in_corpus"] else "dashed"
        lines.append(f"  {dot_id(edge['source'])} -> {dot_id(edge['target'])} [style={style}];")
    lines.append("}")
    return "\n".join(lines) + "\n"


def to_mermaid(edges: list[dict[str, object]]) -> str:
    lines = ["flowchart LR"]
    for edge in edges[:2000]:
        source = mermaid_id(str(edge["source"]))
        target = mermaid_id(str(edge["target"]))
        lines.append(f'  {source}["{edge["source"]}"] --> {target}["{edge["target"]}"]')
    if len(edges) > 2000:
        lines.append(f"  %% truncated: {len(edges) - 2000} additional edges omitted")
    return "\n".join(lines) + "\n"


def dot_id(value: object) -> str:
    return "r_" + re.sub(r"[^A-Za-z0-9_]", "_", str(value))


def mermaid_id(value: str) -> str:
    return "r_" + re.sub(r"[^A-Za-z0-9_]", "_", value)


if __name__ == "__main__":
    raise SystemExit(main())
