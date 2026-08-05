# Executable Rule Bundle

The executable rule bundle is the engine-facing form of the normalized Blue
Book semantic IR. It packages the rule dependency graph, ordered `if`/`then`/
`else` programs, decision stages, exceptions, tables, type names, entrypoints,
and host-operation requirements into one deterministic JSON document.

The bundle does not turn chemistry perception into prose or dynamic code.
Predicates such as `structure.has_characteristic_group` remain typed capability
imports. A host engine registers an implementation for each imported symbol;
bundle loading fails with the complete missing or mismatched list before any
rule executes.

## Contract

The format is `iupac-bluebook-executable-rule-bundle` version `1.0.0`, using
the `ordered-if-then-v1` execution model. Its schema is
`data/executable_rule_bundle.schema.json`. Program objects reuse the normative
types in `data/normalized_rule_language.schema.json`.

Important top-level fields are:

| Field | Meaning |
|---|---|
| `complete` | True only when compiled from the assembled complete semantic corpus |
| `sources` | Content-addressed normalized corpus or chunk inputs |
| `type_registry` | All input, output, symbol, entity, and table value types |
| `capability_contract.required` | Exact host functions required by reachable program syntax |
| `capability_contract.external_rule_dependencies` | Rule calls absent from an explicitly partial bundle |
| `entrypoints` | Source rule IDs and semantic-unit IDs mapped to ordered programs |
| `execution_order` | Stable source order for every semantic program |
| `programs` | Typed rules, decisions, definitions, mappings, procedures, and constraints |
| `exceptions` | Specificity-then-source-order exception programs |
| `tables` | Typed lookup and ordering tables |
| `dependency_edges` | The normalized cross-reference dependency graph |
| `bundle_sha256` | Canonical SHA-256 over every other bundle field |

No field contains Python, JavaScript, or another language to evaluate. The
runtime recognizes a closed opcode vocabulary and resolves only declared
capability symbols through a registry.

## Compile

Compile the final corpus after all 151 semantic deltas have passed and the
normalized corpus has been assembled:

```powershell
python scripts\compile_executable_rule_bundle.py `
  data\bluebook_v3\bluebook_v3_rule_ir.json `
  --output dist\bluebook_v3_executable_rules.json
```

Partial bundles are useful for converter conformance and adapter development,
but require an explicit flag and are permanently marked `complete: false`:

```powershell
python scripts\compile_executable_rule_bundle.py `
  data\bluebook_v3\semantic_chunks\P-40-part-001.json `
  --allow-partial `
  --output work\P-40-executable.json
```

The compiler rejects stale source hashes, colliding object identifiers,
undeclared or incorrectly typed symbols, missing tables, and unresolved rule
calls in a complete corpus. It produces canonical JSON and a reproducible
bundle hash.

## Host Integration

The included Python runtime is a reference implementation of the ABI, not a
replacement for a molecular graph toolkit. An engine supplies chemistry
operations and then executes either a source rule or semantic unit entrypoint:

```python
import json

from iupac_rule_runtime import CapabilityRegistry, RuleRuntime

bundle = json.load(open("dist/bluebook_v3_executable_rules.json", encoding="utf-8"))

capabilities = (
    CapabilityRegistry()
    .register("structure.has_characteristic_group", "predicate", has_group)
    .register("name.apply_prefix", "transformation", apply_prefix)
)

runtime = RuleRuntime(bundle, capabilities)
result = runtime.execute("P-44.1", {"structure": molecular_graph})
```

`RuleRuntime(...)` verifies the bundle hash and audits the complete capability
contract. `execute(...)` returns final values, emitted values, rendered name
components, rejected candidates, rule outcomes, and a deterministic trace.
The reference runtime currently executes unit-targeted exceptions and rejects
statement-targeted, decision-stage-targeted, and `change_precedence` exceptions
at load time. They remain preserved in the bundle for engines implementing the
full exact-target model; they are never silently ignored.

Host adapters can be implemented in any language by following the schema and
these semantics. The Python interpreter is deliberately small and contains no
SMILES parser, aromaticity model, or nomenclature shortcuts; those belong in
typed host capabilities or normalized rules.

## Execution Semantics

- A rule evaluates its scope, then its `when`; exactly one of `then` or `else`
  runs.
- A decision evaluates stages in ordinal order and retains tied candidates for
  the next stage.
- A definition evaluates `value`; a single declared output receives it.
- A mapping returns its typed table.
- A procedure runs its ordered statements.
- A constraint runs `on_violation` only when its assertion is false.
- Exceptions are tested in normalized precedence order before their target.
- `invoke` dispatches a source rule entrypoint and rejects recursive call loops.
- `table_lookup` enforces the table's key and cardinality contract.
- Unknown opcodes, missing variables, missing rows, ambiguity configured for
  rejection, assertion failures, and invalid exception targets are hard errors.

The normalized conversion remains the authority. A bundle can only execute
rules that have actually been semantically converted; the compiler does not
invent missing Blue Book meaning.
