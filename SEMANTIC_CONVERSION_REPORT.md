# Blue Book Semantic Conversion Report

Run date: 2026-07-15

## Status

The full extracted Blue Book corpus plus post-V3 corrections has been converted
into draft semantic rule records.

This is not yet an executable nomenclature engine. It is a machine-readable rule
corpus intended to be compiled into graph predicates, ranking functions,
rendering operations, and validators.

## Source Coverage

- Blue Book rule sections: 1,744
- Post-V3 correction records: 85
- Total semantic records: 1,829

Chapter coverage:

- P-1: 191
- P-2: 269
- P-3: 74
- P-4: 116
- P-5: 107
- P-6a: 223
- P-6b: 275
- P-7: 127
- P-8: 42
- P-9: 131
- P-10: 189
- Post-V3-Corrections: 85

## Semantic Content

- Dependency references: 4,764
- Dependency graph edges: 4,835
- Ordered comparison criteria: 767
- Exception entries: 581
- Extracted example references: 9,663
- Unresolved semantic notes: 2,294
- Missing required fields: 0

## Primary Artifacts

- `data/bluebook_semantic_rules.json`: complete semantic rule corpus
- `data/bluebook_rule_dependency_graph.json`: dependency graph as JSON
- `data/bluebook_rule_dependency_graph.dot`: Graphviz dependency graph
- `data/bluebook_rule_dependency_graph.mmd`: Mermaid dependency graph
- `data/semantic_rules/*.json`: per-chapter semantic rule files
- `data/semantic_rule_schema.json`: semantic rule schema

## Important Caveats

- Some graph edges point to rule prefixes, ranges, or contextual references
  rather than exact rule-section nodes. These are preserved as dependency edges
  and marked by the graph builder when the exact target is not present.
- `unresolved_semantics` marks places where the Blue Book prose depends on
  tables, figures, chemical judgment, or future graph predicate implementation.
- The corpus is ready for the next phase: compiling semantic records into
  executable predicates/actions and attaching conformance tests.
