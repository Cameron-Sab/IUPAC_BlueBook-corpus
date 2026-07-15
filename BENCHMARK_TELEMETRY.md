# ChEBI Benchmark Telemetry

This report summarizes local testing against the ChEBI 253 three-star IUPAC-name case set.

The local dataset is not committed to the repository. It was run from:

- `work/local_benchmark/chebi/chebi_3star_iupac_cases.tsv`
- 44,940 cases

## Current Full-Run Result

Latest local run:

- output directory: `work/local_benchmark/results_chebi_3star_iupac_saturated_monocycles_v2`
- total cases: 44,940
- exact passes: 1,353
- exact failures: 43,587
- false-success name mismatches: 223

Failure stages:

| Stage | Count |
|---|---:|
| other unsupported scope | 19,691 |
| ring or aromatic chemistry outside scope | 16,129 |
| bracket, charge, isotope, or stereochemistry outside scope | 5,747 |
| disconnected salts, mixtures, hydrates, or multi-component structures | 1,797 |
| successful render but exact-name mismatch | 223 |

These stages record only the first blocker returned for each case. Their counts
are not directly comparable with the earlier handwritten-parser run: the RDKit
adapter now parses bracket atoms and full molecular graphs, so it can classify
rings and graph modifiers that previously stopped at the input syntax layer.

## Improvement Since Initial Local Blitz

The initial local ChEBI run before this improvement pass produced:

- exact passes: 271
- false-success name mismatches: 662

After the acyclic naming fixes:

- exact passes: 1,180
- false-success name mismatches: 183

After adopting the RDKit graph adapter:

- exact passes: 1,335
- false-success name mismatches: 221

After adding saturated monocyclic hydrocarbon parents:

- exact passes: 1,353
- false-success name mismatches: 223

This is a net gain of 1,082 exact matches from the initial run. The graph-adapter
checkpoint added 155 exact matches with zero regressions among the previous 1,180
passes. Its 38 additional successful renders that disagree with the ChEBI string
are predominantly retained-name or naming-variant cases such as `ethanal` versus
`acetaldehyde`, `methanamide` versus `formamide`, and `methanoic acid` versus
`formic acid`.

The saturated-monocycle checkpoint added 18 exact matches with zero regressions
among the previous 1,335 passes. Its two additional mismatches are systematic
names where ChEBI stores retained hydrocarbon names: `p-menthane` and `humulane`.

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
- locant elision for substituted methane;
- primary amide, nitrile, acid-halide, and multiple-ester suffixes;
- lower-priority amide and ester rendering as paired prefixes;
- functional-prefix participation in numbering and prefix citation-order tie breaking;
- Table 1.4 numerical terms beyond six substituents and ten carbon atoms;
- complete single-halogen substitution with locant elision;
- attachment-aware branched alkyl, alkoxy, and ester organyl names;
- `bis(...)` rendering for repeated complex ester organyl groups.
- canonical, input-order-independent graph parsing through RDKit;
- neutral bracket-atom parsing while charges, isotopes, radicals, rings, and
  stereochemistry remain explicitly fail-closed;
- molecular ring, aromaticity, charge, isotope, radical, and stereochemical
  metadata for later nomenclature phases.
- saturated monocyclic carbon parents, ring-versus-chain parent choice, complete
  ring-numbering enumeration, citation-order tie breaking, and perhalogen locant
  elision.

## Benchmark Limitation

The ChEBI set is useful as broad telemetry, but it is not a valid exact pass-rate benchmark for the current engine.

Reasons:

- The current engine scope is explicitly small: single-component, acyclic organic structures with a limited element and functional-group set.
- Most ChEBI failures are outside that declared scope: rings, aromatics, stereochemistry, salts, charges, isotopes, sulfur/phosphorus chemistry, carbohydrates, peptides, natural products, and mixtures.
- ChEBI names are curated names, not guaranteed preferred IUPAC names.
- Exact string equality penalizes valid naming variants, retained names, functional-class names, optional locants, optional parentheses, and PIN/non-PIN differences.
- The current runner assigns one failure stage per row even when a molecule has
  several independent blockers. A separate all-feature census is required for
  sound prioritization.

Examples of benchmark-style mismatches that are not straightforward engine defects:

- `ethanoic acid` vs `acetic acid`
- `ethan-1-ol` vs `ethanol`
- substitutive ether names vs functional-class ether names
- optional parentheses around complex prefixes such as `difluoromethoxy`
- amino acid retained names such as `serine`, `valine`, or `aspartic acid`

## Conclusion

ChEBI should remain a stress and regression telemetry set, not the headline correctness oracle. The best next benchmark should be a scope-filtered, Blue Book/PIN-oriented gold set with accepted-name equivalence classes.
