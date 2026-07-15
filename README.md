# Deterministic SMILES-to-IUPAC Naming Engine Prototype

This repo now separates the work into two layers:

1. A machine-readable Blue Book rule corpus.
2. A later naming engine that consumes that corpus.

The corpus work is the important first step. The converter reads the official
IUPAC Blue Book HTML chapters and turns the rule sections into JSON records with
`if`, `unless`, `then`, `prefer`, `must_not`, and `definitions` buckets.

Source authority:

- Canonical PDF source: https://iupac.qmul.ac.uk/BlueBook/PDF/BlueBookV3.pdf
- Section-linked extraction source: https://iupac.qmul.ac.uk/BlueBook/
- Section-linked post-Version-3 correction provenance:
  https://iupac.qmul.ac.uk/BlueBook/changes2.html

The canonical source for the Blue Book Version 3 corpus is the PDF. The HTML
chapters are used as section-addressable extraction sources, and the correction
page is used to preserve post-Version-3 correction provenance. The canonical PDF
is not committed to this repository; source metadata is recorded in
`data/source_manifest.json`.

## Generate The Rule Corpus

```powershell
python scripts\bluebook_to_rules.py --out data\bluebook_rules.json
```

Current generated corpus:

- `data/bluebook_rules.json`: 1,744 extracted rule sections.
- `data/rule_schema.json`: JSON schema for the extracted corpus.
- `BLUEBOOK_ALGORITHM.md`: conversion strategy.
- `data/bluebook_semantic_rules.normalized.json`: 1,829 normalized semantic
  rule records, including post-V3 corrections.
- `data/bluebook_semantic_rules.json`: draft semantic corpus retained as
  provenance.
- `data/bluebook_rule_dependency_graph.json`: dependency graph for
  cross-referenced rules, hierarchy, exceptions, examples, source tables, and
  implementation requirements.
- `data/source_manifest.json`: canonical source and extraction provenance
  metadata.
- `SEMANTIC_CONVERSION_REPORT.md`: current conversion report.

Each record preserves the original prose in `body`, extracts cross-references,
and provides both raw `logic_clauses` and grouped `logical_form`.

Example shape:

```json
{
  "rule_id": "P-61.2.1",
  "title": "Acyclic hydrocarbons",
  "logical_form": {
    "if": [],
    "unless": [],
    "then": [],
    "prefer": [
      "Thus, the first criterion to be considered in choosing a preferred parent acyclic chain is the length of the chain; unsaturation is now the second criterion."
    ],
    "must_not": [],
    "definitions": []
  }
}
```

The old prototype naming engine remains in `iupac_engine/`, but it should be
treated as secondary scaffolding until the corpus is compiled into executable
graph predicates.

## Prototype Engine Notes

This is a scoped prototype inspired by the attached technical design specification.
It is not a universal IUPAC implementation. It demonstrates the requested
architecture: parse a SMILES string into a graph, detect supported structural
features, generate/rank parent-chain numbering candidates, render a systematic
name, and return an explanation trace.

## Basic Rule Engine

The first rule-set engine is available through `python -m iupac_engine`.

```powershell
python -m iupac_engine stats
python -m iupac_engine rule P-61.2.1
python -m iupac_engine search locant --chapter P-4 --limit 5
python -m iupac_engine deps P-61.2.1 --depth 1
python -m iupac_engine eval --fact parent --fact locant --limit 5
```

What this engine does now:

- loads `data/bluebook_semantic_rules.normalized.json` when present;
- loads `data/bluebook_rule_dependency_graph.json`;
- queries rules by id, chapter, type, and text;
- walks outgoing or incoming dependency edges;
- activates candidate rules from simple fact tokens.
- parses SMILES into canonical, input-order-independent RDKit molecular graphs;
- preserves ring, aromaticity, charge, isotope, radical, and stereochemical
  metadata for deterministic nomenclature phases.

Current boundaries:

- graph parsing is broad, but naming support remains deliberately fail-closed;
- many Blue Book predicates now have explicit implementation requirements;
- final full-scale IUPAC rendering from semantic actions is not complete.

## Supported Scope

The current prototype supports single-component organic molecules in these
implemented families:

- carbon parent chains;
- single, double, and triple bonds;
- branches;
- halogen prefixes: fluoro, chloro, bromo, iodo;
- simple alkyl substituent prefixes;
- attachment-aware branched alkyl and alkoxy substituent prefixes;
- Blue Book Table 1.4 numerical terms and parent roots;
- saturated monocyclic carbon parents with hydrocarbon, halo, alkyl, alkoxy, or
  simple methylidene substitution;
- one principal characteristic group among:
  - carboxylic acid;
  - ester;
  - acid halide;
  - primary amide;
  - nitrile;
  - aldehyde;
  - ketone;
  - alcohol;
  - amine;
  - hydrocarbon.

Neutral bracket atoms are accepted when their chemistry is otherwise supported.
Polycyclic, unsaturated-ring, heterocyclic, and aromatic systems, along with
formal charges, isotopic modification, radicals, stereochemical descriptors,
disconnected structures, and unsupported elements, currently return structured
`unsupported` responses rather than guessed names.

## Quick Start

```powershell
cd outputs\smiles_iupac_engine
python -m iupac_engine "CC(C)O" --explain
python -m iupac_engine "CC(=O)O" --json --explain
```

## Examples

```text
CCO        -> ethanol
CC(C)O     -> propan-2-ol
CCC(=O)C   -> butan-2-one
CC(=O)O    -> ethanoic acid
CC(C)Cl    -> 2-chloropropane
```

## Run Tests

```powershell
python -m pytest
```

The implementation is deliberately small, but the internal types are arranged so
new nomenclature modules can be added without turning the engine into one long
chain of ad hoc conditionals.

## Example Test Engine

Run the bundled smoke/conformance harness:

```powershell
python scripts\example_test_engine.py
python scripts\example_test_engine.py --markdown-out ..\iupac_prototype_test_audit.md
```
