# Deterministic SMILES-to-IUPAC Naming Engine Prototype

This repo now separates the work into two layers:

1. A machine-readable Blue Book rule corpus.
2. A later naming engine that consumes that corpus.

The corpus work is the important first step. The converter reads the official
IUPAC Blue Book HTML chapters and turns the rule sections into JSON records with
`if`, `unless`, `then`, `prefer`, `must_not`, and `definitions` buckets.

Source authority:

- https://iupac.qmul.ac.uk/BlueBook/
- "Nomenclature of Organic Chemistry. IUPAC Recommendations and Preferred Names
  2013", online version 3, posted 2023-12-06.

## Generate The Rule Corpus

```powershell
python scripts\bluebook_to_rules.py --out data\bluebook_rules.json
```

Current generated corpus:

- `data/bluebook_rules.json`: 1,744 extracted rule sections.
- `data/rule_schema.json`: JSON schema for the extracted corpus.
- `BLUEBOOK_ALGORITHM.md`: conversion strategy.
- `data/bluebook_semantic_rules.json`: 1,829 draft semantic rule records,
  including post-V3 corrections.
- `data/bluebook_rule_dependency_graph.json`: dependency graph for
  cross-referenced rules.
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

- loads `data/bluebook_semantic_rules.json`;
- loads `data/bluebook_rule_dependency_graph.json`;
- queries rules by id, chapter, type, and text;
- walks outgoing or incoming dependency edges;
- activates candidate rules from simple fact tokens.

What it does not do yet:

- convert a molecular graph into Blue Book facts;
- execute chemistry-specific predicates;
- render final IUPAC names from semantic actions.

## Supported Scope

The current prototype supports single-component, acyclic organic molecules using:

- carbon parent chains;
- single, double, and triple bonds;
- branches;
- halogen prefixes: fluoro, chloro, bromo, iodo;
- simple alkyl substituent prefixes;
- one principal characteristic group among:
  - carboxylic acid;
  - aldehyde;
  - ketone;
  - alcohol;
  - amine;
  - hydrocarbon.

Unsupported structures return structured `unsupported` responses rather than guessed names.

## Quick Start

```powershell
cd outputs\smiles_iupac_engine
python -m iupac_engine "CC(C)O" --explain
python -m iupac_engine "CC(=O)O" --json --explain
```

## Examples

```text
CCO        -> ethan-1-ol
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
