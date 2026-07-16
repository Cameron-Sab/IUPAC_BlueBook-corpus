# Normalized Blue Book Rule Language

## Purpose

The normalized corpus is a compiler intermediate representation, not a keyword
index or prose summary. It must preserve normative force, control flow,
ordering, scope, exceptions, tables, examples, corrections, and cross-record
dependencies closely enough for a later engine to execute without rereading the
book.

The JSON Schema authority is `data/normalized_rule_language.schema.json`.

## Immutable Source Snapshot

Every chunk and final corpus binds these exact artifacts:

- PDF-aligned source corpus;
- page and line corpus;
- lossless HTML document nodes;
- correction overlays;
- atomic clause inventory;
- occurrence-level references;
- explicit exceptional reference resolutions.

Six artifact SHA-256 values flow from the work-packet manifest through each
packet and chunk into the final `source_snapshot`. `effective_through` records
the latest official correction represented, not the retrieval date.

## Clause Coverage

The clause inventory is the accounting boundary. Every source unit receives
exactly one `clause_disposition`:

- `compiled` points to the typed objects implementing an operative clause;
- `nonoperative` assigns a controlled informative reason;
- `superseded` points to the correction that replaced or deleted the source.

Allowed roles distinguish conditions, effects, constraints, permissions,
prohibitions, preference criteria, tie continuation, procedure steps,
mappings, exceptions, cross-references, tables, figures, examples, notes,
rationale, history, corrections, and source metadata.

## Semantic Units

The six unit kinds are:

- `rule`: guarded `when`, `then`, and `else` behavior;
- `decision`: ordered stages over a candidate set with an explicit terminal-tie
  policy;
- `definition`: a term, entity type, and defining expression;
- `mapping`: a typed table contract;
- `procedure`: ordered statements;
- `constraint`: an assertion plus explicit violation behavior.

Each unit declares force, exact supporting clause ids, scope, inputs, and
outputs. Force is one of required, permitted, prohibited, preference, or
definition.

## Expressions And Statements

Expressions are recursive trees supporting literals, variables, property
access, registry-backed predicates and functions, Boolean composition,
quantification, comparison, table lookup, and prior-rule outcomes.

Statements support sequence, branch, assignment, transformation, rendering,
rejection, rule invocation, iteration, emission, and assertion. Every nested
expression, statement, and decision stage has a stable id and supporting clause
ids, so it can be referenced and audited independently.

## Decisions And Exceptions

A decision stage states its applicability guard, comparison key, comparator,
direction, and tie behavior. Source order is data. A later criterion cannot be
evaluated before an earlier criterion ties.

Exceptions are separate typed objects with a guard, exact target, effect, and
deterministic precedence pair. Their effects suppress, replace, guard, or
redirect a specific semantic object. The final assembler rejects ambiguous or
reordered precedence.

## Tables, Figures, And Examples

Tables contain typed columns, rows, cells, footnotes, and a contract describing
lookup, rank, allowlist, denylist, construction, or other semantics. A citation
to a table is not compiled until the table data and lookup relationship exist.

Figures preserve assets and captions. Examples preserve illustrative evidence
and demonstrated object references; they do not create general rules merely by
appearing near normative prose.

## References And Graphs

References distinguish hierarchy, citation, invocation, exception, override,
supersession, correction, demonstration, definition, table/figure use,
continuation, and constraint. Targets are typed object references and resolve
as exact, range, deictic, or intentional external references.

The raw occurrence graph remains a separate source artifact. It retains all
4,023 source citations, including raw targets. Three exact resolution overlay
records project two source typos to the active rule `P-66.1.2` and preserve the
deleted `P-65.7.8` target as a historical-rule tombstone. No generic resolution
fallback exists.

## Symbols

Reusable entity types, predicates, functions, transformations, comparators, and
reason codes are declared in a flat symbol registry. Each symbol has typed
arguments, a return type, and source grounding. Sentence-shaped pseudo-symbols
and ungrounded implementation placeholders are invalid.

## Completion Gate

The semantic conversion is complete only when:

1. all 2,554 source records occur exactly once in source order;
2. all 32,408 clauses have exactly one valid disposition;
3. operative clauses reach typed semantic objects;
4. all ids, member links, and typed references resolve uniquely;
5. decision and exception order reproduce deterministically;
6. dependency edges reproduce from references and exceptions;
7. all six source hashes, metrics, and corpus hashes reproduce;
8. the final corpus passes the Draft 2020-12 schema and mutation tests;
9. no TODO, review marker, placeholder, unresolved state, or generic fallback
   remains.

Until all nine conditions hold, the final rule corpus is not emitted as a
completed conversion.
