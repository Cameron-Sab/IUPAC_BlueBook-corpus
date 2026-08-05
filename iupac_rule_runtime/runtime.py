from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, MutableMapping, Sequence


class ExecutionError(ValueError):
    """Raised when a valid-looking bundle cannot be executed deterministically."""


class CapabilityError(ExecutionError):
    """Raised before execution when host-provided operations are incomplete."""

    def __init__(self, missing: Sequence[str], mismatched: Sequence[str] = ()) -> None:
        self.missing = tuple(sorted(missing))
        self.mismatched = tuple(sorted(mismatched))
        details = []
        if self.missing:
            details.append("missing=" + ",".join(self.missing))
        if self.mismatched:
            details.append("kind_mismatch=" + ",".join(self.mismatched))
        super().__init__("Unsatisfied capability contract: " + "; ".join(details))


@dataclass(frozen=True)
class RegisteredCapability:
    kind: str
    implementation: Callable[..., Any]


class CapabilityRegistry:
    """Host operations used by predicate, function, transform, and comparator nodes."""

    def __init__(self) -> None:
        self._items: dict[str, RegisteredCapability] = {}

    def register(
        self, symbol_id: str, kind: str, implementation: Callable[..., Any]
    ) -> "CapabilityRegistry":
        if kind not in {"predicate", "function", "transformation", "comparator"}:
            raise ValueError(f"Unsupported executable capability kind: {kind}")
        if symbol_id in self._items:
            raise ValueError(f"Capability already registered: {symbol_id}")
        self._items[symbol_id] = RegisteredCapability(kind, implementation)
        return self

    def resolve(self, symbol_id: str, expected_kind: str) -> Callable[..., Any]:
        item = self._items.get(symbol_id)
        if item is None:
            raise CapabilityError([symbol_id])
        if item.kind != expected_kind:
            raise CapabilityError([], [f"{symbol_id}:{item.kind}!={expected_kind}"])
        return item.implementation

    def audit(self, requirements: Sequence[Mapping[str, Any]]) -> None:
        missing: list[str] = []
        mismatched: list[str] = []
        for requirement in requirements:
            symbol_id = str(requirement["symbol_id"])
            expected_kind = str(requirement["kind"])
            item = self._items.get(symbol_id)
            if item is None:
                missing.append(symbol_id)
            elif item.kind != expected_kind:
                mismatched.append(f"{symbol_id}:{item.kind}!={expected_kind}")
        if missing or mismatched:
            raise CapabilityError(missing, mismatched)


