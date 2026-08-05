# Token-Efficient Semantic Authoring

The semantic authoring layer is the compact input format used to finish the
Blue Book conversion without asking a model or human to reproduce mechanical
JSON. It does not replace the normalized rule language or relax its validator.
It expands deterministically into the existing semantic delta, which must then
pass the unchanged strict compiler.

## Pipeline

For one task:

```powershell
python scripts\scaffold_semantic_authoring.py `
  work\compact_semantic_tasks\P-20-part-001.json `
  --output work\semantic_authoring\P-20-part-001.json

python scripts\render_semantic_authoring_task.py `
  work\compact_semantic_tasks\P-20-part-001.json `
  --output work\semantic_authoring_views\P-20-part-001.jsonl

python scripts\compile_semantic_authoring.py `
  work\semantic_authoring\P-20-part-001.json
```

The skeleton has one ordered `clauses` slot for every task clause. `null` means
the mechanical scaffold has already locked that decision. `[]` means semantic
work is still required and makes compilation fail. A completed authoring file
replaces every unresolved `[]` with a clause decision.

The sparse view includes only unresolved `U` clause rows, unresolved `X`
citations, correction `K` rows, and rule-boundary `R` rows. Its task hash binds
the response to the exact source snapshot.

Source image assets are not semantic authoring work. A deterministic asset
scaffold, bound to the exact clause-inventory file hash, turns each image clause
into a typed `figure` with its source URL and provenance clause. New authoring
skeletons set `mechanical_assets: true`; their corresponding clause slots stay
`null`, and the strict compiler generates the figure dispositions locally.
Existing authoring files without that flag retain their original behavior.
Generated IDs are source-derived, for example clause `P-76:clause:0005` becomes
`figure.asset.p_76:clause:0005`, so authored examples can reference the figure
without reproducing its URL payload.

`heading_text` describes the source HTML node, not the clause's semantic force.
Only bare rule identifiers and clearly all-uppercase section labels are skipped
mechanically. Mixed-case heading clauses must receive an explicit authoring
decision because Blue Book headings frequently contain operative rule prose.

Rebuild or verify the asset scaffold with:

```powershell
python scripts\build_semantic_asset_scaffold.py
python scripts\build_semantic_asset_scaffold.py --check
```

For bounded production waves:

```powershell
python scripts\plan_semantic_authoring_waves.py `
  --max-view-bytes 50000 `
  --max-unresolved-clauses 100 `
  --max-tasks 4 `
  --lanes 4 `
  --output work\semantic_authoring_plan.json
```

The planner strictly audits existing deltas, excludes completed tasks, measures
the sparse evidence for every remaining task, and emits deterministic batches
from smallest to largest. `--skip-task` reserves work already assigned to an
active worker. Both evidence bytes and unresolved clause counts bound normal
batches. Oversized individual tasks remain isolated rather than being silently
split.

After strict compilation, run the semantic quality audit:

```powershell
python scripts\audit_semantic_authoring_quality.py `
  data\bluebook_v3\semantic_authoring\P-53-part-001.json `
  --require-clean
```

This second gate flags clauses marked nonoperative despite source semantic cues,
normative nomenclature language, or close rule context. It is deliberately
conservative: findings require review and are not automatically rewritten.

## Clause Decisions

Clause slots use compact arrays:

```json
["definition", "normative", "compile"]
["note", "informative", "skip", "explanatory_note"]
["correction_event", "correction", "supersede", ["application.id"], [42]]
```

Clause references inside units are one-based indexes into the task's complete
ordered clause list. Compiled targets are not authored: the compiler walks the
expanded AST and generates exact typed target references for each clause.

## Expressions

Expressions are prefix arrays. Nested expression IDs and repeated clause IDs
are generated from their deterministic AST paths.

| Form | Expanded operation |
|---|---|
| `["lit", value]` | literal |
| `["var", name]` | variable |
| `["get", expression, path]` | property access |
| `["pred", symbol, ...args]` | predicate |
| `["call", symbol, ...args]` | function |
| `["all", ...args]`, `["any", ...args]` | Boolean composition |
| `["not", expression]` | negation |
| `["exists", bind, collection, test]` | existential quantifier |
| `["forall", bind, collection, test]` | universal quantifier |
| `["cmp", relation, left, right]` | comparison |
| `["lookup", table, key, column]` | typed table lookup |
| `["outcome", rule, outcome]` | prior-rule outcome |

Primitive JSON values may be used where a literal expression is expected.

## Statements

| Form | Expanded operation |
|---|---|
| `["seq", ...steps]` | sequence |
| `["if", test, then, else]` | branch |
| `["set", target, value]` | assignment |
| `["xform", target, symbol, ...args]` | transformation |
| `["render", component, position, value]` | rendered name component |
| `["reject", target, reason]` | rejection |
| `["invoke", rule, bindings]` | rule invocation |
| `["each", bind, collection, body, stop]` | iteration |
| `["emit", value]` | emission |
| `["assert", test, reason]` | assertion |

Reason-code symbol declarations are generated and grounded automatically from
the statements that emit them.

## Unit Example

```json
{
  "id": "acyclic_parent_hydride",
  "k": "definition",
  "c": [4],
  "in": [["parent", "ParentHydride"]],
  "out": [["result", "boolean"]],
  "term": "acyclic parent hydride",
  "entity": "ParentHydride",
  "value": [
    "all",
    ["pred", "parent.is_acyclic", ["var", "parent"]],
    ["pred", "parent.is_saturated", ["var", "parent"]],
    ["pred", "parent.is_unbranched", ["var", "parent"]]
  ]
}
```

Unit keys are `id`, `k`, `f`, `c`, `scope`, `in`, and `out`, followed by the
kind-specific fields:

- rule: `if`, `then`, `else`;
- decision: `candidates`, ordered `stages`, and `tie`;
- definition: `term`, `entity`, and `value`;
- mapping: `table`;
- procedure: `steps`;
- constraint: `assert` and `violation`.

Tables use compact `cols`, row value arrays, and a typed `contract`. Examples
use `ok`, `bad`, and `shows`. Exceptions use compact expressions and object
references. Figures and correction applications use short field names while
retaining source hashes and exact targets.

## Fail-Closed Guarantees

The authoring compiler rejects:

- a missing, extra, or unresolved clause slot;
- attempts to override mechanically proven slots or citations;
- a compiled clause with no semantic object;
- unknown prefix operations or malformed arity;
- missing citation occurrences or changed source order;
- stale task hashes, duplicate generated IDs, undeclared symbols, dangling
  object references, invalid tables, forbidden review markers, and every other
  condition enforced by the normalized delta and chunk validators.

Current fleet measurements:

- sparse evidence is 4,083,099 bytes versus 6,884,500 bytes for the original
  compact views, a 40.69% input reduction;
- automatic figures remove another 1,349,391 bytes, or 24.84%, from the first
  sparse-authoring design;
- 10,587 clause decisions and 2,918 citations are filled mechanically;
- 21,821 clauses remain for semantic judgment;
- compact authoring is 71.6% smaller than its expanded delta in the strict
  representative round-trip test.
