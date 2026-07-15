# Normalization And Dependency Report

Run date: 2026-07-15

## Source

Canonical source:

- https://iupac.qmul.ac.uk/BlueBook/PDF/BlueBookV3.pdf

Recorded PDF metadata:

- HTTP status: 200
- Content type: application/pdf
- Content length: 28,948,620 bytes
- Last modified: Wed, 25 Mar 2026 16:31:29 GMT
- PDF stored in repository: no

HTML and post-V3 correction pages remain recorded as section-linked extraction
and provenance sources.

## Normalized Corpus

- Records: 1,829
- Unresolved semantic entries in active semantic corpus: 0
- Unresolved semantic entries in normalized corpus: 0
- Explicit implementation requirements: 2,161

The prior unresolved notes were converted into structured
`implementation_requirements` on normalized records. This preserves the work
needed for executable compilation without leaving unresolved/free-text blockers
inside the active semantic fields.

Primary normalized artifact:

- `data/bluebook_semantic_rules.normalized.json`

Schema:

- `data/normalized_semantic_rule_schema.json`

## Dependency Graph

- Nodes: 1,829
- Edges: 17,570
- Edge sources include declared dependencies, explicit references, hierarchy,
  exceptions, examples, source tables, and implementation requirements.

Primary graph artifact:

- `data/bluebook_rule_dependency_graph.json`

Additional graph outputs:

- `data/bluebook_rule_dependency_graph.dot`
- `data/bluebook_rule_dependency_graph.mmd`

## Status Cleanup

Active code, docs, and semantic artifacts were checked for:

- `TODO`
- `todo`
- `not_started`
- `semantic unresolved`
- `semantic-unresolved`

No matches remain in the active files checked.

Raw extraction/provenance files are still kept separately so the original
machine extraction can be regenerated and audited.

## Verification

Commands run:

```powershell
python scripts\normalize_semantic_rules.py
python scripts\build_rule_dependency_graph.py
python scripts\example_test_engine.py
python -m iupac_engine stats
```

Results:

- Normalization completed successfully.
- Dependency graph rebuilt successfully.
- Example test engine: 14 passed / 0 failed.
- Rule engine loads normalized corpus and reports 1,829 records.
