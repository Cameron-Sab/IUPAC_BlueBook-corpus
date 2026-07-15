# Blue Book Algorithm Conversion

Source authority:

- IUPAC Blue Book online release: https://iupac.qmul.ac.uk/BlueBook/
- Release used here: "Nomenclature of Organic Chemistry. IUPAC Recommendations
  and Preferred Names 2013", online version 3, posted 2023-12-06.

This project treats the Blue Book as an executable rule system. The first
deliverable is not the naming engine; it is the machine-readable rule corpus.
Each prose rule is converted into one or more records with:

- a rule id;
- a graph predicate;
- an action;
- a priority vector;
- implementation status;
- conformance examples.

## Corpus Conversion Algorithm

```text
official Blue Book HTML chapter
  -> text extraction
  -> P-rule heading detection
  -> section body capture
  -> sentence/clause segmentation
  -> condition/exception/preference/requirement detection
  -> predicate/action stub generation
  -> JSON rule corpus
```

The converter is `scripts/bluebook_to_rules.py`. It emits
`data/bluebook_rules.json` using the schema in `data/rule_schema.json`.

## Later Runtime Algorithm

```text
SMILES
  -> normalized molecular graph
  -> feature perception
  -> candidate name operation generation
  -> parent candidate generation
  -> numbering candidate generation
  -> suffix/prefix candidate generation
  -> Blue Book rule evaluation in priority order
  -> syntax tree rendering
  -> verification
```

## Rule Dispatch

Rules are not stored as comments. They are executable metadata in
`iupac_engine/rules.py`. The engine response includes the rule coverage table so
the caller can see exactly which Blue Book rules are implemented, partial, or
still pending.

## Current Encoded Rule Families

- P-10: route carbon-containing structures into organic nomenclature.
- P-11: element-domain check for the organic nomenclature domain.
- P-13.1: substitutive nomenclature operation.
- P-14: locant ranking as a lexicographic comparison problem.
- P-21.2.1: acyclic alkane parent hydride roots.
- P-31.1.3.1/P-31.1.3.2: simple ene/yne rendering.
- P-33: characteristic group suffix/prefix ranking, partial.
- P-44: parent selection, partial.
- P-45: detachable prefix rendering, partial.

## Next Mechanical Conversion Steps

1. Add a scraper for the official HTML chapters and extract every `P-...`
   heading into a rule manifest.
2. Convert each manifest entry into predicate/action skeletons.
3. Encode every table as data first: parent hydrides, suffixes, prefixes,
   retained names, seniority orders, element roots, locant priorities.
4. Encode prose exceptions as explicit guard predicates.
5. Add every Blue Book example as a conformance fixture.
6. Only mark a rule `implemented` when the fixture set passes.
