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
`scripts/build_compact_semantic_tasks.py` deterministically partitions all 2,554
records and 32,408 clauses into 151 source-bound tasks without copying full
source records, document fragments, and provenance into every task. The tasks
occupy 16,936,679 bytes; their token-efficient JSON Lines model views occupy
6,884,500 bytes, 97.95% less than the former 335,255,522-byte packet set. The
model view preserves ordered ancestor kinds so prose nested inside examples,
notes, figures, or other containers cannot lose its local semantic context.

Converters emit only a semantic decision delta. The compiler generates record
envelopes, hierarchy edges, citation targets, occurrence and resolution
evidence, source hashes, metrics, and content hashes. This keeps mechanical
provenance out of model output and rejects missing clauses, missing citations,
ambiguous targets, stale inputs, and altered deltas before assembly.

A semantic chunk is accepted only if:

- every assigned clause has exactly one compiled, nonoperative, or superseded
  disposition;
- every operative clause reaches typed semantic objects;
- references and object identifiers resolve uniquely;
- exception order and dependency projections are deterministic;
- task, delta, schema, source, metrics, and content hashes reproduce;
- no review marker, placeholder, unresolved state, or generic fallback action
  occurs anywhere in the chunk.

The final semantic corpus is intentionally absent until every packet passes.
See `docs/NORMALIZED_RULE_LANGUAGE.md` and
`work/SEMANTIC_IR_CONVERSION_GUIDE.md`.

## Executable Rule ABI

`scripts/compile_executable_rule_bundle.py` compiles normalized semantic IR into
a deterministic `ordered-if-then-v1` JSON bundle. The bundle contains typed
entrypoints, `when`/`then`/`else` programs, decisions, exceptions, tables,
dependency edges, and an explicit host capability contract. A partial bundle
requires `--allow-partial` and cannot be labeled complete.

`iupac_rule_runtime/` is a safe reference interpreter for that ABI. It uses a
closed opcode set, never evaluates code from JSON, verifies the bundle hash,
and reports every missing or incorrectly typed chemistry operation before
execution. See `docs/EXECUTABLE_RULE_BUNDLE.md` and
`data/executable_rule_bundle.schema.json`.

```powershell
python scripts\compile_executable_rule_bundle.py `
  data\bluebook_v3\bluebook_v3_rule_ir.json
```

## Reproduce And Validate

```powershell
python scripts\fetch_official_sources.py --offline-verify
python scripts\document_node_store.py verify
python scripts\build_reference_dependency_graph.py `
  --out data\bluebook_v3\bluebook_v3_reference_dependency_graph.json
python scripts\build_compact_semantic_tasks.py
python scripts\build_compact_semantic_tasks.py --check
python scripts\audit_semantic_delta_progress.py
python scripts\validate_pdf_rebuild.py --stage source
python -m pytest
```

The full source gate is intentionally expensive: it replays extraction and
provenance instead of trusting generated counts.

The delta progress auditor returns success for valid partial progress, but
`--require-complete` succeeds only when all 151 deltas compile and pass. Invalid
or extra deltas always fail. Legacy manual chunks are not counted.

### Token-Efficient Authoring

Semantic converters no longer need to emit the verbose normalized AST directly.
`scripts/scaffold_semantic_authoring.py` locks mechanically certain clause and
citation decisions, `scripts/render_semantic_authoring_task.py` shows only the
remaining evidence, and `scripts/compile_semantic_authoring.py` expands compact
prefix expressions and statements into the strict delta format.

The sparse views remove 40.69% of the original compact fleet input, while
representative compact authoring is 71.6% smaller than its expanded delta. All generated IDs,
provenance, compiled targets, citations, reason symbols, metrics, and hashes are
local deterministic work. See `docs/SEMANTIC_AUTHORING.md`.

## Local Nomenclature Benchmark

`scripts/benchmark_chebi_iupac_names.py` joins the ChEBI compounds, names, and
structures downloads without copying those datasets into this repository. It
selects active three-star compounds and active English `IUPAC NAME` rows,
verifies structures independently with RDKit, and compares each name to its
structure with OPSIN. Detailed cases and reports are written under the ignored
`work/benchmarks/` directory.

```powershell
python scripts\benchmark_chebi_iupac_names.py
```

This is an independent name-to-structure consistency benchmark, not proof that
an unfinished semantic conversion or naming engine is complete. Exact-name,
connectivity, stereochemistry/protonation, parser failure, and unscorable
dataset outcomes remain separate.

### Preferred-Name Conformance

`scripts/build_bluebook_pin_benchmark.py` extracts names explicitly designated
`(PIN)` in the provenance-preserving Blue Book node store, parses each name to
a structure with OPSIN, canonicalizes it with RDKit, and assigns stable
calibration, holdout, and final-holdout splits. The engine benchmark keeps exact
PIN matches separate from structurally equivalent nonpreferred names, wrong
structures, parser failures, and unsupported scope.

```powershell
python scripts\build_bluebook_pin_benchmark.py
python scripts\benchmark_pin_engine.py --split calibration
```

The generated cases and reports stay under `work/benchmarks/`. Because the
structure is independently reconstructed from the authoritative name rather
than digitized from every printed structure image, these cases are PIN
conformance oracles, not an image-to-structure audit.

### Local Semantic Supervisor

The local compaction harness supports Ollama and llama.cpp's OpenAI-compatible
server, strict JSON Schema output, exact clause/reference coverage, immutable
hashes, held-out semantic scoring, best-attempt retention, and packet-local
quarantine. On Windows, the pinned installer verifies the official llama.cpp
archives before extraction. The supervisor starts a baseline decoder and runs
the remaining compact tasks in prompt-size order with persistent state.

```powershell
powershell -File scripts\install_llama_cpp_windows.ps1
powershell -File scripts\start_local_semantic_supervisor.ps1 -ReplaceServer
```

Runtime state and logs are written to `work/local_semantic_supervisor/`.
Creating `work/local_semantic_supervisor/state.stop` requests a clean stop after
the current packet. Speculative modes are available through
`scripts/start_local_semantic_server.ps1`, but the supervisor defaults to the
ordinary decoder because speculative output must first beat the same semantic
and integrity gates before it is accepted.

## Prototype Engine

The earlier `iupac_engine/` and `scripts/example_test_engine.py` remain as
separate prototype scaffolding. They are not the authority for this conversion
and should not be used to infer semantic-corpus completeness.
