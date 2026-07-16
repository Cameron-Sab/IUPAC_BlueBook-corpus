# Blue Book Semantic IR Conversion Guide

This guide governs the human/agent conversion pass over the lossless Blue Book
source corpus. The target is `data/normalized_rule_language.schema.json`.
Conversion is source compilation, not prose summarization and not engine work.

## Inputs

Each converter receives an immutable work packet containing:

- the source record and its PDF line ids;
- ordered HTML document nodes and stable node ids;
- linked tables, figures, images, examples, notes, and footnotes;
- incoming and outgoing source citations;
- every occurrence-level citation and any exact resolution overlay;
- applicable correction overlays;
- the six immutable source-artifact SHA-256 hashes.

PDF and HTML are parallel evidence. Do not silently choose one when they differ.
Record the alignment or correction that explains the difference.

## Atomic Coverage

Every document node is divided into the smallest source clauses that can have a
single role and normative force. A clause may be:

- an operative condition, effect, constraint, definition, mapping, criterion,
  procedure step, or scoped exception;
- a table row, table footnote, or rank-group declaration;
- an example input, accepted name, rejected name, or explanation;
- a figure or chemical-structure reference;
- a note, rationale, historical statement, scope statement, heading, or other
  explicitly nonoperative source material.

Every clause must have exactly one coverage disposition. Operative clauses point
to semantic unit or asset ids. Nonoperative clauses use a controlled reason code.
A record is not complete merely because it contains one semantic unit.

## Semantic Rules

1. Preserve logical scope. `A and (B or C)` must remain nested; it cannot become
   three independent predicates.
2. Preserve polarity. `must not`, `is not used`, and `except` never create a
   positive action by keyword overlap.
3. Preserve order. Procedure order, output order, table rank, and first-decisive
   preference order are different constructs.
4. Preserve ties. A decision criterion continues only on the equality condition
   stated by the source.
5. Preserve exception targets. An override identifies the exact unit, criterion,
   or statement that it changes and the mode of that change.
6. Preserve multiplicity. A section can produce any number of semantic units.
7. Preserve tables as data. Do not replace a table lookup or rank list with a
   prose predicate.
8. Preserve cross-references by relation. A citation is not automatically an
   invocation; history and examples commonly cite rules without executing them.
9. Preserve examples as nonoperative evidence. Example names never generate a
   general condition, action, or preference.
10. Preserve regime. PIN, preselected-name, general-nomenclature, retained-name,
    and class-specific scopes must be explicit guards or bindings.

## Symbols

Predicates, functions, transformations, entity types, and reason codes must be
declared in the shared symbol registry before use. Symbols describe reusable,
typed chemistry or nomenclature operations. They must not be sentence slugs,
generic fallbacks, or disguised copies of the source paragraph.

Literal expressions are for finite values, terms, locants, strings, numbers,
enumerated modes, and structured constants. A literal containing an entire rule
sentence is not a semantic conversion.

## Source Spans

Every semantic unit, criterion, override, table row, figure, and example cites
the exact source span that supports it. Quotes are complete for the cited clause
and are hashed. Do not cite a section-wide span for an effect found in one list
item when the narrower node or line span exists.

## Cross-Record Rules

- Store only immediate hierarchy parents.
- Resolve exact citations uniquely.
- Resolve ranges to their ordered member set.
- Represent deictic references such as "the preceding rule" with the resolved
  target and preserve the original wording in the source span.
- Use `invokes` only when application of the target rule is part of the behavior.
- Use `exception_to`, `overrides`, `supersedes`, and `corrects` for rule-changing
  relationships.
- Keep external recommendations as typed external targets with source URLs or
  bibliography ids; external is a resolved target kind, not missing work.

## Completion Gate

A chunk is acceptable only when:

- all assigned records are present exactly once;
- every source clause has exactly one coverage disposition;
- every operative clause reaches a schema-valid semantic object;
- every nonoperative clause has a registry-backed reason;
- list arity and criterion order equal the source;
- every referenced object exists and has a unique id;
- source quotes and hashes reproduce from the source artifacts;
- no source text was truncated;
- no forbidden marker or generic fallback appears;
- the chunk passes schema, provenance, registry, reference, and mutation checks.

Do not mark a difficult clause nonoperative merely because it is difficult to
formalize. Difficulty changes the converter's work, not the clause's force.

## Finalization

Write draft chunks under `data/bluebook_v3/semantic_chunks/`. Finalize each one
against its exact packet; the finalizer writes only after strict validation:

```powershell
python scripts\finalize_normalized_rule_chunk.py `
  data\bluebook_v3\semantic_chunks\P-1-part-001.json `
  --packet work\semantic_packets\P-1-part-001.json
```

When all 151 chunks pass, assemble the corpus with:

```powershell
python scripts\assemble_normalized_rule_corpus.py
```

Assembly is atomic and rejects missing packets, duplicate records, conflicting
symbols, dangling typed references, ambiguous exception precedence, altered
dependency projections, stale source hashes, or nonreproducible metrics/hashes.
