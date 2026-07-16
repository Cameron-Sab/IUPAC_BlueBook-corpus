# IUPAC Blue Book Machine-Readable Corpus

This repository converts the IUPAC Blue Book into a provenance-preserving,
machine-readable rule system before using it in a naming engine.

The current release checkpoint completes the lossless source layer. It does not
claim that the semantic rule IR or a universal naming engine is complete.

## Source Authority

- Canonical PDF: https://iupac.qmul.ac.uk/BlueBook/PDF/BlueBookV3.pdf
- Section-addressable HTML: https://iupac.qmul.ac.uk/BlueBook/
- Post-Version-3 corrections: https://iupac.qmul.ac.uk/BlueBook/changes2.html

The local source snapshot was retrieved on 2026-07-15 and is effective through
the latest encoded official correction dated 2026-01-22. The PDF is not stored
in Git. Exact source URLs, byte lengths, and SHA-256 digests are recorded in the
generated artifacts and `data/source_manifest.json`.

## Validated Source Corpus

The source release gate currently verifies:

| Artifact | Coverage |
|---|---:|
| PDF source pages | 1,149 |
| PDF source lines | 39,773 |
| Active rule records | 2,554 |
| Lossless document nodes | 14,453 |
| Tables / rows / cells | 567 / 3,782 / 9,100 |
| Image occurrences | 5,371 |
| Correction records / operations | 90 / 108 |
| Atomic clause units | 32,408 |
| Field-source ownership records | 38,256 |
| Cross-reference occurrences | 4,023 |
| Explicit exceptional resolutions | 3 |
| Resolved dependency edges | 3,587 |
| Remaining unresolved reference targets | 0 |

Important generated files are under `data/bluebook_v3/`:

- `bluebook_v3_source_corpus.json` and `bluebook_v3_source_pages.json`
- `bluebook_v3_document_nodes/` (11 GitHub-safe shards plus manifest)
- `bluebook_v3_correction_overlays.json`
- `bluebook_v3_clause_inventory.json`
- `bluebook_v3_reference_occurrences.json`
- `bluebook_v3_reference_resolutions.json`
- `bluebook_v3_reference_dependency_graph.json`
- `bluebook_v3_validation_report.json`

The document-node shard store reconstructs the canonical 240,001,951-byte
monolith exactly, including its SHA-256 digest, without committing a file over
GitHub's 100 MB limit.

## Semantic Conversion

`data/normalized_rule_language.schema.json` defines the final rule IR.
`scripts/build_semantic_work_packets.py` deterministically partitions all 2,554
records and 32,408 clauses into 151 immutable work packets. Each packet carries
the source record, document fragment, clause inventory, correction overlays,
reference occurrences, explicit resolutions, neighboring context, and six
source-artifact hashes.

A semantic chunk is accepted only if:

- every assigned clause has exactly one compiled, nonoperative, or superseded
  disposition;
- every operative clause reaches typed semantic objects;
- references and object identifiers resolve uniquely;
- exception order and dependency projections are deterministic;
- packet, schema, source, metrics, and content hashes reproduce;
- no review marker, placeholder, unresolved state, or generic fallback action
  occurs anywhere in the chunk.

The final semantic corpus is intentionally absent until every packet passes.
See `docs/NORMALIZED_RULE_LANGUAGE.md` and
`work/SEMANTIC_IR_CONVERSION_GUIDE.md`.

## Reproduce And Validate

```powershell
python scripts\fetch_official_sources.py --offline-verify
python scripts\document_node_store.py verify
python scripts\build_reference_dependency_graph.py `
  --out data\bluebook_v3\bluebook_v3_reference_dependency_graph.json
python scripts\build_semantic_work_packets.py
python scripts\validate_pdf_rebuild.py --stage source
python -m pytest
```

The full source gate is intentionally expensive: it replays extraction and
provenance instead of trusting generated counts.

## Prototype Engine

The earlier `iupac_engine/` and `scripts/example_test_engine.py` remain as
separate prototype scaffolding. They are not the authority for this conversion
and should not be used to infer semantic-corpus completeness.