@dataclass
class ExecutionResult:
    entrypoint: str
    values: dict[str, Any]
    emitted: list[Any] = field(default_factory=list)
    rendered: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    outcomes: dict[str, str] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _State:
    values: dict[str, Any]
    emitted: list[Any] = field(default_factory=list)
    rendered: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    outcomes: dict[str, str] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    call_stack: list[str] = field(default_factory=list)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def digest_without_field(value: Mapping[str, Any], field_name: str) -> str:
    payload = dict(value)
    payload.pop(field_name, None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest().upper()


class RuleRuntime:
    """Safe interpreter for the ordered-if-then-v1 execution model.

    The interpreter never evaluates source text. Every operation is either a
    built-in opcode or a declared capability resolved through the host registry.
    """

    def __init__(
        self,
        bundle: Mapping[str, Any],
        capabilities: CapabilityRegistry | None = None,
        *,
        verify_hash: bool = True,
    ) -> None:
        if bundle.get("format") != "iupac-bluebook-executable-rule-bundle":
            raise ExecutionError("Unsupported rule bundle format")
        if bundle.get("format_version") != "1.0.0":
            raise ExecutionError("Unsupported rule bundle version")
        if bundle.get("execution_model") != "ordered-if-then-v1":
            raise ExecutionError("Unsupported execution model")
        if verify_hash and bundle.get("bundle_sha256") != digest_without_field(
            bundle, "bundle_sha256"
        ):
            raise ExecutionError("Rule bundle SHA-256 does not reproduce")

        self.bundle = bundle
        self.capabilities = capabilities or CapabilityRegistry()
        self.programs = self._unique_index(bundle.get("programs", []), "unit_id")
        self.tables = self._unique_index(bundle.get("tables", []), "table_id")
        self.entrypoints = self._entrypoint_index(bundle.get("entrypoints", []))
        self.exceptions = self._exception_index(bundle.get("exceptions", []))
        contract = bundle.get("capability_contract", {})
        requirements = contract.get("required", []) if isinstance(contract, Mapping) else []
        self.capabilities.audit(requirements)

    @staticmethod
    def _unique_index(values: Any, id_field: str) -> dict[str, Mapping[str, Any]]:
        if not isinstance(values, list):
            raise ExecutionError(f"Bundle {id_field} collection must be an array")
        result: dict[str, Mapping[str, Any]] = {}
        for value in values:
            if not isinstance(value, Mapping) or not isinstance(value.get(id_field), str):
                raise ExecutionError(f"Bundle item has no {id_field}")
            object_id = str(value[id_field])
            if object_id in result:
                raise ExecutionError(f"Duplicate {id_field}: {object_id}")
            result[object_id] = value
        return result

    @staticmethod
    def _entrypoint_index(values: Any) -> dict[str, tuple[str, ...]]:
        if not isinstance(values, list):
            raise ExecutionError("Bundle entrypoints must be an array")
        result: dict[str, tuple[str, ...]] = {}
        for item in values:
            if not isinstance(item, Mapping):
                raise ExecutionError("Entrypoint must be an object")
            entrypoint_id = item.get("entrypoint_id")
            program_ids = item.get("program_ids")
            if not isinstance(entrypoint_id, str) or not isinstance(program_ids, list):
                raise ExecutionError("Entrypoint is malformed")
            if entrypoint_id in result:
                raise ExecutionError(f"Duplicate entrypoint: {entrypoint_id}")
            result[entrypoint_id] = tuple(str(value) for value in program_ids)
        return result

    @staticmethod
    def _exception_index(values: Any) -> dict[str, tuple[Mapping[str, Any], ...]]:
        if not isinstance(values, list):
            raise ExecutionError("Bundle exceptions must be an array")
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for item in values:
            if not isinstance(item, Mapping):
                raise ExecutionError("Exception must be an object")
            target = item.get("target")
            if not isinstance(target, Mapping) or target.get("kind") != "semantic_unit":
                raise ExecutionError(
                    f"Reference runtime does not support exception target "
                    f"{target.get('kind') if isinstance(target, Mapping) else target}:"
                    f"{target.get('id') if isinstance(target, Mapping) else ''}"
                )
            effect = item.get("effect")
            if not isinstance(effect, Mapping):
                raise ExecutionError("Exception has no effect object")
            mode = effect.get("mode")
            if mode == "change_precedence":
                raise ExecutionError(
                    "Reference runtime does not support change_precedence exceptions"
                )
            if mode == "add_guard" and not isinstance(effect.get("guard"), Mapping):
                raise ExecutionError("add_guard exception has no executable guard")
            if mode in {"replace", "redirect"}:
                key = "redirect" if mode == "redirect" else "replacement"
                destination = effect.get(key)
                if not isinstance(destination, Mapping) or destination.get("kind") != (
                    "semantic_unit"
                ):
                    raise ExecutionError(
                        f"Reference runtime requires a semantic_unit {key} for {mode}"
                    )
            grouped.setdefault(str(target.get("id")), []).append(item)
        return {key: tuple(items) for key, items in grouped.items()}

    def execute(
        self,
        entrypoint: str,
        inputs: Mapping[str, Any] | None = None,
        *,
        regime: str = "all",
    ) -> ExecutionResult:
        program_ids = self.entrypoints.get(entrypoint)
        if program_ids is None:
            program_ids = (entrypoint,) if entrypoint in self.programs else None
        if program_ids is None:
            raise ExecutionError(f"Unknown entrypoint: {entrypoint}")
        state = _State(values=dict(inputs or {}))
        state.values["regime"] = regime
        for program_id in program_ids:
            self._execute_program(program_id, state, regime=regime)
        state.values.pop("regime", None)
        return ExecutionResult(
            entrypoint=entrypoint,
            values=state.values,
            emitted=state.emitted,
            rendered=state.rendered,
            rejected=state.rejected,
            outcomes=state.outcomes,
            trace=state.trace,
        )

    def _execute_program(self, program_id: str, state: _State, *, regime: str) -> Any:
        if program_id in state.call_stack:
            chain = " -> ".join([*state.call_stack, program_id])
            raise ExecutionError(f"Recursive rule invocation: {chain}")
        program = self.programs.get(program_id)
        if program is None:
            nested = self.entrypoints.get(program_id)
            if nested is None:
                raise ExecutionError(f"Invoked program does not exist: {program_id}")
            value = None
            for nested_id in nested:
                value = self._execute_program(nested_id, state, regime=regime)
            nested_outcomes = [state.outcomes.get(nested_id) for nested_id in nested]
            state.outcomes[program_id] = (
                "applied" if any(outcome == "applied" for outcome in nested_outcomes) else "inapplicable"
            )
            return value

        state.call_stack.append(program_id)
        try:
            program = self._apply_exceptions(program, state, regime)
            if program is None:
                state.outcomes[program_id] = "suppressed"
                return None
            effective_id = str(program["unit_id"])
            scope = program.get("scope", {})
            regimes = scope.get("regimes", []) if isinstance(scope, Mapping) else []
            if "all" not in regimes and regime not in regimes:
                state.outcomes[program_id] = "out_of_scope"
                return None
            applies_to = scope.get("applies_to") if isinstance(scope, Mapping) else None
            if applies_to is not None and not bool(self._eval(applies_to, state.values, state)):
                state.outcomes[program_id] = "out_of_scope"
                return None

            kind = program.get("kind")
            state.trace.append({"event": "enter", "program_id": effective_id, "kind": kind})
            if kind == "rule":
                branch = "then" if bool(self._eval(program["when"], state.values, state)) else "else"
                self._statements(program.get(branch, []), state.values, state, regime)
                value: Any = None
            elif kind == "definition":
                value = self._eval(program["value"], state.values, state)
                outputs = program.get("outputs", [])
                if len(outputs) == 1:
                    state.values[str(outputs[0]["name"])] = value
            elif kind == "mapping":
                value = self.tables.get(str(program["table_id"]))
                if value is None:
                    raise ExecutionError(f"Mapping table does not exist: {program['table_id']}")
            elif kind == "procedure":
                self._statements(program.get("steps", []), state.values, state, regime)
                value = None
            elif kind == "constraint":
                passed = bool(self._eval(program["assertion"], state.values, state))
                if not passed:
                    self._statements(program.get("on_violation", []), state.values, state, regime)
                value = passed
            elif kind == "decision":
                value = self._execute_decision(program, state, regime)
                outputs = program.get("outputs", [])
                if len(outputs) == 1:
                    state.values[str(outputs[0]["name"])] = value
            else:
                raise ExecutionError(f"Unknown semantic unit kind: {kind}")
            state.outcomes[program_id] = "applied"
            state.trace.append({"event": "exit", "program_id": effective_id})
            return value
        finally:
            state.call_stack.pop()

    def _apply_exceptions(
        self, program: Mapping[str, Any], state: _State, regime: str
    ) -> Mapping[str, Any] | None:
        original_id = str(program["unit_id"])
        current = program
        for exception in self.exceptions.get(original_id, ()):
            if not bool(self._eval(exception["when"], state.values, state)):
                continue
            effect = exception["effect"]
            mode = effect["mode"]
            state.trace.append(
                {"event": "exception", "exception_id": exception["exception_id"], "mode": mode}
            )
            if mode == "suppress":
                return None
            if mode == "add_guard":
                guard = effect.get("guard")
                if guard is None or not bool(self._eval(guard, state.values, state)):
                    return None
                continue
            if mode in {"replace", "redirect"}:
                key = "redirect" if mode == "redirect" else "replacement"
                target = effect.get(key)
                if not isinstance(target, Mapping) or target.get("kind") != "semantic_unit":
                    raise ExecutionError(
                        f"Exception {exception['exception_id']} {mode} requires a semantic_unit {key}"
                    )
                replacement = self.programs.get(str(target.get("id")))
                if replacement is None:
                    raise ExecutionError(f"Exception target does not exist: {target.get('id')}")
                current = replacement
                continue
            raise ExecutionError(f"Unknown exception mode: {mode}")
        return current

    def _execute_decision(
        self, program: Mapping[str, Any], state: _State, regime: str
    ) -> Any:
        candidates = list(self._eval(program["candidates"], state.values, state))
        for stage in program.get("stages", []):
            scored: list[tuple[Any, Any]] = []
            skipped: list[Any] = []
            for candidate in candidates:
                local = dict(state.values)
                local["candidate"] = candidate
                if bool(self._eval(stage["guard"], local, state)):
                    scored.append((candidate, self._eval(stage["key"], local, state)))
                else:
                    skipped.append(candidate)
            if scored:
                winners = self._select(scored, stage["comparator"])
                candidates = [*winners, *skipped]
            state.trace.append(
                {
                    "event": "decision_stage",
                    "program_id": program["unit_id"],
                    "stage_id": stage["stage_id"],
                    "remaining": len(candidates),
                }
            )
            if len(candidates) <= 1:
                break
        if len(candidates) == 1:
            return candidates[0]
        terminal = program["terminal_tie"]
        mode = terminal["mode"]
        if mode == "retain_coequal":
            return candidates
        if mode == "reject_ambiguous":
            raise ExecutionError(f"Decision remained ambiguous: {program['unit_id']}")
        fallback = terminal.get("fallback_ref")
        if mode == "apply_fallback" and isinstance(fallback, Mapping):
            return self._execute_program(str(fallback["id"]), state, regime=regime)
        raise ExecutionError(f"Decision has invalid terminal tie behavior: {program['unit_id']}")

    def _select(self, scored: list[tuple[Any, Any]], comparator: Mapping[str, Any]) -> list[Any]:
        kind = comparator["kind"]
        direction = comparator["direction"]
        if kind == "custom" or direction == "symbol_defined":
            symbol = comparator.get("symbol")
            if not isinstance(symbol, str):
                raise ExecutionError("Custom comparator has no symbol")
            compare = self.capabilities.resolve(symbol, "comparator")
            best: list[tuple[Any, Any]] = []
            for item in scored:
                if not best:
                    best = [item]
                    continue
                relation = int(compare(item[1], best[0][1]))
                if relation < 0:
                    best = [item]
                elif relation == 0:
                    best.append(item)
            return [candidate for candidate, _ in best]
        if kind == "ordered_table":
            table_id = comparator.get("table_id")
            if not isinstance(table_id, str):
                raise ExecutionError("Ordered-table comparator has no table_id")
            order = self._table_order(table_id)
            keys = [(candidate, order.get(self._hashable(key))) for candidate, key in scored]
            if any(rank is None for _, rank in keys):
                raise ExecutionError(f"Comparator key absent from ordered table: {table_id}")
            scored = [(candidate, rank) for candidate, rank in keys]
        elif kind == "set_order":
            scored = [(candidate, tuple(sorted(key))) for candidate, key in scored]
        elif kind not in {"numeric", "lexicographic"}:
            raise ExecutionError(f"Unsupported comparator kind: {kind}")
        values = [key for _, key in scored]
        if direction in {"minimum", "source_order"}:
            best_key = min(values)
        elif direction == "maximum":
            best_key = max(values)
        else:
            raise ExecutionError(f"Unsupported comparator direction: {direction}")
        return [candidate for candidate, key in scored if key == best_key]

    def _statements(
        self,
        statements: Any,
        env: MutableMapping[str, Any],
        state: _State,
        regime: str,
    ) -> None:
        if not isinstance(statements, list):
            raise ExecutionError("Statement block must be an array")
        for statement in statements:
            self._statement(statement, env, state, regime)

    def _statement(
        self,
        statement: Mapping[str, Any],
        env: MutableMapping[str, Any],
        state: _State,
        regime: str,
    ) -> None:
        op = statement["op"]
        if op == "sequence":
            self._statements(statement["steps"], env, state, regime)
        elif op == "branch":
            key = "then" if bool(self._eval(statement["when"], env, state)) else "else"
            self._statements(statement[key], env, state, regime)
        elif op == "assign":
            value = self._eval(statement["value"], env, state)
            env[str(statement["target"])] = value
            state.values[str(statement["target"])] = value
        elif op == "transform":
            symbol = str(statement["transformation"])
            transform = self.capabilities.resolve(symbol, "transformation")
            args = [self._eval(arg, env, state) for arg in statement.get("args", [])]
            value = transform(env.get(str(statement["target"])), *args)
            env[str(statement["target"])] = value
            state.values[str(statement["target"])] = value
        elif op == "render":
            state.rendered.append(
                {
                    "component": statement["component"],
                    "position": statement["position"],
                    "value": self._eval(statement["value"], env, state),
                }
            )
        elif op == "reject":
            state.rejected.append(
                {"target": statement["target"], "reason_code": statement["reason_code"]}
            )
        elif op == "invoke":
            bindings = {
                name: self._eval(value, env, state)
                for name, value in statement.get("bindings", {}).items()
            }
            previous = {name: env.get(name, _MISSING) for name in bindings}
            env.update(bindings)
            state.values.update(bindings)
            try:
                self._execute_program(str(statement["rule_id"]), state, regime=regime)
            finally:
                for name, value in previous.items():
                    if value is _MISSING:
                        env.pop(name, None)
                        state.values.pop(name, None)
                    else:
                        env[name] = value
                        state.values[name] = value
        elif op == "iterate":
            for item in self._eval(statement["in"], env, state):
                env[str(statement["bind"])] = item
                self._statements(statement["body"], env, state, regime)
                if bool(self._eval(statement["stop_when"], env, state)):
                    break
            env.pop(str(statement["bind"]), None)
        elif op == "emit":
            state.emitted.append(self._eval(statement["value"], env, state))
        elif op == "assert":
            if not bool(self._eval(statement["assertion"], env, state)):
                raise ExecutionError(f"Assertion failed: {statement['reason_code']}")
        else:
            raise ExecutionError(f"Unknown statement opcode: {op}")

    def _eval(self, expression: Mapping[str, Any], env: Mapping[str, Any], state: _State) -> Any:
        op = expression["op"]
        if op == "literal":
            return expression["value"]
        if op == "var":
            name = str(expression["name"])
            if name not in env:
                raise ExecutionError(f"Undefined variable: {name}")
            return env[name]
        if op == "get":
            return self._get_path(self._eval(expression["from"], env, state), expression["path"])
        if op in {"predicate", "function"}:
            symbol = str(expression["symbol"])
            implementation = self.capabilities.resolve(symbol, op)
            return implementation(*(self._eval(arg, env, state) for arg in expression["args"]))
        if op == "all":
            return all(bool(self._eval(arg, env, state)) for arg in expression["args"])
        if op == "any":
            return any(bool(self._eval(arg, env, state)) for arg in expression["args"])
        if op == "not":
            return not bool(self._eval(expression["arg"], env, state))
        if op in {"exists", "forall"}:
            values = self._eval(expression["in"], env, state)
            results = []
            for value in values:
                local = dict(env)
                local[str(expression["bind"])] = value
                results.append(bool(self._eval(expression["where"], local, state)))
                if op == "exists" and results[-1]:
                    return True
                if op == "forall" and not results[-1]:
                    return False
            return False if op == "exists" else True
        if op == "compare":
            left = self._eval(expression["left"], env, state)
            right = self._eval(expression["right"], env, state)
            return self._compare(str(expression["relation"]), left, right)
        if op == "table_lookup":
            key = self._eval(expression["key"], env, state)
            return self._table_lookup(str(expression["table_id"]), key, str(expression["column_id"]))
        if op == "rule_outcome":
            return state.outcomes.get(str(expression["rule_id"])) == expression["outcome"]
        raise ExecutionError(f"Unknown expression opcode: {op}")

    @staticmethod
    def _get_path(value: Any, path: str) -> Any:
        current = value
        for part in str(path).split("."):
            if isinstance(current, Mapping):
                if part not in current:
                    raise ExecutionError(f"Missing property in get expression: {path}")
                current = current[part]
            elif isinstance(current, (list, tuple)) and part.isdigit():
                current = current[int(part)]
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                raise ExecutionError(f"Missing property in get expression: {path}")
        return current

    @staticmethod
    def _compare(relation: str, left: Any, right: Any) -> bool:
        if relation == "eq":
            return left == right
        if relation == "ne":
            return left != right
        if relation == "lt":
            return left < right
        if relation == "le":
            return left <= right
        if relation == "gt":
            return left > right
        if relation == "ge":
            return left >= right
        if relation == "contains":
            return right in left
        if relation == "member_of":
            return left in right
        if relation == "same_set":
            return set(left) == set(right)
        raise ExecutionError(f"Unknown comparison relation: {relation}")

    def _table_lookup(self, table_id: str, key: Any, column_id: str) -> Any:
        table = self.tables.get(table_id)
        if table is None:
            raise ExecutionError(f"Unknown table: {table_id}")
        contract = table["contract"]
        key_columns = list(contract["key_column_ids"])
        if len(key_columns) == 1:
            key_values = {key_columns[0]: key}
        elif isinstance(key, Mapping):
            key_values = {column: key[column] for column in key_columns}
        elif isinstance(key, (list, tuple)) and len(key) == len(key_columns):
            key_values = dict(zip(key_columns, key))
        else:
            raise ExecutionError(f"Composite key for {table_id} must be a mapping or sequence")
        matches = []
        for row in table["rows"]:
            cells = {cell["column_id"]: cell["value"] for cell in row["cells"]}
            if all(cells.get(column) == value for column, value in key_values.items()):
                if column_id not in cells:
                    raise ExecutionError(f"Column {column_id} absent from matching row in {table_id}")
                matches.append(cells[column_id])
        cardinality = contract["cardinality"]
        if cardinality in {"one_to_one", "many_to_one"}:
            if len(matches) != 1:
                raise ExecutionError(f"Table {table_id} expected one result; found {len(matches)}")
            return matches[0]
        return matches

    def _table_order(self, table_id: str) -> dict[Any, int]:
        table = self.tables.get(table_id)
        if table is None:
            raise ExecutionError(f"Unknown ordered table: {table_id}")
        key_columns = table["contract"]["key_column_ids"]
        if len(key_columns) != 1:
            raise ExecutionError("Ordered-table comparator requires one key column")
        result: dict[Any, int] = {}
        for row in table["rows"]:
            cells = {cell["column_id"]: cell["value"] for cell in row["cells"]}
            rank = row["rank_group"] if row["rank_group"] is not None else row["ordinal"]
            result[self._hashable(cells[key_columns[0]])] = rank
        return result

    @staticmethod
    def _hashable(value: Any) -> Any:
        if isinstance(value, list):
            return tuple(RuleRuntime._hashable(item) for item in value)
        if isinstance(value, Mapping):
            return tuple(sorted((key, RuleRuntime._hashable(item)) for key, item in value.items()))
        return value


_MISSING = object()
