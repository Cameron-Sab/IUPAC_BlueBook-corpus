# ChEBI Benchmark Telemetry

This report summarizes local testing against the ChEBI 253 three-star IUPAC-name case set.

The local dataset is not committed to the repository. It was run from:

- `work/local_benchmark/chebi/chebi_3star_iupac_cases.tsv`
- 44,940 cases

## Current Full-Run Result

Latest local run:

- output directory: `work/local_benchmark/results_chebi_3star_iupac_after_methane_locants`
- total cases: 44,940
- exact passes: 503
- exact failures: 44,437
- false-success name mismatches: 225

Failure stages:

| Stage | Count |
|---|---:|
| bracket, charge, isotope, or stereochemistry outside scope | 21,217 |
| ring or aromatic chemistry outside scope | 11,735 |
| other unsupported scope | 9,386 |
| disconnected salts, mixtures, hydrates, or multi-component structures | 1,874 |
| successful render but exact-name mismatch | 225 |

## Improvement Since Initial Local Blitz

The initial local ChEBI run before this improvement pass produced:

- exact passes: 271
- false-success name mismatches: 662

After the engine fixes:

- exact passes: 503
- false-success name mismatches: 225

This is a net gain of 232 exact matches and a reduction of 437 successful-but-wrong names.

## Fixes Driven By This Benchmark

The benchmark exposed and helped verify fixes for:

- polyfunctional parent-chain selection;
- oxo, hydroxy, amino, acylamino, alkoxy, hydroxyimino, and methylidene prefix rendering;
- simple ester rendering;
- halogenated alkoxy prefixes;
- fail-closed handling for unsupported amide suffixes, imines, amidines, guanidines, and complex amine substituents;
- unsaturated parent endings such as `prop-1-ene` and `but-2-yne`;
- aldehyde suffix handling without duplicate `oxo` prefixes;
- multiplicative suffixes such as `triol`;
- locant elision for substituted methane.

## Benchmark Limitation

The ChEBI set is useful as broad telemetry, but it is not a valid exact pass-rate benchmark for the current engine.

Reasons:

- The current engine scope is explicitly small: single-component, acyclic organic structures with a limited element and functional-group set.
- Most ChEBI failures are outside that declared scope: rings, aromatics, stereochemistry, salts, charges, isotopes, sulfur/phosphorus chemistry, carbohydrates, peptides, natural products, and mixtures.
- ChEBI names are curated names, not guaranteed preferred IUPAC names.
- Exact string equality penalizes valid naming variants, retained names, functional-class names, optional locants, optional parentheses, and PIN/non-PIN differences.

Examples of benchmark-style mismatches that are not straightforward engine defects:

- `ethanoic acid` vs `acetic acid`
- `ethan-1-ol` vs `ethanol`
- substitutive ether names vs functional-class ether names
- optional parentheses around complex prefixes such as `difluoromethoxy`
- amino acid retained names such as `serine`, `valine`, or `aspartic acid`

## Conclusion

ChEBI should remain a stress and regression telemetry set, not the headline correctness oracle. The best next benchmark should be a scope-filtered, Blue Book/PIN-oriented gold set with accepted-name equivalence classes.
